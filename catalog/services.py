from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import Company, Movie, Person
from .tmdb import TMDbClient
from .new_movie_helpers import (
    extract_movie_release_dates_from_credits,
    extract_movie_release_dates_from_credits_for_role,
    get_person_comeback_info,
)


# Use release_date (not primary_release_date) for better completeness.
# Some TMDb titles lack a primary_release_date but still have release_date.
COMPANY_FILMOGRAPHY_SORT_BY = "release_date.desc"
COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE = "1800-01-01"
COMPANY_TBA_SORT_BY = "popularity.desc"


def _compact_person_credit_item(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    media_type = str(item.get("media_type") or "movie").strip().lower()
    if media_type not in {"", "movie"}:
        return {}
    movie_id = item.get("id")
    if not isinstance(movie_id, int):
        return {}
    compact: dict[str, Any] = {
        "id": movie_id,
        "title": str(item.get("title") or item.get("name") or movie_id),
        "release_date": str(item.get("release_date") or "").strip(),
        "popularity": item.get("popularity") if isinstance(item.get("popularity"), (int, float)) else item.get("popularity"),
        "media_type": "movie",
        "poster_path": str(item.get("poster_path") or ""),
        "backdrop_path": str(item.get("backdrop_path") or ""),
    }
    character = str(item.get("character") or "").strip()
    job = str(item.get("job") or "").strip()
    if character:
        compact["character"] = character
    if job:
        compact["job"] = job
    return compact


def compact_person_credits_payload(credits: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(credits, dict):
        return {"cast": [], "crew": []}
    cast = [
        compact
        for compact in (_compact_person_credit_item(item) for item in (credits.get("cast") or []))
        if compact
    ]
    crew = [
        compact
        for compact in (_compact_person_credit_item(item) for item in (credits.get("crew") or []))
        if compact
    ]
    return {"cast": cast, "crew": crew}


def _company_movie_year(movie: dict[str, Any]) -> int | None:
    release_date = str(movie.get("release_date") or "").strip()
    if release_date:
        try:
            return date.fromisoformat(release_date).year
        except ValueError:
            pass
    year = movie.get("year")
    if isinstance(year, int) and year > 0:
        return year
    if isinstance(year, str):
        year_s = year.strip()
        if len(year_s) == 4 and year_s.isdigit():
            return int(year_s)
    return None


def compact_company_movie(movie: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(movie, dict):
        return {}
    movie_id = movie.get("id")
    if not isinstance(movie_id, int):
        return {}
    compact = {
        "id": movie_id,
        "title": str(movie.get("title") or movie.get("name") or movie_id),
    }
    year = _company_movie_year(movie)
    if year is not None:
        compact["year"] = year
    release_date = str(movie.get("release_date") or "").strip()
    if release_date:
        compact["release_date"] = release_date
    poster_path = str(movie.get("poster_path") or "").strip()
    if poster_path:
        compact["poster_path"] = poster_path
    return compact


def compact_company_filmography_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    results = payload.get("results") or []
    compact_results = [compact_company_movie(movie) for movie in results if isinstance(movie, dict)]
    return {**payload, "results": [movie for movie in compact_results if movie]}


def compact_company_filmography_pages(pages: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pages, dict):
        return {}
    compact_pages: dict[str, Any] = {}
    for key, payload in pages.items():
        if isinstance(payload, dict):
            compact_pages[str(key)] = compact_company_filmography_payload(payload)
    return compact_pages


def hydrate_company_movie_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill in missing movie display fields for a currently rendered page.

    Company filmography pages may intentionally store compact rows without
    poster_path. For the small set of movies on the current page, fetch the
    TMDb movie details and backfill poster/release fields so the page can render
    posters directly from TMDb without changing the database.
    """
    if not isinstance(results, list):
        return []
    client = TMDbClient.from_settings()
    hydrated: list[dict[str, Any]] = []
    for movie in results:
        if not isinstance(movie, dict):
            continue
        movie_id = movie.get("id")
        if not isinstance(movie_id, int):
            hydrated.append(movie)
            continue

        needs_poster = not str(movie.get("poster_path") or "").strip()
        needs_release = not str(movie.get("release_date") or "").strip()
        if not needs_poster and not needs_release:
            merged = {**movie}
            year = _company_movie_year(merged)
            if year is not None:
                merged["year"] = year
            hydrated.append(merged)
            continue

        try:
            full_movie = client.get_movie(movie_id)
        except Exception:
            full_movie = {}

        if isinstance(full_movie, dict) and full_movie:
            merged = {**movie}
            if needs_poster and str(full_movie.get("poster_path") or "").strip():
                merged["poster_path"] = str(full_movie.get("poster_path") or "").strip()
            if needs_release and str(full_movie.get("release_date") or "").strip():
                merged["release_date"] = str(full_movie.get("release_date") or "").strip()
            merged.setdefault("id", movie_id)
            merged.setdefault("title", movie.get("title") or movie.get("name") or movie_id)
            year = _company_movie_year(merged)
            if year is not None:
                merged["year"] = year
            hydrated.append(merged)
        else:
            hydrated.append(movie)
    return hydrated


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


def get_person_status_label(person: Person, *, followed_role: str | None = None) -> str:
    """Return a clean single-word status label for a person.

    Labels: Deceased, Upcoming, Announced, Inactive, Idle
    Priority: Deceased > Upcoming > Announced > Inactive > Idle
    """
    credits = person.tmdb_credits_raw or {}

    # Deceased check (TMDb 'deathday')
    deathday = "" if not isinstance(person.tmdb_raw, dict) else (person.tmdb_raw.get("deathday") or "")
    if isinstance(deathday, str) and deathday.strip():
        return "Deceased"

    # Release date map (respect followed_role when provided)
    if followed_role:
        rd_map = extract_movie_release_dates_from_credits_for_role(credits, followed_role)
    else:
        rd_map = extract_movie_release_dates_from_credits(credits)

    from datetime import date
    from django.utils import timezone

    today = timezone.now().date()
    has_tba = False

    for rd in rd_map.values():
        if not isinstance(rd, str):
            continue
        rd_s = rd.strip()
        if not rd_s:
            has_tba = True
            continue
        try:
            parsed = date.fromisoformat(rd_s)
        except Exception:
            continue
        if parsed > today:
            return "Upcoming"

    if has_tba:
        return "Announced"

    # Inactive (use existing comeback detection which respects thresholds)
    comeback = get_person_comeback_info(credits, followed_role=followed_role)
    if comeback:
        return "Inactive"

    return "Idle"


def get_person_status_key(person: Person, *, followed_role: str | None = None) -> str:
    label = get_person_status_label(person, followed_role=followed_role).strip().lower()
    return "announced" if label == "announced" else label


def _cache_stamp(obj: object) -> str:
    stamp = getattr(obj, "tmdb_last_sync_at", None) or getattr(obj, "updated_at", None)
    if hasattr(stamp, "isoformat"):
        try:
            return stamp.isoformat()
        except Exception:
            return ""
    return str(stamp or "")


def _seed_person_summary_cache(person: Person) -> None:
    try:
        raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
        stamp = _cache_stamp(person)
        cache.set(f"person:deathday:v1:{int(person.pk)}:{stamp}", str(raw.get("deathday") or "").strip(), 5 * 60)
        cache.set(
            f"person:dept:v1:{int(person.pk)}:{stamp}",
            str(raw.get("known_for_department") or "").strip(),
            5 * 60,
        )
        default_status = get_person_status_label(person)
        role_statuses = {}
        for role in ("Actor", "Director", "Crew"):
            role_statuses[role.lower()] = {
                "status": get_person_status_label(person, followed_role=role),
                "status_key": get_person_status_key(person, followed_role=role),
            }
        cache.set(
            f"person:summary:v1:{int(person.pk)}:{stamp}",
            {
                "default": {
                    "status": default_status,
                    "status_key": "announced" if default_status.strip().lower() == "announced" else default_status.strip().lower(),
                },
                "roles": role_statuses,
            },
            5 * 60,
        )
    except Exception:
        pass


def _seed_company_summary_cache(company: Company) -> None:
    try:
        raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
        stamp = _cache_stamp(company)
        cache.set(f"company:homepage:v1:{int(company.pk)}:{stamp}", str(raw.get("homepage") or "").strip(), 5 * 60)
        status, status_key = get_company_status_snapshot(company)
        cache.set(
            f"company:status:v2:{int(company.pk)}:{stamp}:0",
            {"status": status, "status_key": status_key},
            5 * 60,
        )
    except Exception:
        pass


def _load_person_tmdb_raw(person: Person) -> dict[str, Any]:
    raw = person.__dict__.get("tmdb_raw")
    if isinstance(raw, dict):
        return raw
    try:
        raw = Person.objects.only("tmdb_raw").filter(pk=person.pk).values_list("tmdb_raw", flat=True).first()
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _load_person_tmdb_credits_raw(person: Person) -> dict[str, Any]:
    credits = person.__dict__.get("tmdb_credits_raw")
    if isinstance(credits, dict):
        return credits
    try:
        credits = (
            Person.objects.only("tmdb_credits_raw")
            .filter(pk=person.pk)
            .values_list("tmdb_credits_raw", flat=True)
            .first()
        )
    except Exception:
        credits = {}
    return credits if isinstance(credits, dict) else {}


def get_person_deathday(person: Person) -> str:
    cache_key = f"person:deathday:v1:{int(person.pk)}:{_cache_stamp(person)}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, str):
            return cached
    except Exception:
        pass
    raw = _load_person_tmdb_raw(person)
    deathday = str(raw.get("deathday") or "").strip()
    try:
        cache.set(cache_key, deathday, 5 * 60)
    except Exception:
        pass
    return deathday


def get_person_known_for_department(person: Person) -> str:
    cache_key = f"person:dept:v1:{int(person.pk)}:{_cache_stamp(person)}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, str):
            return cached
    except Exception:
        pass
    raw = _load_person_tmdb_raw(person)
    dept = str(raw.get("known_for_department") or "").strip()
    try:
        cache.set(cache_key, dept, 5 * 60)
    except Exception:
        pass
    return dept


def get_person_status_snapshot(person: Person, *, followed_role: str | None = None) -> tuple[str, str]:
    cache_key = f"person:summary:v1:{int(person.pk)}:{_cache_stamp(person)}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            if followed_role:
                role_cache = cached.get("roles")
                if isinstance(role_cache, dict):
                    role_value = role_cache.get((followed_role or "").strip().lower())
                    if isinstance(role_value, dict):
                        status = str(role_value.get("status") or "")
                        status_key = str(role_value.get("status_key") or "")
                        if status or status_key:
                            return status, status_key
            default_value = cached.get("default")
            if isinstance(default_value, dict):
                status = str(default_value.get("status") or "")
                status_key = str(default_value.get("status_key") or "")
            else:
                status = ""
                status_key = ""
            if status or status_key:
                return status, status_key
    except Exception:
        pass
    raw = _load_person_tmdb_raw(person)
    credits = _load_person_tmdb_credits_raw(person)
    temp_person = person
    if isinstance(raw, dict):
        temp_person = Person(
            tmdb_id=person.tmdb_id,
            name=person.name,
            profile_path=person.profile_path,
            tmdb_raw=raw,
            tmdb_credits_raw=credits,
            tmdb_last_sync_at=person.tmdb_last_sync_at,
        )
    status = get_person_status_label(temp_person, followed_role=followed_role)
    status_key = "announced" if status.strip().lower() == "announced" else status.strip().lower()
    _seed_person_summary_cache(temp_person)
    return status, status_key


def _load_company_tmdb_raw(company: Company) -> dict[str, Any]:
    raw = company.__dict__.get("tmdb_raw")
    if isinstance(raw, dict):
        return raw
    try:
        raw = Company.objects.only("tmdb_raw").filter(pk=company.pk).values_list("tmdb_raw", flat=True).first()
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def get_company_homepage(company: Company) -> str:
    cache_key = f"company:homepage:v1:{int(company.pk)}:{_cache_stamp(company)}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, str):
            return cached
    except Exception:
        pass
    raw = _load_company_tmdb_raw(company)
    homepage = str(raw.get("homepage") or "").strip()
    try:
        cache.set(cache_key, homepage, 5 * 60)
    except Exception:
        pass
    return homepage


def get_company_status_snapshot(company: Company, *, fallback_results: list[dict] | None = None, has_tba_hint: bool = False) -> tuple[str, str]:
    cache_key = f"company:status:v3:{int(company.pk)}:{_cache_stamp(company)}:{int(bool(has_tba_hint))}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            status = str(cached.get("status") or "")
            status_key = str(cached.get("status_key") or "")
            if status or status_key:
                return status, status_key
    except Exception:
        pass

    today = timezone.now().date()
    ten_years_ago = today - timedelta(days=365 * 10)
    raw = _load_company_tmdb_raw(company)
    payloads: list[dict[str, Any]] = []
    pages = raw.get("discover_movies_pages")
    if isinstance(pages, dict):
        payloads = [payload for payload in pages.values() if isinstance(payload, dict)]
    elif fallback_results is not None:
        payloads = [{"results": fallback_results}]

    upcoming_with_date = 0
    upcoming_no_date = 0
    latest_past_release: date | None = None
    has_cached_tba_movies = False

    tba_movies = raw.get("tba_movies")
    if isinstance(tba_movies, list) and any(isinstance(movie, dict) for movie in tba_movies):
        has_cached_tba_movies = True

    for payload in payloads:
        for movie in (payload.get("results") or []):
            if not isinstance(movie, dict):
                continue
            release_date_str = str(movie.get("release_date") or "").strip()
            release_dt = None
            if len(release_date_str) == 10 and release_date_str[4] == "-":
                try:
                    release_dt = date.fromisoformat(release_date_str)
                except ValueError:
                    release_dt = None
            if release_dt is not None and release_dt > today:
                upcoming_with_date += 1
            elif not release_date_str:
                upcoming_no_date += 1
            elif release_dt is not None and release_dt <= today:
                if latest_past_release is None or release_dt > latest_past_release:
                    latest_past_release = release_dt

    if upcoming_with_date > 0:
        result = ("Upcoming", "upcoming")
    elif upcoming_no_date > 0 or has_tba_hint or has_cached_tba_movies:
        result = ("Announced", "announced")
    elif latest_past_release is not None and latest_past_release < ten_years_ago:
        result = ("Inactive", "inactive")
    else:
        result = ("Idle", "idle")

    try:
        cache.set(cache_key, {"status": result[0], "status_key": result[1]}, 5 * 60)
    except Exception:
        pass
    return result


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
        meta = {k: v for k, v in meta.items() if k not in {"total_pages", "total_results", "synced_at"}}

    key = str(page)
    cached = pages.get(key)
    filmography_synced_at = None
    synced_at_raw = meta.get("synced_at")
    if isinstance(synced_at_raw, str) and synced_at_raw.strip():
        try:
            filmography_synced_at = datetime.fromisoformat(synced_at_raw.strip())
            if filmography_synced_at.tzinfo is None:
                filmography_synced_at = timezone.make_aware(filmography_synced_at)
        except Exception:
            filmography_synced_at = None

    if (
        isinstance(cached, dict)
        and not force
        and filmography_synced_at is not None
        and not _is_stale(filmography_synced_at)
    ):
        return cached

    client = TMDbClient.from_settings()
    payload = client.discover_movies_by_company(
        company.tmdb_id,
        page=page,
        sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
        extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
    )
    pages[key] = compact_company_filmography_payload(payload)

    meta["total_pages"] = int(payload.get("total_pages") or meta.get("total_pages") or 1)
    meta["total_results"] = int(payload.get("total_results") or meta.get("total_results") or 0)
    meta["sort_by"] = COMPANY_FILMOGRAPHY_SORT_BY
    meta["release_date_gte"] = COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE
    meta["synced_at"] = timezone.now().isoformat()

    company.tmdb_raw = {**tmdb_raw, "discover_movies_pages": compact_company_filmography_pages(pages), "discover_movies_meta": meta}
    company.tmdb_last_sync_at = timezone.now()
    company.tmdb_last_sync_source = "sync" if force else "ttl"
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
    _seed_company_summary_cache(company)
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
    should_stop_cb: Callable[[], bool] | None = None,
) -> int:
    """Prefetch and cache full company filmography into Company.tmdb_raw.

    Uses TMDb discover endpoint (with_companies) because TMDb's
    /company/{id}/movies pagination can be unreliable.

    Returns the number of pages fetched.

    Safety: pass max_pages to cap the number of pages fetched in one call.
    """
    def should_stop() -> bool:
        try:
            return bool(should_stop_cb and should_stop_cb())
        except Exception:
            return False

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
        meta = {k: v for k, v in meta.items() if k not in {"total_pages", "total_results", "synced_at"}}
    cached_total_pages = meta.get("total_pages")
    filmography_synced_at = None
    synced_at_raw = meta.get("synced_at")
    if isinstance(synced_at_raw, str) and synced_at_raw.strip():
        try:
            filmography_synced_at = datetime.fromisoformat(synced_at_raw.strip())
            if filmography_synced_at.tzinfo is None:
                filmography_synced_at = timezone.make_aware(filmography_synced_at)
        except Exception:
            filmography_synced_at = None
    if (
        not force
        and filmography_synced_at is not None
        and not _is_stale(filmography_synced_at)
        and isinstance(cached_total_pages, int)
        and cached_total_pages > 0
        and len(pages) >= cached_total_pages
    ):
        return 0

    if should_stop():
        return 0

    client = TMDbClient.from_settings()
    first = client.discover_movies_by_company(
        company.tmdb_id,
        page=1,
        sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
        extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
    )
    first = compact_company_filmography_payload(first)
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

    if should_stop():
        meta["total_pages"] = total_pages
        meta["total_results"] = int(first.get("total_results") or meta.get("total_results") or 0)
        meta["sort_by"] = COMPANY_FILMOGRAPHY_SORT_BY
        meta["release_date_gte"] = COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE
        meta["synced_at"] = timezone.now().isoformat()
        company.tmdb_raw = {**tmdb_raw, "discover_movies_pages": compact_company_filmography_pages(pages), "discover_movies_meta": meta}
        company.tmdb_last_sync_at = timezone.now()
        company.tmdb_last_sync_source = "sync" if force else "ttl"
        company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
        try:
            cache.set(f"db:company:v1:{int(company.tmdb_id)}", company, timeout=5 * 60)
        except Exception:
            pass
        return fetched

    for p in range(2, pages_to_fetch + 1):
        if should_stop():
            break
        key = str(p)
        if not force and key in pages and not _is_stale(company.tmdb_last_sync_at):
            continue
        payload = client.discover_movies_by_company(
            company.tmdb_id,
            page=p,
            sort_by=COMPANY_FILMOGRAPHY_SORT_BY,
            extra_params={"release_date.gte": COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE},
        )
        pages[key] = compact_company_filmography_payload(payload)
        fetched += 1
        try:
            if progress_cb is not None:
                progress_cb(fetched, pages_to_fetch)
        except Exception:
            pass
        if should_stop():
            break

    meta["total_pages"] = total_pages
    meta["total_results"] = int(first.get("total_results") or meta.get("total_results") or 0)
    meta["sort_by"] = COMPANY_FILMOGRAPHY_SORT_BY
    meta["release_date_gte"] = COMPANY_FILMOGRAPHY_RELEASE_DATE_GTE
    meta["synced_at"] = timezone.now().isoformat()

    company.tmdb_raw = {**tmdb_raw, "discover_movies_pages": compact_company_filmography_pages(pages), "discover_movies_meta": meta}
    company.tmdb_last_sync_at = timezone.now()
    company.tmdb_last_sync_source = "sync" if force else "ttl"
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
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

    if cached_movies and not force:
        start = (page - 1) * page_size
        end = start + page_size
        items = cached_movies[start:end]
        has_prev = page > 1
        has_next = len(cached_movies) > end
        return items, has_prev, has_next

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
    company.tmdb_last_sync_source = "sync" if force else "ttl"
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
    _seed_company_summary_cache(company)

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


def purge_stale_movies(*, days: int | None = None) -> tuple[int, dict[str, int]]:
    days = int(days or getattr(settings, "MOVIE_STALE_DELETE_DAYS", 5) or 5)
    if days < 1:
        days = 1
    cutoff = timezone.now() - timedelta(days=days)
    return Movie.objects.filter(last_accessed_at__lt=cutoff).delete()


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
    has_external_ids = isinstance(tmdb_raw.get("external_ids"), dict)

    if force or _is_stale(person.tmdb_last_sync_at) or not person.tmdb_raw or not person.tmdb_credits_raw or not has_external_ids:
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
                try:
                    cache.delete(client.cache_key_for(f"/person/{tmdb_id}/external_ids"))
                except Exception:
                    pass
            except Exception:
                client = TMDbClient.from_settings()
        else:
            client = TMDbClient.from_settings()

        raw = client.get_person(tmdb_id)
        credits = compact_person_credits_payload(client.get_person_credits(tmdb_id))
        try:
            external_ids = client.get_person_external_ids(tmdb_id)
        except Exception:
            external_ids = {}

        credited_roles = extract_person_credited_roles(credits or {})
        if isinstance(raw, dict):
            raw = {**raw, "credited_roles": credited_roles, "external_ids": external_ids}
        else:
            raw = {"credited_roles": credited_roles, "external_ids": external_ids}

        person.name = raw.get("name") or person.name
        person.profile_path = raw.get("profile_path") or ""
        person.tmdb_raw = raw
        person.tmdb_credits_raw = credits
        person.tmdb_last_sync_at = timezone.now()
        person.tmdb_last_sync_source = "sync" if force else "ttl"
        person.save(update_fields=[
            "name",
            "profile_path",
            "tmdb_raw",
            "tmdb_credits_raw",
            "tmdb_last_sync_at",
            "tmdb_last_sync_source",
            "updated_at",
        ])
        _seed_person_summary_cache(person)
        tmdb_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
        has_credited_roles = isinstance(tmdb_raw.get("credited_roles"), list)
        has_external_ids = isinstance(tmdb_raw.get("external_ids"), dict)
    if person.tmdb_credits_raw and not has_credited_roles:
        # Backfill derived roles from cached credits without making a TMDb call.
        credited_roles = extract_person_credited_roles(person.tmdb_credits_raw or {})
        person.tmdb_raw = {**tmdb_raw, "credited_roles": credited_roles}
        person.save(update_fields=["tmdb_raw", "updated_at"])
        tmdb_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
        has_external_ids = isinstance(tmdb_raw.get("external_ids"), dict)
    if not has_external_ids:
        # Backfill social/external link ids once so related links work for older caches too.
        client = TMDbClient.from_settings()
        try:
            external_ids = client.get_person_external_ids(tmdb_id)
        except Exception:
            external_ids = {}
        person.tmdb_raw = {**tmdb_raw, "external_ids": external_ids}
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
        person.tmdb_last_sync_source = "sync" if force else "ttl"
        person.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
        _seed_person_summary_cache(person)
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
    tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
    has_alternative_names = isinstance(tmdb_raw.get("alternative_names"), dict)

    if force or _is_stale(company.tmdb_last_sync_at) or not company.tmdb_raw or not has_alternative_names:
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
                try:
                    cache.delete(client.cache_key_for(f"/company/{tmdb_id}/alternative_names"))
                except Exception:
                    pass
            except Exception:
                client = TMDbClient.from_settings()
        else:
            client = TMDbClient.from_settings()
        raw = client.get_company(tmdb_id)
        try:
            alternative_names = client.get_company_alternative_names(tmdb_id)
        except Exception:
            alternative_names = {}

        # Preserve any cached filmography pages unless forcing a refresh.
        existing = tmdb_raw
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
        discover_pages = compact_company_filmography_pages(
            existing.get("discover_movies_pages") if isinstance(existing.get("discover_movies_pages"), dict) else {}
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
            merged = {**existing, **raw, "alternative_names": alternative_names}
        else:
            merged = {**existing, "alternative_names": alternative_names}
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
        company.tmdb_last_sync_source = "sync" if force else "ttl"
        company.save(update_fields=[
            "name",
            "logo_path",
            "tmdb_raw",
            "tmdb_last_sync_at",
            "tmdb_last_sync_source",
            "updated_at",
        ])
        _seed_company_summary_cache(company)
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
    if isinstance(cached, dict) and not force:
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
    company.tmdb_last_sync_source = "sync" if force else "ttl"
    company.save(update_fields=["tmdb_raw", "tmdb_last_sync_at", "tmdb_last_sync_source", "updated_at"])
    _seed_company_summary_cache(company)
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
