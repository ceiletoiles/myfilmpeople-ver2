from __future__ import annotations

from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from ..models import CompanyFollow
from ..services import (
	COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE,
	COMPANY_FILMOGRAPHY_SORT_BY,
	get_or_sync_company,
	get_or_sync_company_filmography_page,
	get_or_sync_company_tba_movies_page,
)
from ..tmdb import TMDbClient
from ._shared import _countdown_text, _parse_iso_date


@login_required
def company_detail(request: HttpRequest, tmdb_id: int) -> HttpResponse:
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
		discover_page = (
			get_or_sync_company_filmography_page(company, page=page)
			if filmography_mode == "filmography"
			else {}
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
		movies = [m for m in list(movies_all) if isinstance(m, dict)]
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
				discover_page = get_or_sync_company_filmography_page(company, page=page)
				movies_all = discover_page.get("results") or []
				movies = [m for m in list(movies_all) if isinstance(m, dict)]
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
				movies = [m for m in list(movies_all) if isinstance(m, dict)]
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
	# Default to True (optimistic) so we don't accidentally hide Upcoming when state is uncertain.
	has_tba = True
	if filmography_mode == "upcoming":
		try:
			# If we're already in the upcoming branch we computed tba_count above.
			if 'tba_count' in locals() and isinstance(tba_count, int):
				has_tba = tba_count > 0
			else:
				# Unknown — keep optimistic
				has_tba = True
		except Exception:
			has_tba = True
	else:
		# Not in upcoming mode: for followed companies check cached tba_movies or tba_scan_meta;
		# only set False when a completed scan shows zero results. For not-followed keep optimistic.
		if isinstance(getattr(company, "tmdb_raw", None), dict):
			tmdb_raw = company.tmdb_raw or {}
			tba_movies_raw = tmdb_raw.get("tba_movies")
			if isinstance(tba_movies_raw, list):
				# If list exists and is empty -> definite false, otherwise true if non-empty
				has_tba = any(isinstance(m, dict) for m in tba_movies_raw)
			else:
				# Check scan metadata: if scan_complete and discovered count is 0 -> False
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
						# Scan complete — if tba_movies not present, assume zero
						has_tba = False
					else:
						has_tba = True
				else:
					has_tba = True
		else:
			# Not followed and no cached data: optimistic true to avoid hiding Upcoming
			has_tba = True

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
			"is_followed": is_followed,
			"note_text": note_text,
		},
	)
