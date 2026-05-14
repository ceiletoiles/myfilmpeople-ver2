from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import Company, Movie, Person
from .tmdb import TMDbClient


# Use release_date (not primary_release_date) for better completeness.
# Some TMDb titles lack a primary_release_date but still have release_date.
COMPANY_FILMOGRAPHY_SORT_BY = "release_date.desc"
COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE = "1800-01-01"
COMPANY_TBA_SORT_BY = "popularity.desc"


def get_or_sync_company_tba_movies(
    company: Company,
    *,
    force: bool = False,
    max_pages: int = 5,
    limit: int = 48,
) -> list[dict[str, Any]]:
    """Fetch and cache a best-effort list of TBA/unknown-release-date movies.

    These are movies returned by TMDb discover that have an empty/missing release_date.
    We fetch a few pages sorted by popularity to surface them early.
    """
    max_pages = int(max_pages or 0)
    if max_pages <= 0:
        max_pages = 1
    limit = int(limit or 0)
    if limit <= 0:
        limit = 24

    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    meta = tmdb_raw.get("tba_movies_meta")
    if not isinstance(meta, dict):
        meta = {}
    meta_ok = (
        meta.get("sort_by") == COMPANY_TBA_SORT_BY
        and int(meta.get("max_pages") or 0) == max_pages
        and int(meta.get("limit") or 0) == limit
    )

    cached = tmdb_raw.get("tba_movies")
    if (
        isinstance(cached, list)
        and meta_ok
        and not force
        and not _is_stale(company.tmdb_last_sync_at)
    ):
        # Ensure dict-only payloads.
        return [m for m in cached if isinstance(m, dict)]

    client = TMDbClient.from_settings()
    dedup: dict[int, dict[str, Any]] = {}
    for p in range(1, max_pages + 1):
        payload = client.discover_movies_by_company(
            company.tmdb_id,
            page=p,
            sort_by=COMPANY_TBA_SORT_BY,
        )
        results = payload.get("results") or []
        if not isinstance(results, list):
            continue
        for m in results:
            if not isinstance(m, dict):
                continue
            if (m.get("release_date") or "").strip():
                continue
            mid = m.get("id")
            if not isinstance(mid, int):
                continue
            dedup[mid] = m
            if len(dedup) >= limit:
                break
        if len(dedup) >= limit:
            break

    tba_movies = list(dedup.values())[:limit]
    company.tmdb_raw = {
        **tmdb_raw,
        "tba_movies": tba_movies,
        "tba_movies_meta": {
            "sort_by": COMPANY_TBA_SORT_BY,
            "max_pages": max_pages,
            "limit": limit,
        },
    }
    company.save(update_fields=["tmdb_raw", "updated_at"])
    return tba_movies


def extract_person_credited_roles(credits: dict) -> list[str]:
    roles: set[str] = set()
    cast_items = credits.get("cast", []) or []
    crew_items = credits.get("crew", []) or []

    if len(cast_items) > 0:
        roles.add("Actor")

    for item in crew_items:
        job = (item.get("job") or "").strip()
        if job:
            roles.add(job)

    preferred = {"director": 0, "actor": 1}
    return sorted(roles, key=lambda r: (preferred.get(r.strip().lower(), 99), r.lower()))


def _ensure_paged_cache(
    raw: dict,
    *,
    pages_key: str,
    meta_key: str,
    page: int,
    payload: dict,
) -> dict:
    pages = raw.get(pages_key)
    if not isinstance(pages, dict):
        pages = {}

    key = str(int(page or 1))
    pages[key] = payload

    meta = raw.get(meta_key)
    if not isinstance(meta, dict):
        meta = {}
    meta["total_pages"] = int(payload.get("total_pages") or meta.get("total_pages") or 1)
    meta["total_results"] = int(payload.get("total_results") or meta.get("total_results") or 0)
    return {**raw, pages_key: pages, meta_key: meta}

def get_or_sync_company_filmography_page(
    company: Company,
    *,
    page: int,
    force: bool = False,
) -> dict:
    page = int(page or 1)
    if page < 1:
        page = 1

    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    pages = tmdb_raw.get("discover_movies_pages")
    if not isinstance(pages, dict):
        pages = {}

    meta = tmdb_raw.get("discover_movies_meta")
    if not isinstance(meta, dict):
        meta = {}
    # If the query shape changes, invalidate cached pages.
    if (
        meta.get("sort_by") != COMPANY_FILMOGRAPHY_SORT_BY
        or meta.get("release_date_gte") != COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE
    ):
        pages = {}
        meta = {k: v for k, v in meta.items() if k not in {"total_pages", "total_results"}}

    key = str(page)
    cached = pages.get(key)
    if isinstance(cached, dict) and not force and not _is_stale(company.tmdb_last_sync_at):
        return cached

    client = TMDbClient.from_settings()
    payload = client.discover_movies_by_company(
        company.tmdb_id,
        page=page,
        sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
        extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
    )
    pages[key] = payload

    meta["total_pages"] = int(payload.get("total_pages") or meta.get("total_pages") or 1)
    meta["total_results"] = int(payload.get("total_results") or meta.get("total_results") or 0)
    meta["sort_by"] = COMPANY_FILMOGRAPHY_SORT_BY
    meta["release_date_gte"] = COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE

    company.tmdb_raw = {**tmdb_raw, "discover_movies_pages": pages, "discover_movies_meta": meta}
    company.tmdb_last_sync_at = timezone.now()
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "updated_at"])
    # Keep the short-lived cache consistent with DB writes.
    try:
        cache.set(f"db:company:v1:{int(company.tmdb_id)}", company, timeout=5 * 60)
    except Exception:
        pass
    return payload


def prefetch_company_filmography(
    company: Company,
    *,
    force: bool = False,
    max_pages: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Prefetch and cache full company filmography into Company.tmdb_raw.

    Uses TMDb discover endpoint (with_companies) because TMDb's
    /company/{id}/movies pagination can be unreliable.

    Returns the number of pages fetched.

    Safety: pass max_pages to cap the number of pages fetched in one call.
    """
    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    pages = tmdb_raw.get("discover_movies_pages")
    if not isinstance(pages, dict):
        pages = {}

    # If not stale and we already have all pages cached, do nothing.
    meta = tmdb_raw.get("discover_movies_meta")
    if not isinstance(meta, dict):
        meta = {}
    # If the query shape changes, invalidate cached pages.
    if (
        meta.get("sort_by") != COMPANY_FILMOGRAPHY_SORT_BY
        or meta.get("release_date_gte") != COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE
    ):
        pages = {}
        meta = {k: v for k, v in meta.items() if k not in {"total_pages", "total_results"}}
    cached_total_pages = meta.get("total_pages")
    if (
        not force
        and not _is_stale(company.tmdb_last_sync_at)
        and isinstance(cached_total_pages, int)
        and cached_total_pages > 0
        and len(pages) >= cached_total_pages
    ):
        return 0

    client = TMDbClient.from_settings()
    first = client.discover_movies_by_company(
        company.tmdb_id,
        page=1,
        sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
        extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
    )
    total_pages = int(first.get("total_pages") or 1)
    pages_to_fetch = total_pages
    if max_pages is not None and max_pages > 0:
        pages_to_fetch = min(pages_to_fetch, int(max_pages))

    pages["1"] = first
    fetched = 1
    try:
        if progress_cb is not None:
            progress_cb(fetched, pages_to_fetch)
    except Exception:
        pass

    for p in range(2, pages_to_fetch + 1):
        key = str(p)
        if not force and key in pages and not _is_stale(company.tmdb_last_sync_at):
            continue
        payload = client.discover_movies_by_company(
            company.tmdb_id,
            page=p,
            sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
            extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
        )
        pages[key] = payload
        fetched += 1
        try:
            if progress_cb is not None:
                progress_cb(fetched, pages_to_fetch)
        except Exception:
            pass

    meta["total_pages"] = total_pages
    meta["total_results"] = int(first.get("total_results") or meta.get("total_results") or 0)
    meta["sort_by"] = COMPANY_FILMOGRAPHY_SORT_BY
    meta["release_date_gte"] = COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE

    company.tmdb_raw = {**tmdb_raw, "discover_movies_pages": pages, "discover_movies_meta": meta}
    company.tmdb_last_sync_at = timezone.now()
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "updated_at"])
    # Keep the short-lived cache consistent with DB writes.
    try:
        cache.set(f"db:company:v1:{int(company.tmdb_id)}", company, timeout=5 * 60)
    except Exception:
        pass
    return fetched


def get_or_sync_company_tba_movies_page(
    company: Company,
    *,
    page: int,
    page_size: int = 20,
    force: bool = False,
    scan_chunk_pages: int = 0,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Return a page of movies with unknown (TBA) release_date.

    We scan TMDb discover pages (popularity.desc) and keep those with missing release_date.
    This is incremental and cached for followed companies.

    Returns (items, has_prev, has_next).
    """
    page = int(page or 1)
    if page < 1:
        page = 1
    page_size = int(page_size or 20)
    if page_size <= 0:
        page_size = 20
    scan_chunk_pages = int(scan_chunk_pages or 0)
    # If scan_chunk_pages <= 0, scan until the requested page is filled
    # (or until TMDb runs out of pages).
    max_scan_pages = scan_chunk_pages if scan_chunk_pages > 0 else None

    desired_end = page * page_size

    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    meta = tmdb_raw.get("tba_scan_meta")
    if not isinstance(meta, dict):
        meta = {}

    # Reset scan only if cache is stale (or explicitly forced). If scan meta is missing,
    # we still reuse any cached tba_movies and rebuild scan meta incrementally.
    cached = tmdb_raw.get("tba_movies")
    cached_movies = [m for m in cached if isinstance(m, dict)] if isinstance(cached, list) else []

    if force or _is_stale(company.tmdb_last_sync_at):
        meta = {"sort_by": COMPANY_TBA_SORT_BY, "scan_page": 0, "discover_total_pages": None}
        tba_movies: list[dict[str, Any]] = []
    elif meta.get("sort_by") and meta.get("sort_by") != COMPANY_TBA_SORT_BY:
        # Query shape changed; start over.
        meta = {"sort_by": COMPANY_TBA_SORT_BY, "scan_page": 0, "discover_total_pages": None}
        tba_movies = []
    else:
        tba_movies = cached_movies
        if meta.get("sort_by") != COMPANY_TBA_SORT_BY:
            meta = {
                "sort_by": COMPANY_TBA_SORT_BY,
                "scan_page": int(meta.get("scan_page") or 0),
                "discover_total_pages": meta.get("discover_total_pages"),
            }

    # Dedup by id (preserve order-ish).
    dedup: dict[int, dict[str, Any]] = {}
    for m in tba_movies:
        mid = m.get("id")
        if isinstance(mid, int):
            dedup[mid] = m
    if len(dedup) != len(tba_movies):
        tba_movies = list(dedup.values())

    scan_page = int(meta.get("scan_page") or 0)
    discover_total_pages = meta.get("discover_total_pages")
    try:
        discover_total_pages_int = int(discover_total_pages) if discover_total_pages is not None else None
    except (TypeError, ValueError):
        discover_total_pages_int = None

    client = TMDbClient.from_settings()
    fetched_pages = 0
    while len(tba_movies) < desired_end and (max_scan_pages is None or fetched_pages < max_scan_pages):
        if discover_total_pages_int is not None and scan_page >= discover_total_pages_int:
            break
        # TMDb discover endpoints effectively cap at 500 pages.
        if scan_page >= 500:
            break
        scan_page += 1
        payload = client.discover_movies_by_company(
            company.tmdb_id,
            page=scan_page,
            sort_by=COMPANY_TBA_SORT_BY,
        )
        if discover_total_pages_int is None:
            try:
                discover_total_pages_int = int(payload.get("total_pages") or 0) or None
            except (TypeError, ValueError):
                discover_total_pages_int = None
        results = payload.get("results") or []
        if not results:
            # No more data.
            if discover_total_pages_int is None:
                discover_total_pages_int = scan_page
            break
        if isinstance(results, list):
            for m in results:
                if not isinstance(m, dict):
                    continue
                if (m.get("release_date") or "").strip():
                    continue
                mid = m.get("id")
                if not isinstance(mid, int) or mid in dedup:
                    continue
                dedup[mid] = m
                tba_movies.append(m)
                if len(tba_movies) >= desired_end:
                    break
        fetched_pages += 1

    scan_complete = discover_total_pages_int is not None and scan_page >= discover_total_pages_int

    # Persist scan progress.
    company.tmdb_raw = {
        **tmdb_raw,
        "tba_movies": tba_movies,
        "tba_scan_meta": {
            "sort_by": COMPANY_TBA_SORT_BY,
            "scan_page": scan_page,
            "discover_total_pages": discover_total_pages_int,
        },
    }
    company.tmdb_last_sync_at = timezone.now()
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "updated_at"])

    start = (page - 1) * page_size
    end = start + page_size
    items = tba_movies[start:end]
    has_prev = page > 1
    has_next = len(tba_movies) > end or (not scan_complete)
    return items, has_prev, has_next


def _is_stale(last_sync_at) -> bool:
    if not last_sync_at:
        return True
    ttl_hours = getattr(settings, "TMDB_CACHE_TTL_HOURS", 168)
    return last_sync_at < timezone.now() - timedelta(hours=ttl_hours)


def get_or_sync_person(tmdb_id: int, *, force: bool = False) -> Person:
    cache_key = f"db:person:v1:{int(tmdb_id)}"
    if not force:
        try:
            cached = cache.get(cache_key)
            if isinstance(cached, Person):
                if (
                    cached.tmdb_raw
                    and cached.tmdb_credits_raw
                    and not _is_stale(cached.tmdb_last_sync_at)
                ):
                    return cached
        except Exception:
            pass

    person, _ = Person.objects.get_or_create(tmdb_id=tmdb_id, defaults={"name": str(tmdb_id)})

    tmdb_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
    has_credited_roles = isinstance(tmdb_raw.get("credited_roles"), list)

    if force or _is_stale(person.tmdb_last_sync_at) or not person.tmdb_raw or not person.tmdb_credits_raw:
        # If forcing a fresh sync, invalidate any cached TMDb HTTP responses
        # so the client will fetch a fresh payload instead of returning stale cached JSON.
        if force:
            try:
                client = TMDbClient.from_settings()
                try:
                    cache.delete(client.cache_key_for(f"/person/{tmdb_id}"))
                except Exception:
                    pass
                try:
                    cache.delete(client.cache_key_for(f"/person/{tmdb_id}/combined_credits"))
                except Exception:
                    pass
                try:
                    cache.delete(client.cache_key_for(f"/person/{tmdb_id}/images"))
                except Exception:
                    pass
            except Exception:
                client = TMDbClient.from_settings()
        else:
            client = TMDbClient.from_settings()

        raw = client.get_person(tmdb_id)
        credits = client.get_person_credits(tmdb_id)

        credited_roles = extract_person_credited_roles(credits or {})
        if isinstance(raw, dict):
            raw = {**raw, "credited_roles": credited_roles}
        else:
            raw = {"credited_roles": credited_roles}

        person.name = raw.get("name") or person.name
        person.profile_path = raw.get("profile_path") or ""
        person.tmdb_raw = raw
        person.tmdb_credits_raw = credits
        person.tmdb_last_sync_at = timezone.now()
        person.save(update_fields=[
            "name",
            "profile_path",
            "tmdb_raw",
            "tmdb_credits_raw",
            "tmdb_last_sync_at",
            "updated_at",
        ])
    elif person.tmdb_credits_raw and not has_credited_roles:
        # Backfill derived roles from cached credits without making a TMDb call.
        credited_roles = extract_person_credited_roles(person.tmdb_credits_raw or {})
        person.tmdb_raw = {**tmdb_raw, "credited_roles": credited_roles}
        person.save(update_fields=["tmdb_raw", "updated_at"])
    try:
        cache.set(cache_key, person, timeout=5 * 60)
    except Exception:
        pass
    return person


def get_or_sync_person_images(tmdb_id: int, *, force: bool = False) -> Person:
    """Fetch and cache person images for followed/saved people.

    Stores payload in person.tmdb_raw["images"].
    """
    person = get_or_sync_person(tmdb_id, force=force)
    tmdb_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
    has_images = isinstance(tmdb_raw.get("images"), dict)
    if force or _is_stale(person.tmdb_last_sync_at) or not has_images:
        # Invalidate person images HTTP cache when forcing refresh.
        if force:
            try:
                client = TMDbClient.from_settings()
                try:
                    cache.delete(client.cache_key_for(f"/person/{tmdb_id}/images"))
                except Exception:
                    pass
            except Exception:
                client = TMDbClient.from_settings()
        else:
            client = TMDbClient.from_settings()
        images = client.get_person_images(tmdb_id)
        person.tmdb_raw = {**tmdb_raw, "images": images}
        person.tmdb_last_sync_at = timezone.now()
        person.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "updated_at"])
    return person


def get_or_sync_company(tmdb_id: int, *, force: bool = False) -> Company:
    cache_key = f"db:company:v1:{int(tmdb_id)}"
    if not force:
        try:
            cached = cache.get(cache_key)
            if isinstance(cached, Company):
                if cached.tmdb_raw and not _is_stale(cached.tmdb_last_sync_at):
                    return cached
        except Exception:
            pass

    company, _ = Company.objects.get_or_create(tmdb_id=tmdb_id, defaults={"name": str(tmdb_id)})
    if force or _is_stale(company.tmdb_last_sync_at) or not company.tmdb_raw:
        # When forcing a company sync, clear TMDb HTTP cache keys used for
        # the company details and first page of company movies so we get
        # fresh data from TMDb.
        if force:
            try:
                client = TMDbClient.from_settings()
                try:
                    cache.delete(client.cache_key_for(f"/company/{tmdb_id}"))
                except Exception:
                    pass
                try:
                    cache.delete(client.cache_key_for(f"/company/{tmdb_id}/movies", params={"page": 1}))
                except Exception:
                    pass
            except Exception:
                client = TMDbClient.from_settings()
        else:
            client = TMDbClient.from_settings()
        raw = client.get_company(tmdb_id)

        # Preserve any cached filmography pages unless forcing a refresh.
        existing = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
        if force:
            existing = {
                k: v
                for k, v in existing.items()
                if k
                not in {
                    "discover_movies_pages",
                    "discover_movies_meta",
                    "company_movies_pages",
                    "company_movies_meta",
				"tba_movies",
				"tba_scan_meta",
				"tba_movies_meta",
                }
            }
        discover_pages = (
            existing.get("discover_movies_pages")
            if isinstance(existing.get("discover_movies_pages"), dict)
            else {}
        )
        discover_meta = (
            existing.get("discover_movies_meta")
            if isinstance(existing.get("discover_movies_meta"), dict)
            else {}
        )

        company_movies_pages = (
            existing.get("company_movies_pages")
            if isinstance(existing.get("company_movies_pages"), dict)
            else {}
        )
        company_movies_meta = (
            existing.get("company_movies_meta")
            if isinstance(existing.get("company_movies_meta"), dict)
            else {}
        )

        if isinstance(raw, dict):
            merged = {**existing, **raw}
        else:
            merged = {**existing}
        merged.update({
            "discover_movies_pages": discover_pages,
            "discover_movies_meta": discover_meta,
            "company_movies_pages": company_movies_pages,
            "company_movies_meta": company_movies_meta,
        })

        company.name = merged.get("name") or company.name
        company.logo_path = merged.get("logo_path") or ""
        company.tmdb_raw = merged
        company.tmdb_last_sync_at = timezone.now()
        company.save(update_fields=[
            "name",
            "logo_path",
            "tmdb_raw",
            "tmdb_last_sync_at",
            "updated_at",
        ])
    try:
        cache.set(cache_key, company, timeout=5 * 60)
    except Exception:
        pass
    return company


def get_or_sync_company_movies_page(
    company: Company,
    *,
    page: int,
    force: bool = False,
) -> dict:
    """Fetch and cache /company/{id}/movies pages on the company JSON blob."""
    page = int(page or 1)
    if page < 1:
        page = 1

    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    pages = tmdb_raw.get("company_movies_pages")
    if not isinstance(pages, dict):
        pages = {}

    key = str(page)
    cached = pages.get(key)
    if isinstance(cached, dict) and not force and not _is_stale(company.tmdb_last_sync_at):
        return cached

    client = TMDbClient.from_settings()
    payload = client.get_company_movies(company.tmdb_id, page=page)

    company.tmdb_raw = _ensure_paged_cache(
        tmdb_raw,
        pages_key="company_movies_pages",
        meta_key="company_movies_meta",
        page=page,
        payload=payload,
    )
    company.tmdb_last_sync_at = timezone.now()
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "updated_at"])
    return payload


def get_or_sync_movie(
    tmdb_id: int,
    *,
    force: bool = False,
    include_credits: bool = False,
    include_release_dates: bool = False,
    include_images: bool = False,
    include_watch_providers: bool = False,
    include_recommendations: bool = False,
    include_similar: bool = False,
    recommendations_page: int = 1,
    similar_page: int = 1,
) -> Movie:
    cache_key = (
        "db:movie:v1:"
        f"{int(tmdb_id)}:"
        f"c{int(bool(include_credits))}:"
        f"r{int(bool(include_release_dates))}:"
        f"i{int(bool(include_images))}:"
        f"w{int(bool(include_watch_providers))}:"
        f"rec{int(bool(include_recommendations))}:{int(recommendations_page or 1)}:"
        f"sim{int(bool(include_similar))}:{int(similar_page or 1)}"
    )
    if not force:
        try:
            cached = cache.get(cache_key)
            if isinstance(cached, Movie) and cached.tmdb_raw and not _is_stale(cached.tmdb_last_sync_at):
                if include_credits and not cached.tmdb_credits_raw:
                    cached = None
                if include_release_dates and not (
                    isinstance(cached.tmdb_raw, dict) and "release_dates" in cached.tmdb_raw
                ):
                    cached = None
                if include_images and not (
                    isinstance(cached.tmdb_raw, dict) and "images" in cached.tmdb_raw
                ):
                    cached = None
                if include_watch_providers and not (
                    isinstance(cached.tmdb_raw, dict) and "watch_providers" in cached.tmdb_raw
                ):
                    cached = None
                if cached is not None:
                    return cached
        except Exception:
            pass

    # Movie payloads are large; fetch them on-demand per tab.
    # - details: needs only tmdb_raw
    # - cast/crew: needs tmdb_credits_raw
    # - release dates: stored in tmdb_raw["release_dates"]
    movie = _get_or_sync_movie_internal(
        tmdb_id,
        force=force,
        include_credits=include_credits,
        include_release_dates=include_release_dates,
        include_images=include_images,
        include_watch_providers=include_watch_providers,
        include_recommendations=include_recommendations,
        include_similar=include_similar,
        recommendations_page=recommendations_page,
        similar_page=similar_page,
    )
    try:
        cache.set(cache_key, movie, timeout=5 * 60)
    except Exception:
        pass
    return movie


def _get_or_sync_movie_internal(
    tmdb_id: int,
    *,
    force: bool = False,
    include_credits: bool = False,
    include_release_dates: bool = False,
    include_images: bool = False,
    include_watch_providers: bool = False,
    include_recommendations: bool = False,
    include_similar: bool = False,
    recommendations_page: int = 1,
    similar_page: int = 1,
) -> Movie:
    movie, _ = Movie.objects.get_or_create(tmdb_id=tmdb_id, defaults={"title": str(tmdb_id)})

    client = TMDbClient.from_settings()

    needs_details = force or _is_stale(movie.tmdb_last_sync_at) or not movie.tmdb_raw
    needs_credits = include_credits and (force or _is_stale(movie.tmdb_last_sync_at) or not movie.tmdb_credits_raw)
    has_release_dates = isinstance(movie.tmdb_raw, dict) and "release_dates" in movie.tmdb_raw
    needs_release_dates = include_release_dates and (
        force or _is_stale(movie.tmdb_last_sync_at) or not movie.tmdb_raw or not has_release_dates
    )

    has_images = isinstance(movie.tmdb_raw, dict) and "images" in movie.tmdb_raw
    needs_images = include_images and (force or _is_stale(movie.tmdb_last_sync_at) or not movie.tmdb_raw or not has_images)

    has_watch = isinstance(movie.tmdb_raw, dict) and "watch_providers" in movie.tmdb_raw
    needs_watch = include_watch_providers and (
        force or _is_stale(movie.tmdb_last_sync_at) or not movie.tmdb_raw or not has_watch
    )

    rec_page = int(recommendations_page or 1)
    if rec_page < 1:
        rec_page = 1
    sim_page = int(similar_page or 1)
    if sim_page < 1:
        sim_page = 1

    rec_pages = (movie.tmdb_raw or {}).get("recommendations_pages") if isinstance(movie.tmdb_raw, dict) else None
    has_rec_page = isinstance(rec_pages, dict) and str(rec_page) in rec_pages
    needs_rec = include_recommendations and (force or _is_stale(movie.tmdb_last_sync_at) or not has_rec_page)

    # Do not persist similar-pages into the Movie.tmdb_raw blob.
    # Similar movies are fetched on-demand by views and are not stored in the DB.
    needs_sim = False

    raw = movie.tmdb_raw
    credits = movie.tmdb_credits_raw

    did_fetch = False
    if needs_details:
        raw = client.get_movie(tmdb_id)
        did_fetch = True

    if needs_release_dates:
        release_dates = client.get_movie_release_dates(tmdb_id)
        if isinstance(raw, dict):
            raw = {**raw, "release_dates": release_dates}
        else:
            raw = {"release_dates": release_dates}
        did_fetch = True

    if needs_images:
        images = client.get_movie_images(tmdb_id)
        if isinstance(raw, dict):
            raw = {**raw, "images": images}
        else:
            raw = {"images": images}
        did_fetch = True

    if needs_watch:
        watch = client.get_movie_watch_providers(tmdb_id)
        if isinstance(raw, dict):
            raw = {**raw, "watch_providers": watch}
        else:
            raw = {"watch_providers": watch}
        did_fetch = True

    if needs_rec:
        recs = client.get_movie_recommendations(tmdb_id, page=rec_page)
        raw = _ensure_paged_cache(
            raw if isinstance(raw, dict) else {},
            pages_key="recommendations_pages",
            meta_key="recommendations_meta",
            page=rec_page,
            payload=recs,
        )
        did_fetch = True

    # similar pages intentionally not merged into `raw` or saved.
    # Views that need similar movies should call TMDb directly.

    if needs_credits:
        credits = client.get_movie_credits(tmdb_id)
        did_fetch = True

    if did_fetch:
        movie.title = (raw or {}).get("title") or (raw or {}).get("name") or movie.title
        movie.poster_path = (raw or {}).get("poster_path") or ""
        movie.backdrop_path = (raw or {}).get("backdrop_path") or ""
        release_date_str = (raw or {}).get("release_date") or ""
        release_date: date | None = None
        if release_date_str:
            try:
                release_date = date.fromisoformat(release_date_str)
            except ValueError:
                release_date = None

        movie.release_date = release_date
        movie.tmdb_raw = raw or {}
        if include_credits:
            movie.tmdb_credits_raw = credits or {}
        movie.tmdb_last_sync_at = timezone.now()

        update_fields = [
            "title",
            "poster_path",
            "backdrop_path",
            "release_date",
            "tmdb_raw",
            "tmdb_last_sync_at",
            "updated_at",
        ]
        if include_credits:
            update_fields.append("tmdb_credits_raw")

        movie.save(update_fields=update_fields)

    return movie
