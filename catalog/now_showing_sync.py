from __future__ import annotations

from datetime import date

import requests
from django.db import transaction
from django.utils import timezone

from .models import NowShowingMovie
from .tmdb import TMDbClient


def sync_now_showing(*, city: str, source_url: str, sync_key: str = "") -> dict[str, int]:
	"""Fetch the BookYourShow snapshot and replace the city's active movie list."""
	if not city or not source_url:
		raise ValueError("BOOKYOURSHOW_CITY and BOOKYOURSHOW_SYNC_URL are required")
	headers = {"X-Sync-Key": sync_key} if sync_key else {}
	try:
		response = requests.post(source_url, headers=headers, timeout=300)
		response.raise_for_status()
		snapshot = response.json()
	except requests.HTTPError as exc:
		detail = (exc.response.text or "").strip()[:500] if exc.response is not None else ""
		raise RuntimeError(f"BookYourShow sync failed: {exc}{f' - {detail}' if detail else ''}") from exc
	except (requests.RequestException, ValueError) as exc:
		raise RuntimeError(f"BookYourShow sync failed: {exc}") from exc
	items = snapshot.get("items") if isinstance(snapshot, dict) else None
	if not isinstance(items, list):
		raise RuntimeError("BookYourShow response did not contain an items list")

	now = timezone.now()
	tmdb_client = TMDbClient.from_settings()
	matched_count = poster_count = 0
	with transaction.atomic():
		NowShowingMovie.objects.filter(city__iexact=city).update(is_active=False)
		for item in items:
			if not isinstance(item, dict):
				continue
			url = str(item.get("url") or "").strip()
			title = str(item.get("title") or "").strip()
			if not url or not title:
				continue
			tmdb_id, tmdb_poster_path = tmdb_match(tmdb_client, title, item.get("release_date"))
			matched_count += bool(tmdb_id)
			poster_count += bool(tmdb_poster_path)
			NowShowingMovie.objects.update_or_create(
				city=city, bookmyshow_url=url,
				defaults={
					"title": title, "tmdb_id": tmdb_id, "tmdb_poster_path": tmdb_poster_path,
					"poster_url": str(item.get("poster_url") or "").strip(),
					"languages": item.get("languages") if isinstance(item.get("languages"), list) else [],
					"genres": item.get("genres") if isinstance(item.get("genres"), list) else [],
					"rating": str(item.get("rating") or "").strip(),
					"release_date": parse_date(item.get("release_date")),
					"runtime_minutes": parse_runtime(item.get("runtime_minutes")),
					"director": str(item.get("director") or "").strip(),
					"cast": item.get("cast") if isinstance(item.get("cast"), list) else [],
					"is_active": True, "last_seen_at": now,
				},
			)
	return {"imported": len(items), "tmdb_matched": matched_count, "posters_saved": poster_count}


def tmdb_match(client: TMDbClient, title: str, release_date_value):
	try:
		payload = client.search_movies(title, page=1) or {}
		results = payload.get("results") if isinstance(payload, dict) else []
		if not isinstance(results, list) or not results:
			return None, ""
		year = str(release_date_value or "")[:4]
		title_key = "".join(ch for ch in title.casefold() if ch.isalnum())
		candidates = [item for item in results if isinstance(item, dict)]
		if year:
			year_matches = [item for item in candidates if str(item.get("release_date") or "").startswith(year)]
			if year_matches:
				candidates = year_matches
		exact_matches = [item for item in candidates if "".join(ch for ch in str(item.get("title") or "").casefold() if ch.isalnum()) == title_key]
		match = (exact_matches or candidates)[0]
		return int(match.get("id")), str(match.get("poster_path") or "")
	except Exception:
		return None, ""


def parse_date(value):
	try:
		return date.fromisoformat(str(value)) if value else None
	except ValueError:
		return None


def parse_runtime(value):
	try:
		parsed = int(value)
		return parsed if parsed >= 10 else None
	except (TypeError, ValueError):
		return None
