from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.conf import settings

from ..models import CompanyFollow
from ..related_links import build_company_related_links
from ..new_movie_helpers import (
	extract_movie_ids_from_filmography,
	extract_movie_release_dates_from_filmography,
	record_new_movie_arrivals,
)
from ..services import (
	COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE,
	COMPANY_FILMOGRAPHY_SORT_BY,
	get_or_sync_company,
	get_or_sync_company_filmography_page,
	get_or_sync_company_tba_movies_page,
	hydrate_company_movie_results,
)
from ..tmdb import TMDbClient, TMDbError
from ._shared import _add_years_safe, _countdown_text, _parse_iso_date


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

		upcoming_with_date = 0
		upcoming_no_date = 0
		latest_past_release: date | None = None

		for payload in payloads:
			for movie in (payload.get("results") or []):
				if not isinstance(movie, dict):
					continue
				release_value = movie.get("release_date") or movie.get("year")
				release_date_str = str(release_value or "").strip()
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

	def _safe_get_or_sync_company_filmography_page(company, page: int) -> dict:
		try:
			return get_or_sync_company_filmography_page(company, page=page)
		except TMDbError:
			return {}

	mode = (request.GET.get("mode") or "").strip().lower()
	filmography_mode = "upcoming" if mode in {"upcoming", "tba"} else "filmography"

	page_str = (request.GET.get("page") or "1").strip()
	try:
		page = int(page_str)
	except ValueError:
		page = 1
	if page < 1:
		page = 1

	follow = CompanyFollow.objects.select_related("company").filter(
		user=request.user, company__tmdb_id=tmdb_id
	).first()
	is_followed = bool(follow)
	note_text = follow.notes if follow else ""

	if follow:
		# Followed => store + serve from DB (refresh if stale).
		company = get_or_sync_company(tmdb_id)
		# Keep denormalized follow snapshot fresh.
		CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).update(name=company.name)

		old_last_sync_at = getattr(company, "tmdb_last_sync_at", None)
		old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
		old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)
		old_pages = old_tmdb_raw.get("discover_movies_pages")
		old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0

		discover_page = _safe_get_or_sync_company_filmography_page(company, page) if filmography_mode == "filmography" else {}

		# If this request refreshed cached filmography via TTL, record any new arrivals.
		new_last_sync_at = getattr(company, "tmdb_last_sync_at", None)
		source = (getattr(company, "tmdb_last_sync_source", "") or "").strip().lower()
		if (
			filmography_mode == "filmography"
			and old_baseline_present
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
			discover_page = (
				client.discover_movies_by_company(
					tmdb_id,
					page=page,
					sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
					extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
				)
				if filmography_mode == "filmography"
				else {}
			)
		except Exception as exc:  # noqa: BLE001
			messages.error(request, f"TMDb error: {exc}")
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

	filmography_items: list[dict] = []
	prev_page = page - 1
	next_page = page + 1
	has_prev = page > 1
	has_next = False
	total_pages: int | None = None
	total_results: int | None = None
	upcoming_total_pages: int | None = None
	upcoming_total_pages_plus = False

	if filmography_mode == "filmography":
		# Dated filmography: query already excludes null dates.
		movies_all = discover_page.get("results") or []
		today = timezone.now().date()
		movies = hydrate_company_movie_results([m for m in list(movies_all) if isinstance(m, dict)])
		for m in movies:
			release_dt = _parse_iso_date(str(m.get("release_date") or ""))
			m["countdown_text"] = (
				_countdown_text(today=today, release_dt=release_dt)
				if release_dt is not None
				else ""
			)
		filmography_items = movies
		total_pages = int(discover_page.get("total_pages") or 1)
		total_results = int(discover_page.get("total_results") or 0)

		# If user requested a page beyond TMDb totals, refetch the last page.
		if page > total_pages and total_pages >= 1:
			page = total_pages
			has_prev = page > 1
			prev_page = page - 1
			next_page = page + 1
			if follow:
				discover_page = _safe_get_or_sync_company_filmography_page(company, page)
				movies_all = discover_page.get("results") or []
				movies = hydrate_company_movie_results([m for m in list(movies_all) if isinstance(m, dict)])
				for m in movies:
					release_dt = _parse_iso_date(str(m.get("release_date") or ""))
					m["countdown_text"] = (
						_countdown_text(today=today, release_dt=release_dt)
						if release_dt is not None
						else ""
					)
				filmography_items = movies
			else:
				client = TMDbClient.from_settings()
				discover_page = client.discover_movies_by_company(
					tmdb_id,
					page=page,
					sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
					extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
				)
				movies_all = discover_page.get("results") or []
				movies = hydrate_company_movie_results([m for m in list(movies_all) if isinstance(m, dict)])
				for m in movies:
					release_dt = _parse_iso_date(str(m.get("release_date") or ""))
					m["countdown_text"] = (
						_countdown_text(today=today, release_dt=release_dt)
						if release_dt is not None
						else ""
					)
				filmography_items = movies
				total_pages = int(discover_page.get("total_pages") or total_pages or 1)
				total_results = int(discover_page.get("total_results") or total_results or 0)

		has_next = page < (total_pages or 1)
	else:
		# Upcoming (TBA): browse missing-release-date titles with a normal pager.
		page_size = 20
		if follow:
			filmography_items, has_prev, has_next = get_or_sync_company_tba_movies_page(
				company,
				page=page,
				page_size=page_size,
			)
			tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			tba_movies_raw = tmdb_raw.get("tba_movies")
			tba_movies = (
				[m for m in tba_movies_raw if isinstance(m, dict)]
				if isinstance(tba_movies_raw, list)
				else []
			)
			tba_count = len(tba_movies)
			tba_scan_meta = tmdb_raw.get("tba_scan_meta")
			if not isinstance(tba_scan_meta, dict):
				tba_scan_meta = {}
			scan_page = int(tba_scan_meta.get("scan_page") or 0)
			discover_total_pages = tba_scan_meta.get("discover_total_pages")
			try:
				discover_total_pages_int = (
					int(discover_total_pages) if discover_total_pages is not None else None
				)
			except (TypeError, ValueError):
				discover_total_pages_int = None
			scan_complete = (
				discover_total_pages_int is not None and scan_page >= discover_total_pages_int
			)
			upcoming_total_pages = max(1, (tba_count + page_size - 1) // page_size)
			upcoming_total_pages_plus = not scan_complete

			# If scan is complete, clamp out-of-range requests.
			if scan_complete and page > upcoming_total_pages:
				page = upcoming_total_pages
				prev_page = page - 1
				next_page = page + 1
				has_prev = page > 1
				filmography_items, has_prev, has_next = get_or_sync_company_tba_movies_page(
					company,
					page=page,
					page_size=page_size,
				)
		else:
			client = TMDbClient.from_settings()
			dedup: dict[int, dict] = {}
			desired_end = page * page_size
			scan_page = 0
			discover_total_pages: int | None = None
			while len(dedup) < desired_end:
				if discover_total_pages is not None and scan_page >= discover_total_pages:
					break
				if scan_page >= 500:
					break
				scan_page += 1
				payload = client.discover_movies_by_company(
					tmdb_id,
					page=scan_page,
					sort_by="popularity.desc",
				)
				if discover_total_pages is None:
					try:
						discover_total_pages = int(payload.get("total_pages") or 0) or None
					except (TypeError, ValueError):
						discover_total_pages = None
				results = payload.get("results") or []
				if not results:
					if discover_total_pages is None:
						discover_total_pages = scan_page
					break
				for m in results:
					if not isinstance(m, dict):
						continue
					if (m.get("release_date") or "").strip():
						continue
					mid = m.get("id")
					if not isinstance(mid, int) or mid in dedup:
						continue
					dedup[mid] = m
					if len(dedup) >= desired_end:
						break
			tba_movies = list(dedup.values())
			scan_complete = (
				discover_total_pages is not None and scan_page >= int(discover_total_pages)
			)
			tba_count = len(tba_movies)
			upcoming_total_pages = max(1, (tba_count + page_size - 1) // page_size)
			upcoming_total_pages_plus = not scan_complete
			if scan_complete and page > upcoming_total_pages:
				page = upcoming_total_pages
			start = (page - 1) * page_size
			end = start + page_size
			filmography_items = tba_movies[start:end]
			has_prev = page > 1
			has_next = len(tba_movies) > end or (
				discover_total_pages is None or scan_page < discover_total_pages
			)
	# Determine whether the company has any TBA (upcoming-without-date) titles.
	# Show the Upcoming toggle only when we can confirm TBA titles exist.
	def _live_tba_scan(max_pages: int) -> bool:
		try:
			client = TMDbClient.from_settings()
			for scan_page in range(1, max_pages + 1):
				payload = client.discover_movies_by_company(tmdb_id, page=scan_page, sort_by="popularity.desc")
				results = payload.get("results") or []
				if any(isinstance(m, dict) and not (m.get("release_date") or "").strip() for m in results):
					return True
				try:
					total_pages = int(payload.get("total_pages") or 0)
				except (TypeError, ValueError):
					total_pages = 0
				if total_pages and scan_page >= total_pages:
					break
			return False
		except Exception:
			# On errors, assume no TBA (user requested strict behavior).
			return False

	has_tba = False
	max_scan = getattr(settings, "TMDB_COMPANY_TBA_LIVE_SCAN_PAGES", 5)
	try:
		max_scan_int = max(1, int(max_scan))
	except (TypeError, ValueError):
		max_scan_int = 5

	if isinstance(getattr(company, "tmdb_raw", None), dict):
		tmdb_raw = company.tmdb_raw or {}
		tba_movies_raw = tmdb_raw.get("tba_movies")
		if isinstance(tba_movies_raw, list) and any(isinstance(m, dict) for m in tba_movies_raw):
			has_tba = True
		else:
			tba_scan_meta = tmdb_raw.get("tba_scan_meta")
			if isinstance(tba_scan_meta, dict):
				scan_page = int(tba_scan_meta.get("scan_page") or 0)
				discover_total_pages = tba_scan_meta.get("discover_total_pages")
				try:
					discover_total_pages_int = (
						int(discover_total_pages) if discover_total_pages is not None else None
					)
				except (TypeError, ValueError):
					discover_total_pages_int = None
				if discover_total_pages_int is not None and scan_page >= discover_total_pages_int:
					# Completed scan; if no cached TBA items, assume none.
					has_tba = isinstance(tba_movies_raw, list) and any(isinstance(m, dict) for m in tba_movies_raw)
				else:
					# Scan incomplete — perform a short live scan to confirm.
					has_tba = _live_tba_scan(max_scan_int)
			else:
				# No scan metadata — perform a short live scan to confirm.
				has_tba = _live_tba_scan(max_scan_int)
	else:
		# No cached company data — perform a short live scan to detect TBA titles.
		has_tba = _live_tba_scan(max_scan_int)

	company_status_label = ""
	if is_followed:
		if isinstance(company.tmdb_raw, dict) and company.tmdb_raw.get("discover_movies_pages"):
			company_status_label = _get_company_status_label(company=company, has_tba_hint=has_tba)
		else:
			fallback_results = filmography_items if filmography_items else None
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
			"filmography_mode": filmography_mode,
			"filmography_items": filmography_items,
			"has_prev": has_prev,
			"has_next": has_next,
			"prev_page": prev_page,
			"next_page": next_page,
			"page": page,
			"total_pages": total_pages,
			"total_results": total_results,
			"upcoming_total_pages": upcoming_total_pages,
			"upcoming_total_pages_plus": upcoming_total_pages_plus,
			"company_status_label": company_status_label,
			"is_followed": is_followed,
			"note_text": note_text,
			"related_links": related_links,
			"alternative_names": alternative_names,
		},
	)
