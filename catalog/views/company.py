from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import Company, CompanyFollow
from ..related_links import build_company_related_links
from ..new_movie_helpers import (
	extract_movie_ids_from_filmography,
	extract_movie_release_dates_from_filmography,
	record_new_movie_arrivals,
)
from ..services import (
	COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE,
	COMPANY_FILMOGRAPHY_SORT_BY,
	COMPANY_TBA_SORT_BY,
	get_or_sync_company_images,
	get_or_sync_company,
	get_or_sync_company_filmography_page,
	hydrate_company_movie_results,
)
from ..rate_limit import rate_limit
from ..tmdb import TMDbClient, TMDbError, tmdb_image_url
from ._shared import _add_years_safe, _parse_iso_date


def _safe_get_or_sync_company_filmography_page(company, page: int) -> dict:
	try:
		return get_or_sync_company_filmography_page(company, page=page)
	except TMDbError:
		return {}


def _company_logo_images_return_to(request: HttpRequest, tmdb_id: int) -> str:
	return_to = (request.GET.get("return_to") or request.POST.get("return_to") or "").strip()
	fallback = reverse("company_detail", args=[tmdb_id])
	if return_to and url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
		return return_to
	return fallback


def _company_logo_candidates(company: Company) -> list[dict[str, object]]:
	raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
	images = raw.get("images") if isinstance(raw.get("images"), dict) else {}
	logos = images.get("logos") if isinstance(images, dict) else []
	if not isinstance(logos, list):
		return []

	results: list[dict[str, object]] = []
	priority = {".svg": 0, ".png": 1}
	seen_paths: set[str] = set()
	for logo in logos:
		if not isinstance(logo, dict):
			continue
		file_path = str(logo.get("file_path") or "").strip()
		lower_path = file_path.lower()
		extension = ".svg" if lower_path.endswith(".svg") else ".png" if lower_path.endswith(".png") else ""
		if not file_path or file_path in seen_paths or not extension:
			continue
		seen_paths.add(file_path)
		results.append(
			{
				"file_path": file_path,
				"format": extension.lstrip("."),
				"sort_order": priority.get(extension, 99),
				"width": logo.get("width"),
				"height": logo.get("height"),
				"vote_average": logo.get("vote_average"),
				"vote_count": logo.get("vote_count"),
				"url": tmdb_image_url(file_path, size="original"),
			}
		)
	return sorted(results, key=lambda item: (int(item.get("sort_order") or 99), -int(item.get("vote_count") or 0)))


_FILMOGRAPHY_PAGE_SIZE = 20
_FILMOGRAPHY_CACHE_TTL_SECONDS = 30 * 60


def _company_filmography_tab_key(tab: str | None) -> str:
	value = (tab or "").strip().lower()
	if value in {"upcoming", "tba", "released"}:
		return value
	return "released"


def _company_filmography_source_key(tab: str) -> str:
	return "tba" if tab == "tba" else "filmography"


def _company_filmography_cache_key(tmdb_id: int, source: str, page: int) -> str:
	return f"company:filmography:v1:{int(tmdb_id)}:{source}:page:{int(page)}"


def _company_parse_date(value: object) -> date | None:
	parsed = _parse_iso_date(str(value or "").strip())
	return parsed if isinstance(parsed, date) else None


def _company_movie_display_year(movie: dict[str, object], release_dt: date | None) -> str:
	if release_dt is not None:
		return str(release_dt.year)
	release_date = str(movie.get("release_date") or "").strip()
	if len(release_date) >= 4 and release_date[:4].isdigit():
		return release_date[:4]
	return ""


def _company_normalize_movie(movie: dict[str, object], *, release_dt: date | None = None) -> dict[str, object]:
	mid = movie.get("id")
	if not isinstance(mid, int):
		return {}
	title = str(movie.get("title") or movie.get("name") or "").strip()
	poster_path = str(movie.get("poster_path") or "").strip()
	release_date = str(movie.get("release_date") or "").strip()
	if release_dt is None and release_date:
		release_dt = _company_parse_date(release_date)
	return {
		"id": mid,
		"title": title or str(mid),
		"poster_path": poster_path,
		"release_date": release_date,
		"release_dt": release_dt,
		"year": _company_movie_display_year(movie, release_dt),
	}


def _company_sort_filmography_items(items: list[dict[str, object]], *, tab: str) -> list[dict[str, object]]:
	def _sort_key(item: dict[str, object]):
		release_dt = item.get("release_dt")
		release_dt = release_dt if isinstance(release_dt, date) else None
		title = str(item.get("title") or "").lower()
		if tab == "upcoming":
			if release_dt is None:
				return (1, 0, title)
			return (0, release_dt.toordinal(), title)
		if release_dt is None:
			return (1, 0, title)
		return (0, -release_dt.toordinal(), title)

	return sorted(items, key=_sort_key)


def _company_filter_movie_by_tab(movie: dict[str, object], *, tab: str, today: date) -> bool:
	release_dt = movie.get("release_dt")
	release_dt = release_dt if isinstance(release_dt, date) else None
	if tab == "released":
		return release_dt is not None and release_dt <= today
	if tab == "upcoming":
		return release_dt is not None and release_dt > today
	return False


def _company_filmography_page_payload(
	company_id: int,
	*,
	tab: str,
	page: int,
	company: Company | None = None,
	followed: bool = False,
	allow_db_cache: bool = True,
) -> dict[str, object]:
	"""Load one raw TMDb filmography page without mutating the DB while scrolling."""
	tab = _company_filmography_tab_key(tab)
	source = _company_filmography_source_key(tab)
	page = max(1, int(page or 1))
	cache_key = _company_filmography_cache_key(company_id, source, page)

	try:
		cached = cache.get(cache_key)
		if isinstance(cached, dict) and cached.get("items"):
			return cached
	except Exception:
		pass

	if source == "filmography":
		if followed and allow_db_cache and page == 1 and company is not None:
			raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			pages = raw.get("discover_movies_pages")
			if isinstance(pages, dict):
				page_payload = pages.get("1")
				if isinstance(page_payload, dict):
					results = [
						_company_normalize_movie(movie)
						for movie in (page_payload.get("results") or [])
						if isinstance(movie, dict)
					]
					results = [movie for movie in results if movie]
					if any(
						not str(movie.get("title") or "").strip() or not str(movie.get("poster_path") or "").strip()
						for movie in results
					):
						try:
							results = hydrate_company_movie_results(results)
						except Exception:
							pass
					payload = {
						"source": "filmography",
						"page": 1,
						"items": results,
						"total_pages": int(page_payload.get("total_pages") or raw.get("discover_movies_meta", {}).get("total_pages") or 1),
						"total_results": int(page_payload.get("total_results") or raw.get("discover_movies_meta", {}).get("total_results") or len(results)),
						"has_more": int(raw.get("discover_movies_meta", {}).get("total_pages") or 1) > 1,
					}
					try:
						cache.set(cache_key, payload, _FILMOGRAPHY_CACHE_TTL_SECONDS)
					except Exception:
						pass
					return payload

		client = TMDbClient.from_settings()
		payload = client.discover_movies_by_company(
			company_id,
			page=page,
			sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
			extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
		)
		results = [
			_company_normalize_movie(movie)
			for movie in (payload.get("results") or [])
			if isinstance(movie, dict)
		]
		results = [movie for movie in results if movie]
		if any(
			not str(movie.get("title") or "").strip() or not str(movie.get("poster_path") or "").strip()
			for movie in results
		):
			try:
				results = hydrate_company_movie_results(results)
			except Exception:
				pass
		normalized = {
			"source": "filmography",
			"page": page,
			"items": results,
			"total_pages": int(payload.get("total_pages") or 1),
			"total_results": int(payload.get("total_results") or len(results)),
			"has_more": page < int(payload.get("total_pages") or 1),
		}
		try:
			cache.set(cache_key, normalized, _FILMOGRAPHY_CACHE_TTL_SECONDS)
		except Exception:
			pass
		return normalized

	client = TMDbClient.from_settings()
	payload = client.discover_movies_by_company(
		company_id,
		page=page,
		sort_by=COMPANY_TBA_SORT_BY,
	)
	page_items = [
		movie
		for movie in (
			_company_normalize_movie(movie)
			for movie in (payload.get("results") or [])
			if isinstance(movie, dict)
		)
		if movie and not _company_parse_date(movie.get("release_date"))
	]
	normalized = {
		"source": "tba",
		"page": page,
		"items": page_items,
		"total_pages": int(payload.get("total_pages") or 1),
		"total_results": int(payload.get("total_results") or len(page_items)),
		"has_more": page < int(payload.get("total_pages") or 1),
	}
	try:
		cache.set(cache_key, normalized, _FILMOGRAPHY_CACHE_TTL_SECONDS)
	except Exception:
		pass
	return normalized


def _company_filmography_initial_state(
	request: HttpRequest,
	company: Company,
	*,
	active_tab: str,
	followed: bool,
) -> dict[str, object]:
	tab = _company_filmography_tab_key(active_tab)
	raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
	source = _company_filmography_source_key(tab)
	if followed:
		if source == "filmography":
			cached_pages = raw.get("discover_movies_pages")
			has_cached_page_1 = isinstance(cached_pages, dict) and isinstance(cached_pages.get("1"), dict)
			page_payload = (
				_company_filmography_page_payload(
					company.tmdb_id,
					tab=tab,
					page=1,
					company=company,
					followed=followed,
					allow_db_cache=True,
				)
				if has_cached_page_1
				else {
					"source": source,
					"page": 1,
					"items": [],
					"has_more": True,
					"total_pages": None,
					"total_results": 0,
				}
			)
		else:
			page_payload = _company_filmography_page_payload(
				company.tmdb_id,
				tab=tab,
				page=1,
				company=company,
				followed=followed,
				allow_db_cache=False,
			)
	else:
		page_payload = _company_filmography_page_payload(
			company.tmdb_id,
			tab=tab,
			page=1,
			company=company,
			followed=followed,
			allow_db_cache=True,
		)
	if tab == "tba":
		source_items = [
			item for item in (page_payload.get("items") or []) if isinstance(item, dict)
		]
		items = list(source_items)
	else:
		today = timezone.now().date()
		source_items = [
			item for item in (page_payload.get("items") or []) if isinstance(item, dict)
		]
		items = [
			item for item in source_items if _company_filter_movie_by_tab(item, tab=tab, today=today)
		]
		items = _company_sort_filmography_items(items, tab=tab)

	initial_items = [
		{
			"id": int(item.get("id") or 0),
			"title": str(item.get("title") or item.get("name") or item.get("id") or "-"),
			"poster_path": str(item.get("poster_path") or ""),
			"release_date": str(item.get("release_date") or ""),
			"year": str(item.get("year") or ""),
		}
		for item in items
		if isinstance(item, dict) and isinstance(item.get("id"), int)
	]

	initial_title = {
		"released": "Released",
		"upcoming": "Upcoming",
		"tba": "TBA",
	}[tab]

	return {
		"active_tab": tab,
		"active_title": initial_title,
		"source_items": [
			{
				"id": int(item.get("id") or 0),
				"title": str(item.get("title") or item.get("name") or item.get("id") or "-"),
				"poster_path": str(item.get("poster_path") or ""),
				"release_date": str(item.get("release_date") or ""),
				"year": str(item.get("year") or ""),
			}
			for item in source_items
			if isinstance(item, dict) and isinstance(item.get("id"), int)
		],
		"items": initial_items,
		"has_more": bool(page_payload.get("has_more")),
		"page": int(page_payload.get("page") or 1),
		"total_pages": page_payload.get("total_pages"),
		"total_results": page_payload.get("total_results"),
		"source": page_payload.get("source"),
	}


def _company_filmography_page_json(
	request: HttpRequest,
	company: Company,
	*,
	tab: str,
	page: int,
	followed: bool,
) -> JsonResponse:
	payload = _company_filmography_page_payload(
		company.tmdb_id,
		tab=tab,
		page=page,
		company=company,
		followed=followed,
		allow_db_cache=True,
	)
	items = [
		{
			"id": int(item.get("id") or 0),
			"title": str(item.get("title") or item.get("name") or item.get("id") or "-"),
			"poster_path": str(item.get("poster_path") or ""),
			"release_date": str(item.get("release_date") or ""),
			"year": str(item.get("year") or ""),
		}
		for item in (payload.get("items") or [])
		if isinstance(item, dict) and isinstance(item.get("id"), int)
	]
	return JsonResponse(
		{
			"ok": True,
			"tab": _company_filmography_tab_key(tab),
			"page": int(payload.get("page") or page),
			"source": payload.get("source"),
			"items": items,
			"has_more": bool(payload.get("has_more")),
			"total_pages": payload.get("total_pages"),
			"total_results": payload.get("total_results"),
		}
	)


@rate_limit(limit=25, window_seconds=60, bucket_name="company_detail")
@login_required
def company_detail(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	def _get_company_status_label(*, company, fallback_results: list[dict] | None = None, has_tba_hint: bool = False) -> str:
		today = timezone.now().date()
		ten_years_ago = _add_years_safe(today, -10)
		tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		payloads: list[dict] = []
		pages = tmdb_raw.get("discover_movies_pages")
		if isinstance(pages, dict):
			payloads = [payload for payload in pages.values() if isinstance(payload, dict)]
		elif fallback_results is not None:
			payloads = [{"results": fallback_results}]
		tba_movies = tmdb_raw.get("tba_movies")
		if isinstance(tba_movies, list) and any(isinstance(movie, dict) for movie in tba_movies):
			payloads.append({"results": [movie for movie in tba_movies if isinstance(movie, dict)]})

		upcoming_with_date = 0
		upcoming_no_date = 0
		latest_past_release: date | None = None

		for payload in payloads:
			for movie in (payload.get("results") or []):
				if not isinstance(movie, dict):
					continue
				release_date_str = str(movie.get("release_date") or movie.get("year") or "").strip()
				release_dt = _parse_iso_date(release_date_str)
				if release_dt is not None and release_dt > today:
					upcoming_with_date += 1
				elif not release_date_str:
					upcoming_no_date += 1
				elif release_dt is not None and release_dt <= today:
					if latest_past_release is None or release_dt > latest_past_release:
						latest_past_release = release_dt

		if upcoming_with_date > 0:
			return "Upcoming"
		if upcoming_no_date > 0 or has_tba_hint:
			return "Announced"
		if latest_past_release is not None and latest_past_release < ten_years_ago:
			return "Inactive"
		return "Idle"

	follow = CompanyFollow.objects.select_related("company").filter(
		user=request.user, company__tmdb_id=tmdb_id
	).first()
	is_followed = bool(follow)
	note_text = follow.notes if follow else ""

	if follow:
		# Followed => store + serve from DB (refresh if stale).
		try:
			company = get_or_sync_company(tmdb_id)
		except TMDbError:
			messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
			return redirect("search")
		# Keep denormalized follow snapshot fresh.
		CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).update(name=company.name)

		old_last_sync_at = getattr(company, "tmdb_last_sync_at", None)
		old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
		old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)
		old_pages = old_tmdb_raw.get("discover_movies_pages")
		old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0

		# If this request refreshed cached filmography via TTL, record any new arrivals.
		new_last_sync_at = getattr(company, "tmdb_last_sync_at", None)
		source = (getattr(company, "tmdb_last_sync_source", "") or "").strip().lower()
		if (
			old_baseline_present
			and old_last_sync_at is not None
			and new_last_sync_at is not None
			and new_last_sync_at != old_last_sync_at
			and source == "ttl"
		):
			new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw)
			new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw)
			# Add a sensible default credit label for company arrivals.
			company_event_meta: dict[int, dict] = {}
			pages = new_tmdb_raw.get("discover_movies_pages") or {}
			if isinstance(pages, dict):
				for payload in pages.values():
					if not isinstance(payload, dict):
						continue
					for movie in payload.get("results", []) or []:
						if not isinstance(movie, dict):
							continue
						mid = movie.get("id")
						if not isinstance(mid, int):
							continue
						company_event_meta.setdefault(mid, {})["credit_job"] = "Production Company"

			record_new_movie_arrivals(
				user=request.user,
				source_type="company",
				source_id=tmdb_id,
				source_name=company.name,
				old_movie_ids=old_movie_ids,
				new_movie_ids=new_movie_ids,
				role="studio",
				old_release_dates=old_release_dates,
				new_release_dates=new_release_dates,
				new_event_meta_by_movie=company_event_meta,
				source_last_sync_at=getattr(company, "tmdb_last_sync_at", None),
			)
	else:
		# Not followed => live fetch only (do not store in DB).
		client = TMDbClient.from_settings()
		try:
			raw = client.get_company(tmdb_id)
		except Exception:  # noqa: BLE001
			messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
			return redirect("search")

		if isinstance(raw, dict):
			raw = {**raw}
		else:
			raw = {}

		company = SimpleNamespace(
			tmdb_id=tmdb_id,
			name=(raw.get("name") or str(tmdb_id)),
			logo_path=(raw.get("logo_path") or ""),
			tmdb_raw=raw,
			tmdb_last_sync_at=None,
		)

	related_links = build_company_related_links(tmdb_id, company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {})
	raw_company = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
	alternative_names_payload = raw_company.get("alternative_names") if isinstance(raw_company, dict) else {}
	alternative_names: list[str] = []
	if isinstance(alternative_names_payload, dict):
		items = alternative_names_payload.get("results") or []
		if isinstance(items, list):
			seen_names: set[str] = set()
			for item in items:
				if not isinstance(item, dict):
					continue
				name = (item.get("name") or item.get("title") or "").strip()
				if not name or name in seen_names:
					continue
				seen_names.add(name)
				alternative_names.append(name)

	active_tab = _company_filmography_tab_key(request.GET.get("tab") or request.GET.get("mode"))
	if request.GET.get("mode") == "upcoming" and not request.GET.get("tab"):
		active_tab = "tba"
	filmography_initial_state = _company_filmography_initial_state(
		request,
		company,
		active_tab=active_tab,
		followed=is_followed,
	)
	filmography_initial_items = list(filmography_initial_state.get("items") or [])
	filmography_page_url = reverse("company_filmography_page", args=[tmdb_id])
	has_tba = False
	tba_movies_raw = raw_company.get("tba_movies")
	if isinstance(tba_movies_raw, list) and any(isinstance(movie, dict) for movie in tba_movies_raw):
		has_tba = True
	elif active_tab == "tba" and filmography_initial_items:
		has_tba = True

	company_status_label = ""
	if is_followed:
		if isinstance(company.tmdb_raw, dict) and company.tmdb_raw.get("discover_movies_pages"):
			company_status_label = _get_company_status_label(company=company, has_tba_hint=has_tba)
		else:
			fallback_results = filmography_initial_items if filmography_initial_items else None
			company_status_label = _get_company_status_label(
				company=company,
				fallback_results=fallback_results,
				has_tba_hint=has_tba,
			)

	return render(
		request,
		"catalog/company_detail.html",
		{
			"has_tba": has_tba,
			"company": company,
			"filmography_active_tab": active_tab,
			"filmography_initial_state": filmography_initial_state,
			"filmography_initial_items": filmography_initial_items,
			"filmography_page_url": filmography_page_url,
			"company_status_label": company_status_label,
			"is_followed": is_followed,
			"note_text": note_text,
			"related_links": related_links,
			"alternative_names": alternative_names,
		},
	)


@login_required
@rate_limit(limit=60, window_seconds=60, bucket_name="company_detail_page")
def company_filmography_page(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	tab = _company_filmography_tab_key(request.GET.get("tab") or request.GET.get("mode"))
	if request.GET.get("mode") == "upcoming" and not request.GET.get("tab"):
		tab = "tba"

	page_str = (request.GET.get("page") or "1").strip()
	try:
		page = max(1, int(page_str))
	except ValueError:
		page = 1

	follow = CompanyFollow.objects.select_related("company").filter(
		user=request.user, company__tmdb_id=tmdb_id
	).first()
	followed = bool(follow)

	if follow:
		company = follow.company
	else:
		company = Company.objects.filter(tmdb_id=tmdb_id).only("tmdb_id", "tmdb_raw", "name").first()
		if company is None:
			client = TMDbClient.from_settings()
			try:
				raw = client.get_company(tmdb_id)
			except Exception:  # noqa: BLE001
				return JsonResponse({"ok": False, "error": "TMDb data is temporarily unavailable."}, status=503)
			company = SimpleNamespace(
				tmdb_id=tmdb_id,
				name=(raw.get("name") or str(tmdb_id)),
				tmdb_raw=raw if isinstance(raw, dict) else {},
			)

	return _company_filmography_page_json(
		request,
		company,
		tab=tab,
		page=page,
		followed=followed,
	)


@login_required
def company_logo_images(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	return_to = _company_logo_images_return_to(request, tmdb_id)
	is_followed = CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).exists()
	if not is_followed:
		messages.error(request, "Logos are available only for followed companies.")
		return redirect(return_to)

	load_error = ""

	if request.method == "POST":
		try:
			company = get_or_sync_company_images(tmdb_id)
		except Exception:
			messages.error(request, "TMDb company logos are temporarily unavailable. Please try again soon.")
			return redirect(return_to)

		selected_logo_path = (request.POST.get("logo_path") or "").strip()
		candidates = _company_logo_candidates(company)
		allowed_paths = {str(candidate.get("file_path") or "").strip() for candidate in candidates}
		if selected_logo_path not in allowed_paths:
			messages.error(request, "Selected logo is no longer available.")
			return redirect(reverse("company_logo_images", args=[tmdb_id]))

		company.logo_path = selected_logo_path
		company.save(update_fields=["logo_path", "updated_at"])
		try:
			cache.delete(f"db:company:v1:{int(tmdb_id)}")
		except Exception:
			pass
		return redirect(return_to)

	try:
		company = get_or_sync_company_images(tmdb_id)
		logos = _company_logo_candidates(company)
	except Exception:
		load_error = "TMDb company logos are temporarily unavailable right now."
		logos = []
		stored_company = Company.objects.filter(tmdb_id=tmdb_id).first()
		if stored_company is not None:
			company = stored_company
		else:
			try:
				company = get_or_sync_company(tmdb_id)
			except Exception:
				company = SimpleNamespace(
					tmdb_id=tmdb_id,
					name=str(tmdb_id),
					logo_path="",
					tmdb_raw={},
				)

	selected_logo_path = str(getattr(company, "logo_path", "") or "").strip()
	current_logo = next((item for item in logos if item.get("file_path") == selected_logo_path), None)
	current_logo_url = tmdb_image_url(selected_logo_path, size="original") if selected_logo_path else ""

	return render(
		request,
		"catalog/company_logo_images.html",
		{
			"company": company,
			"logos": logos,
			"logo_count": len(logos),
			"current_logo": current_logo,
			"selected_logo_path": selected_logo_path,
			"current_logo_url": current_logo_url,
			"return_to": return_to,
			"load_error": load_error,
		},
	)
