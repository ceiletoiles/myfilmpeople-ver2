from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.http import JsonResponse
from django.shortcuts import render

from ..services import get_or_sync_movie
from ..tmdb import TMDbClient, TMDbError


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


def _get_country_name(country_code: str) -> str:
	countries = {
		"US": "United States",
		"GB": "United Kingdom",
		"FR": "France",
		"DE": "Germany",
		"IT": "Italy",
		"ES": "Spain",
		"JP": "Japan",
		"KR": "South Korea",
		"CN": "China",
		"IN": "India",
		"CA": "Canada",
		"AU": "Australia",
		"BR": "Brazil",
		"MX": "Mexico",
		"RU": "Russia",
		"NL": "Netherlands",
		"BE": "Belgium",
		"CH": "Switzerland",
		"AT": "Austria",
		"SE": "Sweden",
		"NO": "Norway",
		"DK": "Denmark",
		"FI": "Finland",
		"PL": "Poland",
		"CZ": "Czech Republic",
		"HU": "Hungary",
		"GR": "Greece",
		"PT": "Portugal",
		"IE": "Ireland",
		"NZ": "New Zealand",
		"ZA": "South Africa",
		"AR": "Argentina",
		"CL": "Chile",
		"CO": "Colombia",
		"PE": "Peru",
		"VE": "Venezuela",
		"TH": "Thailand",
		"ID": "Indonesia",
		"MY": "Malaysia",
		"SG": "Singapore",
		"PH": "Philippines",
		"VN": "Vietnam",
		"TW": "Taiwan",
		"HK": "Hong Kong",
		"TR": "Turkey",
		"IL": "Israel",
		"SA": "Saudi Arabia",
		"AE": "United Arab Emirates",
		"EG": "Egypt",
		"NG": "Nigeria",
		"KE": "Kenya",
		"ET": "Ethiopia",
		"GH": "Ghana",
		"PR": "Puerto Rico",
		"AM": "Armenia",
		"HR": "Croatia",
		"RO": "Romania",
		"BG": "Bulgaria",
		"RS": "Serbia",
		"SI": "Slovenia",
		"SK": "Slovakia",
		"LT": "Lithuania",
		"LV": "Latvia",
		"EE": "Estonia",
		"UA": "Ukraine",
		"BY": "Belarus",
		"MD": "Moldova",
		"BA": "Bosnia and Herzegovina",
		"MK": "North Macedonia",
		"AL": "Albania",
		"MT": "Malta",
		"CY": "Cyprus",
		"IS": "Iceland",
		"LU": "Luxembourg",
		"MC": "Monaco",
		"AD": "Andorra",
		"LI": "Liechtenstein",
		"SM": "San Marino",
		"VA": "Vatican City",
		"GI": "Gibraltar",
		"IM": "Isle of Man",
		"JE": "Jersey",
		"GG": "Guernsey",
		"FO": "Faroe Islands",
		"GL": "Greenland",
	}
	return countries.get(country_code, country_code)


def _build_release_groups(tmdb_raw: dict[str, Any]) -> list[dict[str, Any]]:
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
					"country_name": _get_country_name(country_code),
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
		previous_date_key: str | None = None
		for release in releases:
			date_key = release.get("date") or release.get("raw_date") or ""
			display_release = {**release, "show_date": date_key != previous_date_key}
			displayed_releases.append(display_release)
			previous_date_key = date_key

		release_groups.append(
			{
				"type": release_type,
				"label": _release_type_description(release_type),
				"releases": displayed_releases,
			}
		)

	return release_groups


@login_required
def movie_detail(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	tab = (request.GET.get("tab") or "cast").strip().lower()
	if tab not in {"cast", "crew", "details", "release"}:
		tab = "cast"

	include_credits = True
	include_release_dates = True
	movie = get_or_sync_movie(
		tmdb_id,
		include_credits=include_credits,
		include_release_dates=include_release_dates,
	)

	cast = []
	crew_groups = []
	release_groups = []
	credits = movie.tmdb_credits_raw or {}
	movie_year_runtime = _format_year_runtime(movie.tmdb_raw or {})
	movie_director = _get_director_name(credits)
	movie_rating_text = _format_tmdb_rating(movie.tmdb_raw or {})
	movie_budget_text = _format_money((movie.tmdb_raw or {}).get("budget"))
	movie_box_office_text = _format_money((movie.tmdb_raw or {}).get("revenue"))
	if include_credits:
		cast = credits.get("cast", []) or []
		crew_groups = _build_crew_groups(credits)

	release_groups = _build_release_groups(movie.tmdb_raw or {})

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
			"movie_year_runtime": movie_year_runtime,
			"movie_director": movie_director,
			"movie_rating_text": movie_rating_text,
			"movie_budget_text": movie_budget_text,
			"movie_box_office_text": movie_box_office_text,
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


@login_required
def movie_similar(request: HttpRequest, tmdb_id: int) -> JsonResponse:
	movie = get_or_sync_movie(
		tmdb_id,
		include_similar=True,
		similar_page=1,
	)

	tmdb_raw = movie.tmdb_raw if isinstance(movie.tmdb_raw, dict) else {}
	sim_pages = tmdb_raw.get("similar_pages")
	results: list[dict[str, Any]] = []
	if isinstance(sim_pages, dict):
		page_1 = sim_pages.get("1")
		if isinstance(page_1, dict) and isinstance(page_1.get("results"), list):
			results = [r for r in page_1["results"] if isinstance(r, dict)]

	seen: set[int] = set()
	filtered: list[dict[str, Any]] = []
	for item in results:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		if mid == movie.tmdb_id or mid in seen:
			continue
		seen.add(mid)
		filtered.append(item)
		if len(filtered) >= 12:
			break

	return JsonResponse({"movies": _movie_card_payload(filtered)})


@login_required
def movie_related(request: HttpRequest, tmdb_id: int) -> JsonResponse:
	movie = get_or_sync_movie(tmdb_id)
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
