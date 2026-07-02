from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..models import NewMovieArrival, NewsletterItem, NewsletterItemSeen


HISTORY_DAYS = 30


@dataclass
class MovieSections:
	all_items: list[dict]
	by_role: dict[str, list[dict]]
	studio_cards: list[dict]


def _event_note(arrival: NewMovieArrival) -> str:
	meta = arrival.event_meta or {}
	if (arrival.event_type or "").strip().lower() == "update":
		if isinstance(meta, dict) and meta.get("field") == "release_date":
			old_v = (meta.get("old") or "").strip() if isinstance(meta.get("old"), str) else ""
			new_v = (meta.get("new") or "").strip() if isinstance(meta.get("new"), str) else ""
			old_label = old_v if old_v else "TBA"
			new_label = new_v if new_v else "TBA"
			try:
				new_dt = date.fromisoformat(new_v) if new_v else None
			except ValueError:
				new_dt = None
			if new_dt is not None:
				pretty = new_dt.strftime("%B %d, %Y").replace(" 0", " ")
				return f"Updated release date: {old_label} -> {pretty}"
			return f"Updated release date: {old_label} -> {new_label}"
		return "Updated on TMDb"

	# If TMDb edit timestamp is present, show the edit date (YYYY-MM-DD).
	if isinstance(meta, dict):
		ted = meta.get("tmdb_edited_at")
		if isinstance(ted, str) and ted:
			try:
				from datetime import datetime
				parsed = datetime.fromisoformat(ted)
				edited_date = parsed.date().isoformat()
			except Exception:
				edited_date = ted.split("T", 1)[0] if "T" in ted else ted
			if edited_date:
				return f"Edited on {edited_date}"
	if isinstance(meta, dict) and meta.get("kind") == "comeback":
		gap_label = meta.get("gap_label")
		if isinstance(gap_label, str) and gap_label.strip():
			return f"Back after {gap_label.strip()}"
		return "Back after a long gap"
	return ""


def _movie_entry(arrival: NewMovieArrival) -> dict:
	movie = arrival.movie
	# Use seen_at if available, otherwise use created_at for history grouping
	display_date = arrival.seen_at or arrival.created_at
	credits = []
	meta = arrival.event_meta or {}
	if arrival.source_name:
		# Prefer the exact credit job from TMDb change metadata when available.
		role_label = ""
		if isinstance(meta, dict):
			r = meta.get("credit_job")
			if isinstance(r, str) and r.strip():
				role_label = r.strip()
		if not role_label:
			role_label = (arrival.role or "").strip()

		char = ""
		if isinstance(meta, dict):
			c = meta.get("character")
			if isinstance(c, str) and c.strip():
				char = c.strip()

		if role_label and char:
			credits = [f"{arrival.source_name} - {role_label} (as {char})"]
		elif role_label:
			credits = [f"{arrival.source_name} - {role_label}"]
		else:
			credits = [arrival.source_name]

	event_note = _event_note(arrival)
	# Append TMDb edit date if present in metadata
	if isinstance(meta, dict):
		ted = meta.get("tmdb_edited_at")
		if isinstance(ted, str) and ted:
			try:
				from datetime import datetime
				parsed = datetime.fromisoformat(ted)
				edited_date = parsed.date().isoformat()
			except Exception:
				edited_date = ted.split("T", 1)[0] if "T" in ted else ted
			if edited_date:
				if event_note:
					event_note = f"{event_note} — Edited on {edited_date}"
				else:
					event_note = f"Edited on {edited_date}"

	return {
		"movie_id": movie.tmdb_id,
		"title": movie.title,
		"release_date": movie.release_date,
		"release_dt": movie.release_date,
		"poster_path": movie.poster_path,
		"event_note": event_note,
		"seen_at": display_date,
		"credits": credits,
	}


def _build_movie_sections(arrivals, *, sort_by_seen: bool = False) -> MovieSections:
	new_by_role: dict[str, list[dict]] = {"director": [], "actor": [], "crew": []}
	all_movies_by_id: dict[int, dict] = {}
	studio_cards: dict[str, dict] = {}

	for arrival in arrivals:
		entry = _movie_entry(arrival)
		movie_id = entry["movie_id"]
		role = (arrival.role or "").strip().lower()

		if movie_id not in all_movies_by_id:
			all_movies_by_id[movie_id] = entry
		else:
			existing = all_movies_by_id[movie_id]
			credits = existing.get("credits", [])
			new_credit = f"{arrival.source_name} - {arrival.role}" if arrival.source_name and arrival.role else arrival.source_name
			if new_credit and new_credit not in credits:
				credits.append(new_credit)
				existing["credits"] = credits
			if entry.get("event_note") and not existing.get("event_note"):
				existing["event_note"] = entry["event_note"]
			if entry.get("seen_at") and (not existing.get("seen_at") or entry["seen_at"] > existing["seen_at"]):
				existing["seen_at"] = entry["seen_at"]

		if arrival.source_type == "person" and role in new_by_role:
			if movie_id not in [m["movie_id"] for m in new_by_role[role]]:
				new_by_role[role].append(entry)
		elif arrival.source_type == "company":
			company_key = f"{arrival.source_type}_{arrival.source_id}"
			bucket = studio_cards.setdefault(
				company_key,
				{
					"source_id": arrival.source_id,
					"source_name": arrival.source_name,
					"items": [],
				},
			)
			if movie_id not in [m["movie_id"] for m in bucket["items"]]:
				bucket["items"].append(entry)

	sort_key = (lambda r: (r.get("seen_at") or timezone.now())) if sort_by_seen else (lambda r: (r.get("release_dt") or date.min))
	for items in new_by_role.values():
		items.sort(key=sort_key, reverse=True)

	all_items = list(all_movies_by_id.values())
	all_items.sort(key=sort_key, reverse=True)
	studio_cards_list = sorted(
		[
			{
				"source_id": card["source_id"],
				"name": card["source_name"],
				"recent_count": len(card["items"]),
				"items": card["items"],
			}
			for card in studio_cards.values()
		],
		key=lambda s: (-int(s.get("recent_count") or 0), (s.get("name") or "").casefold()),
	)
	return MovieSections(all_items=all_items, by_role=new_by_role, studio_cards=studio_cards_list)


def _newsletter_entries(items_qs) -> list[dict]:
	return [
		{
			"id": item.id,
			"text": item.text,
			"issue_date": item.issue.issue_date,
			"provider_name": item.issue.provider_name,
			"seen_at": item.issue.published_at,
		}
		for item in items_qs
	]


def _newsletter_history_entries(seen_qs) -> list[dict]:
	return [
		{
			"id": seen.item_id,
			"text": seen.item.text,
			"issue_date": seen.item.issue.issue_date,
			"provider_name": seen.item.issue.provider_name,
			"seen_at": seen.seen_at or seen.item.issue.published_at,
		}
		for seen in seen_qs
	]


def _group_items_by_month(items: list[dict]) -> list[dict]:
	"""Group items by month (e.g., 'May 2026'), newest month first.
	Items without seen_at date are grouped in 'Undated' group."""
	from collections import defaultdict
	
	grouped = defaultdict(list)
	undated_items = []
	
	for item in items:
		seen_dt = item.get("seen_at")
		if seen_dt is None:
			# Items without a seen_at are collected separately
			undated_items.append(item)
		else:
			# Convert to date if datetime
			if hasattr(seen_dt, 'date'):
				seen_date = seen_dt.date()
			else:
				seen_date = seen_dt
			month_key = seen_date.strftime("%B %Y")
			grouped[month_key].append(item)
	
	# Sort months newest-first (by parsing the month key)
	sorted_groups = []
	month_order = {
		"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
		"July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
	}
	
	for month_key in sorted(grouped.keys(), key=lambda m: date(int(m.split()[-1]), month_order[m.split()[0]], 1), reverse=True):
		sorted_groups.append({"month": month_key, "items": grouped[month_key]})
	
	# Add undated items at the end if any exist
	if undated_items:
		sorted_groups.append({"month": "Undated", "items": undated_items})
	
	return sorted_groups


@login_required
def new_arrivals(request: HttpRequest) -> HttpResponse:
	"""Display current New Arrivals and 30-day History in the same page."""
	now = timezone.now()
	today = now.date()
	history_cutoff_dt = now - timedelta(days=HISTORY_DAYS)
	history_cutoff_date = history_cutoff_dt.date()

	current_movie_arrivals = NewMovieArrival.objects.select_related("movie").filter(user=request.user, is_seen=False)
	current_movie_arrivals = current_movie_arrivals.filter(
		Q(movie__release_date__isnull=True) | Q(movie__release_date__gte=today)
	)
	current_newsletter_items = (
		NewsletterItem.objects.select_related("issue")
		.filter(issue__published_at__isnull=False)
		.exclude(seen_by__user=request.user)
	)

	current_movies = _build_movie_sections(current_movie_arrivals)
	current_dailies = _newsletter_entries(current_newsletter_items)

	# History is limited to the last 30 days by item date.
	# Include items where either created_at OR seen_at is within the last year
	history_movie_arrivals = NewMovieArrival.objects.select_related("movie").filter(
		user=request.user,
		is_seen=True,
	).filter(
		Q(created_at__gte=history_cutoff_dt) | Q(seen_at__gte=history_cutoff_dt)
	).filter(
		Q(movie__release_date__isnull=True) | Q(movie__release_date__gte=today)
	)
	history_newsletter_items = (
		NewsletterItemSeen.objects.select_related("item", "item__issue")
		.filter(user=request.user, item__issue__published_at__isnull=False, item__issue__issue_date__gte=history_cutoff_date)
		.order_by("-seen_at")
	)

	history_movies = _build_movie_sections(history_movie_arrivals, sort_by_seen=True)
	history_dailies = _newsletter_history_entries(history_newsletter_items)

	# Group history items by month
	history_all_grouped = _group_items_by_month(history_movies.all_items)
	history_by_role_grouped = {role: _group_items_by_month(items) for role, items in history_movies.by_role.items()}
	history_dailies_grouped = _group_items_by_month(history_dailies)

	# Mark current inbox entries as seen after reading the page.
	if current_movie_arrivals.exists():
		NewMovieArrival.objects.filter(user=request.user, is_seen=False).update(is_seen=True, seen_at=now)
	if current_dailies:
		NewsletterItemSeen.objects.bulk_create(
			[NewsletterItemSeen(user=request.user, item_id=row["id"]) for row in current_dailies],
			ignore_conflicts=True,
		)

	current_tab_counts = {
		"all": len(current_movies.all_items) + len(current_dailies),
		"dailies": len(current_dailies),
		"directors": len(current_movies.by_role["director"]),
		"actors": len(current_movies.by_role["actor"]),
		"crew": len(current_movies.by_role["crew"]),
		"studios": sum(int(c.get("recent_count") or 0) for c in current_movies.studio_cards),
	}

	# Count total items in grouped history for tab counts
	history_all_count = sum(len(group["items"]) for group in history_all_grouped)
	history_dailies_count = sum(len(group["items"]) for group in history_dailies_grouped)
	history_directors_count = sum(len(group["items"]) for group in history_by_role_grouped["director"])
	history_actors_count = sum(len(group["items"]) for group in history_by_role_grouped["actor"])
	history_crew_count = sum(len(group["items"]) for group in history_by_role_grouped["crew"])

	history_tab_counts = {
		"all": history_all_count + history_dailies_count,
		"dailies": history_dailies_count,
		"directors": history_directors_count,
		"actors": history_actors_count,
		"crew": history_crew_count,
		"studios": sum(int(c.get("recent_count") or 0) for c in history_movies.studio_cards),
	}

	return render(
		request,
		"catalog/new_arrivals.html",
		{
			"current_all_items": current_movies.all_items,
			"current_by_role": current_movies.by_role,
			"current_studio_cards": current_movies.studio_cards,
			"current_dailies_items": current_dailies,
			"current_tab_counts": current_tab_counts,
			"history_all_items": history_all_grouped,
			"history_by_role": history_by_role_grouped,
			"history_studio_cards": history_movies.studio_cards,
			"history_dailies_items": history_dailies_grouped,
			"history_tab_counts": history_tab_counts,
			"history_cutoff_date": history_cutoff_date,
		},
	)
