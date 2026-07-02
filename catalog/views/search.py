from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import hashlib
import json
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from ..models import CompanyFollow, PersonFollow
from ..services import get_person_known_for_department
from ..rate_limit import rate_limit
from ..tmdb import TMDbClient


MAX_SEARCH_RESULTS = 50
logger = logging.getLogger(__name__)


def _fuzzy_ratio(a: str, b: str) -> float:
	"""Compute similarity ratio between two strings (0.0 to 1.0)."""
	return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _matches_fuzzy(query: str, name: str, *, threshold: float = 0.7) -> bool:
	"""Check if name matches query within fuzzy threshold (typo-tolerant)."""
	if not query or not name:
		return False
	query_norm = query.lower().strip()
	name_norm = name.lower().strip()
	
	# Exact substring match (highest priority)
	if query_norm in name_norm or name_norm in query_norm:
		return True
	
	# Fuzzy similarity match
	return _fuzzy_ratio(query_norm, name_norm) >= threshold


def _clamp_int(value: str, *, default: int, min_value: int, max_value: int) -> int:
	try:
		n = int((value or "").strip())
	except (TypeError, ValueError):
		n = default
	if n < min_value:
		return min_value
	if n > max_value:
		return max_value
	return n


def _cache_key(prefix: str, payload: dict) -> str:
	"""Build a stable, short cache key from a dict payload."""
	digest = hashlib.sha256(
		json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
	).hexdigest()
	return f"{prefix}:{digest}"


def _tmdb_entity_search(
	query: str,
	*,
	limit: int | None = None,
	include_movie_director: bool = False,
) -> dict[str, list[dict]]:
	people: list[dict] = []
	companies: list[dict] = []
	movies: list[dict] = []

	client = TMDbClient.from_settings()

	# Fetch first page for each category in parallel, then optionally
	# paginate further if `limit` is None (i.e. no cap requested).
	with ThreadPoolExecutor(max_workers=3) as ex:
		people_future = ex.submit(client.search_people, query, page=1)
		companies_future = ex.submit(client.search_companies, query, page=1)
		movies_future = ex.submit(client.search_movies, query, page=1)

		people_payload = people_future.result() or {}
		companies_payload = companies_future.result() or {}
		movies_payload = movies_future.result() or {}

	tmdb_people = people_payload.get("results") or []
	tmdb_companies = companies_payload.get("results") or []
	tmdb_movies = movies_payload.get("results") or []

	# If no explicit `limit` was requested, fetch additional pages from TMDb
	# (pagination) to return all available results for each category.
	if limit is None:
		# People
		try:
			total_pages = int(people_payload.get("total_pages") or 1)
		except (TypeError, ValueError):
			total_pages = 1
		for p in range(2, total_pages + 1):
			try:
				extra = client.search_people(query, page=p) or {}
				tmdb_people.extend(extra.get("results") or [])
			except Exception:
				break

		# Companies
		try:
			total_pages = int(companies_payload.get("total_pages") or 1)
		except (TypeError, ValueError):
			total_pages = 1
		for p in range(2, total_pages + 1):
			try:
				extra = client.search_companies(query, page=p) or {}
				tmdb_companies.extend(extra.get("results") or [])
			except Exception:
				break

		# Movies
		try:
			total_pages = int(movies_payload.get("total_pages") or 1)
		except (TypeError, ValueError):
			total_pages = 1
		for p in range(2, total_pages + 1):
			try:
				extra = client.search_movies(query, page=p) or {}
				tmdb_movies.extend(extra.get("results") or [])
			except Exception:
				break

	for r in tmdb_people:
		if limit is not None and len(people) >= limit:
			break
		pid = r.get("id")
		if not isinstance(pid, int):
			continue
		name = str(r.get("name") or str(pid))
		# Include if fuzzy-matches query (typo-tolerant search)
		if not _matches_fuzzy(query, name, threshold=0.7):
			continue
		known_for_titles: list[str] = []
		for kf in (r.get("known_for") or []):
			title = str((kf or {}).get("title") or (kf or {}).get("name") or "").strip()
			if not title:
				continue
			known_for_titles.append(title)
			if len(known_for_titles) >= 3:
				break
		people.append(
			{
				"id": pid,
				"name": name,
				"profile_path": r.get("profile_path") or "",
				"known_for_department": str(r.get("known_for_department") or "").strip(),
				"known_for_titles": known_for_titles,
				"url": reverse("person_detail", args=[pid]),
			}
		)

	for r in tmdb_companies:
		if limit is not None and len(companies) >= limit:
			break
		cid = r.get("id")
		if not isinstance(cid, int):
			continue
		name = str(r.get("name") or str(cid))
		# Include if fuzzy-matches query (typo-tolerant search)
		if not _matches_fuzzy(query, name, threshold=0.7):
			continue
		origin = str(r.get("origin_country") or "").strip()
		companies.append(
			{
				"id": cid,
				"name": name,
				"logo_path": r.get("logo_path") or "",
				"origin": origin,
				"url": reverse("company_detail", args=[cid]),
			}
		)

	for r in tmdb_movies:
		if limit is not None and len(movies) >= limit:
			break
		mid = r.get("id")
		if not isinstance(mid, int):
			continue
		title = str(r.get("title") or str(mid))
		# Include if fuzzy-matches query (typo-tolerant search)
		if not _matches_fuzzy(query, title, threshold=0.7):
			continue
		movies.append(
			{
				"id": mid,
				"title": title,
				"poster_path": r.get("poster_path") or "",
				"release_date": r.get("release_date") or "",
				"director": "",
				"url": reverse("movie_detail", args=[mid]),
			}
		)

	if include_movie_director and movies:
		def _director_for_movie(movie_id: int) -> str:
			credits = client.get_movie_credits(movie_id) or {}
			crew = credits.get("crew") or []
			directors = [
				str(c.get("name") or "").strip()
				for c in crew
				if str(c.get("job") or "").strip().lower() == "director" and str(c.get("name") or "").strip()
			]
			return ", ".join(directors[:2])

		with ThreadPoolExecutor(max_workers=min(5, len(movies))) as ex:
			future_to_mid = {ex.submit(_director_for_movie, int(m["id"])): m for m in movies}
			for fut, movie in future_to_mid.items():
				try:
					movie["director"] = fut.result()
				except Exception:
					movie["director"] = ""

	return {"people": people, "companies": companies, "movies": movies}


def _known_for_titles_from_credits(credits_raw: object, *, limit: int = 3) -> list[str]:
	if not isinstance(credits_raw, dict):
		return []
	items = list(credits_raw.get("cast") or []) + list(credits_raw.get("crew") or [])
	if not items:
		return []

	scored: list[tuple[float, str]] = []
	seen: set[str] = set()
	for item in items:
		if not isinstance(item, dict):
			continue
		title = str(item.get("title") or item.get("name") or "").strip()
		if not title:
			continue
		key = title.casefold()
		if key in seen:
			continue
		seen.add(key)
		try:
			score = float(item.get("popularity") or 0)
		except (TypeError, ValueError):
			score = 0.0
		scored.append((score, title))

	scored.sort(key=lambda x: (x[0], x[1].casefold()), reverse=True)
	return [title for _, title in scored[:limit]]


def _origin_from_company_raw(raw: object) -> str:
	if not isinstance(raw, dict):
		return ""
	origin = raw.get("origin_country")
	if isinstance(origin, list) and origin:
		return str(origin[0] or "").strip()
	if isinstance(origin, str):
		return origin.strip()
	for key in ("country", "headquarters"):
		value = raw.get(key)
		if isinstance(value, str) and value.strip():
			return value.strip()
	return ""


def _tmdb_prefixed_query(query: str) -> tuple[str, int] | None:
	query = (query or "").strip()
	if ":" not in query:
		return None
	prefix, raw_id = query.split(":", 1)
	prefix = prefix.strip().lower()
	raw_id = raw_id.strip()
	if prefix not in {"p", "c", "m"} or not raw_id.isdigit():
		return None
	try:
		value = int(raw_id)
	except (TypeError, ValueError):
		return None
	return (prefix, value) if value > 0 else None


def _user_prefixed_query(query: str) -> str | None:
	query = (query or "").strip()
	if ":" not in query:
		return None
	prefix, raw_username = query.split(":", 1)
	if prefix.strip().lower() != "u":
		return None
	username = raw_username.strip()
	if username.startswith("@"):
		username = username[1:].strip()
	return username or None


def _person_result_from_tmdb_raw(raw: dict[str, object]) -> dict:
	known_for_titles: list[str] = []
	for kf in (raw.get("known_for") or []):
		title = str((kf or {}).get("title") or (kf or {}).get("name") or "").strip()
		if not title:
			continue
		known_for_titles.append(title)
		if len(known_for_titles) >= 3:
			break
	person_id = int(raw.get("id") or 0)
	return {
		"id": person_id,
		"name": str(raw.get("name") or person_id),
		"profile_path": str(raw.get("profile_path") or ""),
		"known_for_department": str(raw.get("known_for_department") or "").strip(),
		"known_for_titles": known_for_titles,
		"url": reverse("person_detail", args=[person_id]),
	}


def _company_result_from_tmdb_raw(raw: dict[str, object]) -> dict:
	company_id = int(raw.get("id") or 0)
	return {
		"id": company_id,
		"name": str(raw.get("name") or company_id),
		"logo_path": str(raw.get("logo_path") or ""),
		"origin": _origin_from_company_raw(raw),
		"url": reverse("company_detail", args=[company_id]),
	}


def _movie_result_from_tmdb_raw(raw: dict[str, object]) -> dict:
	movie_id = int(raw.get("id") or 0)
	return {
		"id": movie_id,
		"title": str(raw.get("title") or movie_id),
		"poster_path": str(raw.get("poster_path") or ""),
		"release_date": str(raw.get("release_date") or ""),
		"director": "",
		"url": reverse("movie_detail", args=[movie_id]),
	}


@rate_limit(limit=60, window_seconds=60, bucket_name="search_suggest")
@login_required
def search_suggest(request: HttpRequest) -> JsonResponse:
	"""Lightweight JSON suggestions for the Search page.

	TMDb-first for entity suggestions (people, companies, movies).
	Intended for AJAX/autocomplete (no role options / follow actions here).
	"""
	query = (request.GET.get("q") or "").strip()
	limit = _clamp_int(request.GET.get("limit") or "", default=5, min_value=1, max_value=10)

	# Cache globally (not user-specific).
	# Short TTL because suggestions should stay fresh.
	suggest_cache_key = _cache_key(
		"search:suggest:v5",
		{
			"q": query,
			"limit": int(limit),
		},
	)
	try:
		cached = cache.get(suggest_cache_key)
		if isinstance(cached, dict):
			return JsonResponse(cached)
	except Exception:
		pass

	if len(query) < 2:
		return JsonResponse({"q": query, "people": [], "companies": [], "movies": []})

	user_query = _user_prefixed_query(query)
	if user_query is not None:
		return JsonResponse({"q": query, "people": [], "companies": [], "movies": []})

	prefixed_query = _tmdb_prefixed_query(query)
	if prefixed_query is not None:
		query_kind, query_id = prefixed_query
		client = TMDbClient.from_settings()
		people: list[dict] = []
		companies: list[dict] = []
		movies: list[dict] = []
		lookup_plan = {
			"p": (client.get_person, _person_result_from_tmdb_raw, people),
			"c": (client.get_company, _company_result_from_tmdb_raw, companies),
			"m": (client.get_movie, _movie_result_from_tmdb_raw, movies),
		}
		fetcher, builder, bucket = lookup_plan[query_kind]
		try:
			raw = fetcher(query_id) or {}
			if isinstance(raw, dict) and int(raw.get("id") or 0) == query_id:
				result = builder(raw)
				result["from_direct_lookup"] = True
				bucket.append(result)
		except Exception:
			pass
		payload = {"q": query, "people": people, "companies": companies, "movies": movies}
		try:
			cache.set(suggest_cache_key, payload, timeout=60)
		except Exception:
			pass
		return JsonResponse(payload)

	entities: dict[str, list[dict]] = {"people": [], "companies": [], "movies": []}
	try:
			entities = _tmdb_entity_search(query, limit=limit, include_movie_director=True)
	except Exception:
		# If TMDb is unavailable, keep suggestions empty.
		pass

	payload = {"q": query, **entities}
	try:
		cache.set(suggest_cache_key, payload, timeout=60)
	except Exception:
		pass
	return JsonResponse(payload)


@rate_limit(limit=30, window_seconds=60, bucket_name="search_page")
@login_required
def search(request: HttpRequest) -> HttpResponse:
	query = (request.GET.get("q") or "").strip()
	query_norm = " ".join(query.split()).strip()
	user_query = _user_prefixed_query(query_norm)
	prefixed_query = _tmdb_prefixed_query(query_norm)
	people_results: list[dict] = []
	company_results: list[dict] = []
	movie_results: list[dict] = []
	following_people_results: list[dict] = []
	following_company_results: list[dict] = []
	user_results = []
	error: str | None = None

	if not query:
		return render(
			request,
			"catalog/search.html",
			{
				"q": query,
				"user_results": user_results,
				"people_results": people_results,
				"company_results": company_results,
				"movie_results": movie_results,
				"following_people_results": following_people_results,
				"following_company_results": following_company_results,
				"following_count": 0,
				"error": error,
			},
		)

	# Cache the search page payload by query (not user-specific).
	# TMDb results are cached to keep repeat searches responsive.
	page_cache_key = _cache_key(
		"search:page:v5",
		{
			"q": query_norm,
			"uid": int(getattr(request.user, "id", 0) or 0),
		},
	)
	try:
		cached = cache.get(page_cache_key)
		if isinstance(cached, dict):
			return render(
				request,
				"catalog/search.html",
				{
					"q": query,
					"user_results": cached.get("user_results") or [],
					"people_results": cached.get("people_results") or [],
					"company_results": cached.get("company_results") or [],
					"movie_results": cached.get("movie_results") or [],
					"following_people_results": cached.get("following_people_results") or [],
					"following_company_results": cached.get("following_company_results") or [],
					"following_count": int(cached.get("following_count") or 0),
					"error": cached.get("error"),
				},
			)
	except Exception:
		pass

	User = get_user_model()
	user_search_term = user_query or query
	user_results = [
		{"username": u.username}
		for u in (
		User.objects.filter(username__icontains=user_search_term)
		.only("username")
		.order_by("username")[:10]
		)
	]

	entities: dict[str, list[dict]] = {"people": [], "companies": [], "movies": []}
	try:
		if user_query is not None:
			pass
		elif prefixed_query is not None:
			query_kind, query_id = prefixed_query
			client = TMDbClient.from_settings()
			if query_kind == "p":
				raw = client.get_person(query_id) or {}
				if isinstance(raw, dict) and int(raw.get("id") or 0) == query_id:
					result = _person_result_from_tmdb_raw(raw)
					result["from_direct_lookup"] = True
					entities["people"] = [result]
			elif query_kind == "c":
				raw = client.get_company(query_id) or {}
				if isinstance(raw, dict) and int(raw.get("id") or 0) == query_id:
					entities["companies"] = [_company_result_from_tmdb_raw(raw)]
			elif query_kind == "m":
				raw = client.get_movie(query_id) or {}
				if isinstance(raw, dict) and int(raw.get("id") or 0) == query_id:
					result = _movie_result_from_tmdb_raw(raw)
					result["from_direct_lookup"] = True
					entities["movies"] = [result]
		else:
			entities = _tmdb_entity_search(query, limit=MAX_SEARCH_RESULTS)
	except Exception:
		# Keep the page usable if TMDb is unavailable.
		pass

	people_results = entities.get("people") or []
	company_results = entities.get("companies") or []
	movie_results = entities.get("movies") or []

	# Fast local "Following" results (DB only), independent from TMDb API fetch.
	# Build filter to match all words in query (any order) in person name.
	people_q = Q(user=request.user)
	for word in query_norm.split():
		people_q &= Q(person__name__icontains=word)
	follow_people_qs = PersonFollow.objects.select_related("person").filter(people_q).order_by("person__name", "role")
	follow_people_qs = follow_people_qs.defer("person__tmdb_raw", "person__tmdb_credits_raw")
	seen_people: set[int] = set()
	for f in follow_people_qs:
		person = getattr(f, "person", None)
		pid = int(getattr(person, "tmdb_id", 0) or 0)
		if not person or pid <= 0 or pid in seen_people:
			continue
		seen_people.add(pid)
		known_for_department = get_person_known_for_department(person)
		if not known_for_department:
			known_for_department = str(getattr(f, "role", "") or "").strip()
		following_people_results.append(
			{
				"id": pid,
				"name": str(getattr(person, "name", "") or pid),
				"profile_path": str(getattr(person, "profile_path", "") or ""),
				"known_for_department": known_for_department,
				"url": reverse("person_detail", args=[pid]),
			}
		)
		if len(following_people_results) >= 10:
			break

	# Build filter to match all words in query (any order) in company name.
	company_q = Q(user=request.user)
	for word in query_norm.split():
		company_q &= Q(company__name__icontains=word)
	follow_company_qs = CompanyFollow.objects.select_related("company").defer("company__tmdb_raw").filter(company_q).order_by("company__name")
	seen_companies: set[int] = set()
	for f in follow_company_qs:
		company = getattr(f, "company", None)
		cid = int(getattr(company, "tmdb_id", 0) or 0)
		if not company or cid <= 0 or cid in seen_companies:
			continue
		seen_companies.add(cid)
		following_company_results.append(
			{
				"id": cid,
				"name": str(getattr(company, "name", "") or cid),
				"logo_path": str(getattr(company, "logo_path", "") or ""),
				"url": reverse("company_detail", args=[cid]),
			}
		)
		if len(following_company_results) >= 10:
			break

	following_count = len(following_people_results) + len(following_company_results)

	# Cache computed payload.
	try:
		cache.set(
			page_cache_key,
			{
				"user_results": user_results,
				"people_results": people_results,
				"company_results": company_results,
				"movie_results": movie_results,
				"following_people_results": following_people_results,
				"following_company_results": following_company_results,
				"following_count": following_count,
				"error": error,
			},
			timeout=10 * 60,
		)
	except Exception:
		pass

	return render(
		request,
		"catalog/search.html",
		{
			"q": query,
			"user_results": user_results,
			"people_results": people_results,
			"company_results": company_results,
			"movie_results": movie_results,
			"following_people_results": following_people_results,
			"following_company_results": following_company_results,
			"following_count": following_count,
			"error": error,
		},
	)
