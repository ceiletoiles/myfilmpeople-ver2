from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from ..services import get_or_sync_movie, purge_stale_movies
from ..rate_limit import rate_limit
from ..models import DiaryEntry
from ..tmdb import TMDbClient, TMDbError
from ..related_links import build_movie_related_links


def _format_year_runtime(tmdb_raw: dict) -> str:
	release_date = (tmdb_raw.get("release_date") or "").strip()
	year = "TBA"
	if release_date:
		try:
			year = str(datetime.strptime(release_date, "%Y-%m-%d").year)
		except ValueError:
			year = release_date[:4] if len(release_date) >= 4 else "TBA"

	runtime = int(tmdb_raw.get("runtime") or 0)
	if runtime <= 0:
		return year

	hours = runtime // 60
	minutes = runtime % 60
	if hours > 0 and minutes > 0:
		runtime_text = f"{hours}h {minutes}m"
	elif hours > 0:
		runtime_text = f"{hours}h"
	else:
		runtime_text = f"{minutes}m"

	return f"{year} • {runtime_text}"


def _get_director_name(credits: dict) -> str:
	crew = credits.get("crew") or []
	if not isinstance(crew, list):
		return "Unknown"

	for item in crew:
		if not isinstance(item, dict):
			continue
		if (item.get("job") or "").strip().lower() == "director":
			name = (item.get("name") or "").strip()
			if name:
				return name

	return "Unknown"


def _format_tmdb_rating(tmdb_raw: dict) -> str:
	release_date = (tmdb_raw.get("release_date") or "").strip()
	if release_date:
		try:
			release_dt = datetime.strptime(release_date.split("T", 1)[0], "%Y-%m-%d").date()
			if release_dt > datetime.now().date():
				return "--/5"
		except ValueError:
			pass
	else:
		return "--/5"

	vote_average = tmdb_raw.get("vote_average")
	try:
		rating_out_of_five = float(vote_average) / 2
	except (TypeError, ValueError):
		return "--/5"

	return f"{rating_out_of_five:.1f}/5"


def _format_money(value: Any) -> str:
	try:
		amount = int(value or 0)
	except (TypeError, ValueError):
		return "-"

	if amount <= 0:
		return "-"

	return f"${amount:,}"


def _build_alternative_titles(
	payload: dict[str, Any],
	country_name_lookup: dict[str, str],
) -> list[dict[str, str]]:
	titles = payload.get("titles") or []
	if not isinstance(titles, list):
		return []

	results: list[dict[str, str]] = []
	for item in titles:
		if not isinstance(item, dict):
			continue

		title = (item.get("title") or "").strip()
		if not title:
			continue

		country_code = (item.get("iso_3166_1") or "").strip()
		results.append(
			{
				"title": title,
				"country_code": country_code,
				"country_name": _get_country_name(country_code, country_name_lookup) if country_code else "",
			}
		)

	def _sort_key(entry: dict[str, str]) -> tuple[str, str, str]:
		country_code = entry.get("country_code") or ""
		country_name = entry.get("country_name") or ""
		title = entry.get("title") or ""
		return (country_code.lower(), country_name.lower(), title.lower())

	results.sort(key=_sort_key)
	return results


def _build_trailer(payload: dict[str, Any]) -> dict[str, str]:
	results = payload.get("results") or []
	if not isinstance(results, list):
		return {}

	def _score(item: dict[str, Any]) -> tuple[int, int, int]:
		name = (item.get("name") or "").strip().lower()
		video_type = (item.get("type") or "").strip().lower()
		site = (item.get("site") or "").strip().lower()
		official = 0 if item.get("official") else 1
		trailer_rank = 0 if video_type == "trailer" else 1
		youtube_rank = 0 if site == "youtube" else 1
		name_rank = 0 if "trailer" in name else 1
		return (official, trailer_rank, youtube_rank + name_rank)

	filtered: list[dict[str, Any]] = []
	for item in results:
		if not isinstance(item, dict):
			continue
		if (item.get("site") or "").strip().lower() != "youtube":
			continue
		key = (item.get("key") or "").strip()
		if not key:
			continue
		filtered.append(item)

	if not filtered:
		return {}

	filtered.sort(key=_score)
	best = filtered[0]
	key = (best.get("key") or "").strip()
	if not key:
		return {}

	name = (best.get("name") or "Trailer").strip() or "Trailer"
	return {
		"name": name,
		"key": key,
		"youtube_url": f"https://www.youtube.com/watch?v={key}",
		"embed_url": f"https://www.youtube.com/embed/{key}",
	}


def _build_crew_groups(credits: dict[str, Any]) -> list[dict[str, Any]]:
	crew = credits.get("crew") or []
	if not isinstance(crew, list):
		return []

	job_categories: list[tuple[str, list[str]]] = [
		("DIRECTOR", ["Director"]),
		("PRODUCERS", ["Producer", "Executive Producer", "Co-Producer", "Associate Producer"]),
		("WRITER", ["Writer", "Screenplay", "Story", "Original Story"]),
		("ORIGINAL WRITER", ["Original Writer", "Characters", "Novel", "Book"]),
		("CASTING", ["Casting", "Casting Director"]),
		("EDITOR", ["Editor", "Film Editor"]),
		("CINEMATOGRAPHY", ["Director of Photography", "Cinematography"]),
		(
			"ORIGINAL MUSIC COMPOSER",
			[
				"Original Music Composer",
				"Theme Music Composer",
				"Music",
				"Songs",
				"Playback Singer",
				"Singer",
				"Vocalist",
				"Vocals",
			],
		),
		("ADDITIONAL DIRECTING", ["Assistant Director", "First Assistant Director", "Second Assistant Director"]),
		("EXECUTIVE PRODUCERS", ["Executive Producer"]),
		("LIGHTING", ["Gaffer", "Key Grip", "Best Boy Electric", "Lighting Technician"]),
		("CAMERA OPERATORS", ["Camera Operator", "Steadicam Operator", "Camera Technician"]),
	]

	crew_by_job: dict[str, list[dict[str, Any]]] = {}
	for person in crew:
		if not isinstance(person, dict):
			continue

		matched = False
		job = (person.get("job") or "").strip()
		for category, jobs in job_categories:
			if job in jobs:
				crew_by_job.setdefault(category, []).append(person)
				matched = True
				break

		if not matched:
			fallback_job = job or "CREW"
			crew_by_job.setdefault(fallback_job, []).append(person)

	groups: list[dict[str, Any]] = []
	for category, _jobs in job_categories:
		people = crew_by_job.get(category)
		if people:
			groups.append({"job": category, "people": people})
			crew_by_job.pop(category, None)

	for job, people in crew_by_job.items():
		groups.append({"job": job.upper(), "people": people})

	return groups


def _release_type_description(release_type: int) -> str:
	types = {
		1: "Premiere",
		2: "Theatrical (limited)",
		3: "Theatrical",
		4: "Digital",
		5: "Physical",
		6: "TV/Festival",
	}
	return types.get(release_type, "Unknown")


def _format_release_date(date_value: str) -> str:
	if not date_value:
		return ""

	date_part = date_value.strip().split("T", 1)[0]
	try:
		release_date = datetime.strptime(date_part, "%Y-%m-%d")
	except ValueError:
		return date_part or date_value

	return f"{release_date:%b} {release_date.day}, {release_date:%Y}"


def _build_country_name_lookup(country_payload: list[dict[str, Any]]) -> dict[str, str]:
	lookup: dict[str, str] = {}
	for country in country_payload:
		country_code = (country.get("iso_3166_1") or "").strip()
		country_name = (country.get("english_name") or "").strip()
		if country_code and country_name:
			lookup[country_code] = country_name
	return lookup


def _get_country_name(country_code: str, country_name_lookup: dict[str, str]) -> str:
	if not country_code:
		return ""
	return country_name_lookup.get(country_code, country_code)


def _build_release_groups(tmdb_raw: dict[str, Any], country_name_lookup: dict[str, str]) -> list[dict[str, Any]]:
	release_dates = tmdb_raw.get("release_dates") or {}
	results = release_dates.get("results") or []
	if not isinstance(results, list):
		return []

	all_releases: list[dict[str, Any]] = []
	for country_release in results:
		if not isinstance(country_release, dict):
			continue
		country_code = (country_release.get("iso_3166_1") or "").strip()
		entries = country_release.get("release_dates") or []
		if not isinstance(entries, list):
			continue

		for entry in entries:
			if not isinstance(entry, dict):
				continue
			raw_release_date = (entry.get("release_date") or "").strip()
			if not raw_release_date:
				continue

			release_date = raw_release_date.split("T", 1)[0]

			try:
				release_type = int(entry.get("type") or 0)
			except (TypeError, ValueError):
				release_type = 0

			all_releases.append(
				{
					"type": release_type,
					"date": release_date,
					"raw_date": raw_release_date,
					"date_display": _format_release_date(release_date),
					"country_code": country_code,
					"country_name": _get_country_name(country_code, country_name_lookup),
					"certification": (entry.get("certification") or "").strip(),
					"note": (entry.get("note") or "").strip(),
				}
			)

	def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
		premiere_rank = 0 if item.get("type") == 1 else 1
		country_name = str(item.get("country_name") or item.get("country_code") or "")
		return (premiere_rank, str(item.get("date") or ""), country_name.lower())

	all_releases.sort(key=_sort_key)

	grouped: dict[int, list[dict[str, Any]]] = {}
	for release in all_releases:
		release_type = int(release.get("type") or 0)
		grouped.setdefault(release_type, []).append(release)

	release_groups: list[dict[str, Any]] = []
	release_type_order = [1, 2, 3, 4, 5, 6]
	ordered_types = [release_type for release_type in release_type_order if release_type in grouped]
	ordered_types.extend(sorted([release_type for release_type in grouped.keys() if release_type not in release_type_order]))

	for release_type in ordered_types:
		releases = grouped.get(release_type, [])
		displayed_releases: list[dict[str, Any]] = []
		# For certain release types we want to show the date for each country
		# (do not collapse countries that share the same date). However we
		# still want to keep release notes associated with the original
		# date-grouping (notes intended for the date only appear once).
		special_types_no_merge = {1, 2}  # Premiere, Theatrical (limited)
		previous_date_key: str | None = None
		for release in releases:
			date_only_key = release.get("date") or release.get("raw_date") or ""
			# show_date: True for special types (never merge), otherwise only
			# show when the date differs from the previous date in the group.
			show_date = (release_type in special_types_no_merge) or (date_only_key != previous_date_key)
			# show_note_on_date: True only for the first entry of a date group
			# (this preserves whether a note belongs to the date vs a country).
			show_note_on_date = date_only_key != previous_date_key
			display_release = {**release, "show_date": show_date, "show_note_on_date": show_note_on_date}
			displayed_releases.append(display_release)
			previous_date_key = date_only_key

		release_groups.append(
			{
				"type": release_type,
				"label": _release_type_description(release_type),
				"releases": displayed_releases,
			}
		)

	return release_groups


@rate_limit(limit=20, window_seconds=60, bucket_name="movie_detail")
@login_required
def movie_detail(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	tab = (request.GET.get("tab") or "cast").strip().lower()
	if tab not in {"cast", "crew", "details", "release"}:
		tab = "cast"

	include_credits = True
	include_release_dates = True
	try:
		movie = get_or_sync_movie(
			tmdb_id,
			include_credits=include_credits,
			include_release_dates=include_release_dates,
		)
	except TMDbError:
		messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
		return redirect("search")
	movie.last_accessed_at = timezone.now()
	movie.save(update_fields=["last_accessed_at", "updated_at"])
	try:
		purge_stale_movies()
	except Exception:
		pass
	client = TMDbClient.from_settings()
	watched_entry = (
		DiaryEntry.objects.filter(user=request.user, tmdb_id=tmdb_id)
		.only("poster_path", "watched_date", "created_at", "id")
		.first()
	)
	movie_display_poster_path = str((watched_entry.poster_path if watched_entry else "") or movie.poster_path or "").strip()
	movie_watched_date_label = ""
	if watched_entry and watched_entry.watched_date:
		watched_date = watched_entry.watched_date
		movie_watched_date_label = f"Watched on: {watched_date.strftime('%b')} {watched_date.day}, {watched_date.year}"

	cast = []
	crew_groups = []
	release_groups = []
	alternative_titles = []
	credits = movie.tmdb_credits_raw or {}
	movie_raw = movie.tmdb_raw or {}
	movie_year_runtime = _format_year_runtime(movie_raw)
	movie_director = _get_director_name(credits)
	movie_rating_text = _format_tmdb_rating(movie_raw)
	movie_budget_text = _format_money(movie_raw.get("budget"))
	movie_box_office_text = _format_money(movie_raw.get("revenue"))
	country_name_lookup: dict[str, str] = {}
	try:
		country_name_lookup = _build_country_name_lookup(client.get_configuration_countries())
	except TMDbError:
		country_name_lookup = {}
	if include_credits:
		cast = credits.get("cast", []) or []
		crew_groups = _build_crew_groups(credits)

	release_groups = _build_release_groups(movie_raw, country_name_lookup)
	try:
		alt_titles_payload = client.get_movie_alternative_titles(tmdb_id)
	except TMDbError:
		alt_titles_payload = {}
	alternative_titles = _build_alternative_titles(alt_titles_payload, country_name_lookup)
	try:
		trailer_payload = client.get_movie_videos(tmdb_id)
	except TMDbError:
		trailer_payload = {}
	movie_trailer = _build_trailer(trailer_payload)

	# External/related links (TMDb, IMDb, homepage, socials)
	try:
		external_ids_payload = client.get_movie_external_ids(tmdb_id)
	except TMDbError:
		external_ids_payload = {}

	# Merge external ids into a copy of the movie raw payload so
	# `build_movie_related_links` can discover social ids.
	combined_raw = dict(movie_raw or {})
	if isinstance(external_ids_payload, dict):
		combined_raw["external_ids"] = external_ids_payload

	related_links = build_movie_related_links(tmdb_id, combined_raw)

	# Similar/Related movies are lazy-loaded via JSON endpoints.

	return render(
		request,
		"catalog/movie_detail.html",
		{
			"movie": movie,
			"tab": tab,
			"cast": cast,
			"crew_groups": crew_groups,
			"release_groups": release_groups,
			"alternative_titles": alternative_titles,
			"movie_trailer": movie_trailer,
			"movie_year_runtime": movie_year_runtime,
			"movie_director": movie_director,
			"movie_rating_text": movie_rating_text,
			"movie_budget_text": movie_budget_text,
			"movie_box_office_text": movie_box_office_text,
			"related_links": related_links,
			"movie_display_poster_path": movie_display_poster_path,
			"movie_watched_date_label": movie_watched_date_label,
		},
	)


def _year_from_release_date(release_date: Any) -> str:
	value = (str(release_date or "")).strip()
	if len(value) >= 4:
		return value[:4]
	return "TBA"


def _movie_card_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	out: list[dict[str, Any]] = []
	for item in items:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		title = (item.get("title") or item.get("original_title") or "").strip()
		release_date = (item.get("release_date") or "").strip()
		out.append(
			{
				"id": mid,
				"title": title or "-",
				"year": _year_from_release_date(release_date),
				"poster_path": (item.get("poster_path") or ""),
			}
		)
	return out


@rate_limit(limit=30, window_seconds=60, bucket_name="movie_similar")
@login_required
def movie_similar(request: HttpRequest, tmdb_id: int) -> JsonResponse:
	# Fetch similar movies on-demand from TMDb (do not persist into DB).
	page = int(request.GET.get("page") or 1)
	client = TMDbClient.from_settings()
	try:
		payload = client.get_movie_similar(tmdb_id, page=page) or {}
	except TMDbError:
		payload = {}

	results: list[dict[str, Any]] = []
	if isinstance(payload, dict) and isinstance(payload.get("results"), list):
		results = [r for r in payload.get("results") if isinstance(r, dict)]

	seen: set[int] = set()
	filtered: list[dict[str, Any]] = []
	for item in results:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		if mid == tmdb_id or mid in seen:
			continue
		seen.add(mid)
		filtered.append(item)
		if len(filtered) >= 12:
			break

	return JsonResponse({"movies": _movie_card_payload(filtered)})


@rate_limit(limit=30, window_seconds=60, bucket_name="movie_related")
@login_required
def movie_related(request: HttpRequest, tmdb_id: int) -> JsonResponse:
	try:
		movie = get_or_sync_movie(tmdb_id)
	except TMDbError:
		return JsonResponse({"movies": []})
	tmdb_raw = movie.tmdb_raw if isinstance(movie.tmdb_raw, dict) else {}

	belongs = tmdb_raw.get("belongs_to_collection")
	collection_id: int | None = None
	if isinstance(belongs, dict) and isinstance(belongs.get("id"), int):
		collection_id = int(belongs["id"])

	items: list[dict[str, Any]] = []
	if collection_id:
		client = TMDbClient.from_settings()
		try:
			collection_payload = client.get_collection(collection_id)
		except TMDbError:
			collection_payload = {}

		parts = collection_payload.get("parts")
		if isinstance(parts, list):
			items = [p for p in parts if isinstance(p, dict) and p.get("id") != movie.tmdb_id]

			def _rel_sort_key(item: dict[str, Any]) -> tuple[int, str]:
				rd = (item.get("release_date") or "").strip()
				return (0 if rd else 1, rd)

			items.sort(key=_rel_sort_key)

	return JsonResponse({"movies": _movie_card_payload(items)})
