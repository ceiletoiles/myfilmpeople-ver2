from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..models import CompanyFollow, PersonFollow
from .diary import _diary_sync_start_background
from ._shared import _countdown_text, _normalize_role, _parse_iso_date, _role_category


def _ensure_movie_entry(
	collection: dict[int, dict],
	movie_id: int,
	title: str,
	release_date: str | None,
	release_dt,
	poster_path: str,
) -> dict:
	entry = collection.get(movie_id)
	if entry is None:
		entry = {
			"movie_id": movie_id,
			"title": title,
			"release_date": release_date,
			"release_dt": release_dt,
			"poster_path": poster_path,
			"credits": [],
			"studio_names": set(),
			"_credit_set": set(),
			"_credit_details_by_person": {},
		}
		collection[movie_id] = entry
	else:
		if not entry.get("title"):
			entry["title"] = title
		if not entry.get("release_date"):
			entry["release_date"] = release_date
		if entry.get("release_dt") is None or release_dt < entry["release_dt"]:
			entry["release_dt"] = release_dt
		if not entry.get("poster_path") and poster_path:
			entry["poster_path"] = poster_path
	if "_credit_details_by_person" not in entry:
		entry["_credit_details_by_person"] = {}
	return entry


def _format_credit_detail(*, follow_role: str, credit_item: dict) -> str:
	role_n = _normalize_role(follow_role)
	if role_n == "actor":
		character = (credit_item.get("character") or "").strip()
		return character if character else "Actor"
	if role_n == "director":
		return "Director"
	if role_n == "crew":
		job = (credit_item.get("job") or "").strip()
		return job if job else "Crew"
	return follow_role.strip() if follow_role.strip() else "Credit"


def _split_credit_jobs(job: str) -> list[str]:
	tokens: list[str] = []
	for raw in (job or "").replace("/", ",").replace(";", ",").replace("|", ",").split(","):
		token = raw.strip().lower()
		if token and token not in tokens:
			tokens.append(token)
	return tokens


def _append_credit(entry: dict, *, person_name: str, credit_detail: str) -> None:
	credit_set = entry.setdefault("_credit_set", set())
	credit_details_by_person = entry.setdefault("_credit_details_by_person", {})

	if person_name not in credit_details_by_person:
		credit_details_by_person[person_name] = []

	key = f"{person_name}|{credit_detail}"
	if key in credit_set:
		return

	credit_set.add(key)
	credit_details_by_person[person_name].append(credit_detail)


def _finalize_credits(entry: dict) -> None:
	credit_details_by_person = entry.pop("_credit_details_by_person", {}) or {}
	credits: list[str] = []
	for person_name, details in credit_details_by_person.items():
		if details:
			credits.append(f"{person_name} - {', '.join(details)}")
		else:
			credits.append(person_name)
	entry["credits"] = credits
	entry.pop("_credit_set", None)


def _crew_job_matches_follow_role(job: str, follow_role: str) -> bool:
	job_n = _normalize_role(job)
	job_tokens = _split_credit_jobs(job)
	role_n = _normalize_role(follow_role)
	if not job_n or not role_n:
		return False
	if job_n == role_n:
		return True
	if role_n in job_tokens:
		return True
	return role_n in job_n or job_n in role_n


@login_required
def upcoming(request: HttpRequest) -> HttpResponse:
	_diary_sync_start_background(request.user)
	today = timezone.now().date()

	follows = (
		PersonFollow.objects.select_related("person")
		.defer("person__tmdb_raw")
		.filter(user=request.user)
		.order_by("role", "person__name")
	)

	company_follows = (
		CompanyFollow.objects.select_related("company")
		.filter(user=request.user)
		.order_by("company__name")
	)

	upcoming_by_role: dict[str, list[dict]] = {
		"director": [],
		"actor": [],
		"crew": [],
	}
	upcoming_by_role_map: dict[str, dict[int, dict]] = {
		"director": {},
		"actor": {},
		"crew": {},
	}
	all_movies_by_id: dict[int, dict] = {}

	for follow in follows:
		# Use person from the select_related query (already in DB).
		person = follow.person
		credits = person.tmdb_credits_raw or {}
		follow_role = (follow.role or "").strip()
		follow_role_n = _normalize_role(follow_role)
		category = _role_category(follow_role)

		if follow_role_n == "actor":
			credit_items = credits.get("cast", []) or []
		elif follow_role_n == "director":
			credit_items = [
				c
				for c in (credits.get("crew", []) or [])
				if "director" in _split_credit_jobs(str(c.get("job") or ""))
			]
		elif follow_role_n == "crew":
			# Backward compatible: generic "Crew" follows include all crew jobs.
			credit_items = credits.get("crew", []) or []
		else:
			credit_items = [
				c
				for c in (credits.get("crew", []) or [])
				if _crew_job_matches_follow_role(c.get("job") or "", follow_role)
			]

		for item in credit_items:
			if item.get("media_type") not in (None, "movie"):
				continue

			# Skip "Self" credits (including variations like "Self (archive footage)")
			if follow_role_n == "actor":
				char = (item.get("character") or "").strip().lower()
				if "self" in char:
					continue
			else:
				job = (item.get("job") or "").strip().lower()
				if "self" in job:
					continue

			release_date_str = item.get("release_date")
			release_dt = _parse_iso_date(release_date_str)
			if release_dt is None or release_dt <= today:
				continue

			mid = item.get("id")
			if not isinstance(mid, int):
				continue

			title = item.get("title") or item.get("name") or str(mid)
			poster_path = item.get("poster_path") or ""
			credit_detail = _format_credit_detail(
				follow_role=follow_role,
				credit_item=item,
			)

			role_entry = _ensure_movie_entry(
				upcoming_by_role_map[category],
				mid,
				title,
				release_date_str,
				release_dt,
				poster_path,
			)
			_append_credit(role_entry, person_name=person.name, credit_detail=credit_detail)

			all_entry = _ensure_movie_entry(
				all_movies_by_id,
				mid,
				title,
				release_date_str,
				release_dt,
				poster_path,
			)
			_append_credit(all_entry, person_name=person.name, credit_detail=credit_detail)

	for role, items in upcoming_by_role_map.items():
		role_items = list(items.values())
		for item in role_items:
			_finalize_credits(item)
		role_items.sort(key=lambda r: r["release_dt"])
		upcoming_by_role[role] = role_items

	studio_summaries: list[dict] = []
	studio_cards: list[dict] = []

	for follow in company_follows:
		company = follow.company
		company_id = company.tmdb_id

		tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		pages = tmdb_raw.get("discover_movies_pages")
		if not isinstance(pages, dict):
			pages = {}

		studio_movie_map: dict[int, dict] = {}
		seen_movie_ids: set[int] = set()
		for payload in pages.values():
			if not isinstance(payload, dict):
				continue
			for m in [movie for movie in (payload.get("results") or []) if isinstance(movie, dict)]:
				if not isinstance(m, dict):
					continue
				mid = m.get("id")
				if not isinstance(mid, int):
					continue
				if mid in seen_movie_ids:
					continue
				seen_movie_ids.add(mid)

				release_date_str = str(m.get("release_date") or "").strip()
				release_dt = _parse_iso_date(release_date_str)
				if release_dt is None or release_dt <= today:
					continue

				title = m.get("title") or m.get("name") or str(mid)
				poster_path = m.get("poster_path") or ""
				studio_entry = _ensure_movie_entry(
					studio_movie_map,
					mid,
					title,
					release_date_str,
					release_dt,
					poster_path,
				)
				studio_entry["studio_names"].add(company.name)

		studio_items = list(studio_movie_map.values())
		for item in studio_items:
			item["studio_names"] = sorted(item["studio_names"], key=str.casefold)
			_finalize_credits(item)
		studio_items.sort(key=lambda r: r["release_dt"])
		if not studio_items:
			continue
		studio_summaries.append(
			{
				"tmdb_id": company_id,
				"name": company.name,
				"logo_path": company.logo_path,
				"upcoming_count": len(studio_items),
			}
		)
		studio_cards.append(
			{
				"tmdb_id": company_id,
				"name": company.name,
				"logo_path": company.logo_path,
				"upcoming_count": len(studio_items),
				"items": studio_items,
			}
		)

	studio_summaries.sort(key=lambda s: (-int(s.get("upcoming_count") or 0), (s.get("name") or "").casefold()))
	studio_cards.sort(key=lambda s: (-int(s.get("upcoming_count") or 0), (s.get("name") or "").casefold()))

	all_items = list(all_movies_by_id.values())
	for item in all_items:
		_finalize_credits(item)
		if item.get("release_dt"):
			item["countdown"] = _countdown_text(today=today, release_dt=item["release_dt"])
		else:
			item["countdown"] = ""
	all_items.sort(key=lambda r: r["release_dt"])

	for role_items in upcoming_by_role.values():
		for item in role_items:
			if item.get("release_dt"):
				item["countdown"] = _countdown_text(today=today, release_dt=item["release_dt"])
			else:
				item["countdown"] = ""

	for studio_card in studio_cards:
		for item in studio_card.get("items", []):
			if item.get("release_dt"):
				item["countdown"] = _countdown_text(today=today, release_dt=item["release_dt"])
			else:
				item["countdown"] = ""

	return render(
		request,
		"catalog/upcoming.html",
		{
			"all_items": all_items,
			"upcoming_by_role": upcoming_by_role,
			"studio_cards": studio_cards,
		},
	)
