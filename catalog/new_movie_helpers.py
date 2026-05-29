"""Helpers for tracking new movie arrivals."""
from __future__ import annotations

import datetime
from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

from .models import NewMovieArrival

if TYPE_CHECKING:
	from django.contrib.auth.models import User


def _norm_date_str(value: object) -> str:
	if not isinstance(value, str):
		return ""
	return value.strip()


def _norm_role(value: object) -> str:
	if not isinstance(value, str):
		return ""
	return value.strip().lower()


def _is_self_character(value: object) -> bool:
	if not isinstance(value, str):
		return False
	character = value.strip().lower()
	if not character:
		return False
	if "self" in character:
		return True
	self_variants = ("himself", "herself", "themselves", "archive footage")
	if any(variant in character for variant in self_variants):
		return True
	return False


def _is_passive_crew_job(value: object) -> bool:
	job = _norm_role(value)
	if not job:
		return True
	passive_markers = (
		"thanks",
		"original film writer",
		"original screenplay",
		"original story",
		"characters",
		"based on",
		"creator",
	)
	return any(marker in job for marker in passive_markers)


def _string_eq(left: object, right: str) -> bool:
	if not isinstance(left, str):
		return False
	return left.strip().casefold() == right.strip().casefold()


def _value_matches_source(value: object, *, source_type: str, source_id: int, source_name: str, role: str) -> bool:
	"""Best-effort exact match against a TMDb change item value.

	TMDb change payloads nest identifiers in `value` objects. We recurse through
	dicts/lists looking for an exact match on id/name and, for people, the role/job.
	"""
	role_n = _norm_role(role)
	source_name.strip().casefold()

	if isinstance(value, dict):
		for key in ("id", "person_id", "company_id"):
			item_id = value.get(key)
			if isinstance(item_id, int) and item_id == source_id:
				return True

		for key in ("name", "original_name", "title"):
			item_name = value.get(key)
			if _string_eq(item_name, source_name):
				return True

		if source_type == "person":
			# Actor and crew credit changes typically expose a `job` or `character`.
			item_job = value.get("job")
			if role_n and isinstance(item_job, str) and item_job.strip():
				if role_n == "actor" and _string_eq(item_job, "actor"):
					return True
				if role_n != "actor" and _string_eq(item_job, role):
					return True

			item_character = value.get("character")
			if role_n == "actor" and isinstance(item_character, str) and item_character.strip():
				# Character alone is not enough; continue recursion so we still need the
				# person id/name match somewhere in the payload.
				pass

		for nested in value.values():
			if _value_matches_source(nested, source_type=source_type, source_id=source_id, source_name=source_name, role=role):
				return True
		return False

	if isinstance(value, list):
		return any(
			_value_matches_source(item, source_type=source_type, source_id=source_id, source_name=source_name, role=role)
			for item in value
		)

	return False


def _change_key_matches_source(group_key: str, *, source_type: str) -> bool:
	key_n = _norm_role(group_key)
	if not key_n:
		return False
	if source_type == "company":
		return any(token in key_n for token in ("production", "company", "companies"))
	return any(token in key_n for token in ("cast", "crew", "credits", "credit"))


def _parse_iso_date(value: object) -> date | None:
	if not isinstance(value, str):
		return None
	value = value.strip()
	if not value:
		return None
	try:
		return date.fromisoformat(value)
	except ValueError:
		return None


def _format_gap_label(days: int) -> str:
	if days >= 365:
		years = days // 365
		return f"{years} year" if years == 1 else f"{years} years"
	if days >= 30:
		months = days // 30
		return f"{months} month" if months == 1 else f"{months} months"
	return f"{days} day" if days == 1 else f"{days} days"


def _latest_release_date_from_dates(release_dates: dict[int, str], *, today: date | None = None) -> date | None:
	today = today or timezone.now().date()
	latest: date | None = None
	for raw_value in release_dates.values():
		parsed = _parse_iso_date(raw_value)
		if parsed is None or parsed > today:
			continue
		if latest is None or parsed > latest:
			latest = parsed
	return latest


def _earliest_release_date_from_dates(release_dates: dict[int, str], *, today: date | None = None) -> date | None:
	today = today or timezone.now().date()
	earliest: date | None = None
	for raw_value in release_dates.values():
		parsed = _parse_iso_date(raw_value)
		if parsed is None or parsed > today:
			continue
		if earliest is None or parsed < earliest:
			earliest = parsed
	return earliest


def _credit_matches_follow_role(credit: dict, *, followed_role: str) -> bool:
	if not isinstance(credit, dict):
		return False
	if credit.get("media_type") not in (None, "movie"):
		return False
	role_n = _norm_role(followed_role)
	if not role_n:
		return True

	if role_n == "actor":
		return not _is_self_character(credit.get("character"))

	job_n = _norm_role(credit.get("job"))
	if not job_n:
		return False
	if _is_passive_crew_job(job_n):
		return False

	if role_n == "director":
		return job_n == "director"
	if role_n == "crew":
		return True
	return job_n == role_n


def extract_movie_ids_from_credits_for_role(credits: dict, followed_role: str) -> set[int]:
	"""Extract movie IDs for a specific followed role from person credits."""
	role_n = _norm_role(followed_role)
	movie_ids: set[int] = set()

	if role_n == "actor":
		for credit in (credits.get("cast") or []):
			if not _credit_matches_follow_role(credit, followed_role=followed_role):
				continue
			movie_id = credit.get("id") if isinstance(credit, dict) else None
			if isinstance(movie_id, int):
				movie_ids.add(movie_id)
		return movie_ids

	for credit in (credits.get("crew") or []):
		if not _credit_matches_follow_role(credit, followed_role=followed_role):
			continue
		movie_id = credit.get("id") if isinstance(credit, dict) else None
		if isinstance(movie_id, int):
			movie_ids.add(movie_id)
	return movie_ids


def extract_movie_release_dates_from_credits_for_role(credits: dict, followed_role: str) -> dict[int, str]:
	"""Extract movie_id -> release_date for a followed role from person credits."""
	by_id: dict[int, str] = {}
	role_n = _norm_role(followed_role)

	if role_n == "actor":
		for credit in (credits.get("cast") or []):
			if not _credit_matches_follow_role(credit, followed_role=followed_role):
				continue
			if not isinstance(credit, dict):
				continue
			mid = credit.get("id")
			if not isinstance(mid, int):
				continue
			rd = _norm_date_str(credit.get("release_date"))
			if rd or mid not in by_id:
				by_id[mid] = rd
		return by_id

	for credit in (credits.get("crew") or []):
		if not _credit_matches_follow_role(credit, followed_role=followed_role):
			continue
		if not isinstance(credit, dict):
			continue
		mid = credit.get("id")
		if not isinstance(mid, int):
			continue
		rd = _norm_date_str(credit.get("release_date"))
		if rd or mid not in by_id:
			by_id[mid] = rd
	return by_id


def get_person_last_release_date(credits: dict, *, followed_role: str | None = None, today: date | None = None) -> date | None:
	"""Return latest past movie release date for a specific followed role.

	`today` may be provided to limit which releases are considered (useful
	for computing the last release before a given date such as a deathday).
	"""
	if followed_role:
		release_dates = extract_movie_release_dates_from_credits_for_role(credits, followed_role)
	else:
		release_dates = extract_movie_release_dates_from_credits(credits)
	return _latest_release_date_from_dates(release_dates, today=today)


def get_person_first_release_date(credits: dict, *, followed_role: str | None = None, today: date | None = None) -> date | None:
	"""Return earliest past movie release date for a specific followed role."""
	if followed_role:
		release_dates = extract_movie_release_dates_from_credits_for_role(credits, followed_role)
	else:
		release_dates = extract_movie_release_dates_from_credits(credits)
	return _earliest_release_date_from_dates(release_dates, today=today)


def get_person_active_info(
	credits: dict,
	*,
	followed_role: str | None = None,
	deathday: str | date | None = None,
) -> dict | None:
	"""Return active-career metadata for a person if they have at least one past release."""
	first_release_date = get_person_first_release_date(credits, followed_role=followed_role)
	if first_release_date is None:
		return None

	# If a deathday is provided, compute active period up to death; otherwise use today.
	end_date = None
	if deathday:
		if isinstance(deathday, date):
			end_date = deathday
		else:
			end_date = _parse_iso_date(deathday)
	if end_date is None:
		end_date = timezone.now().date()
	active_days = (end_date - first_release_date).days
	active_years = max(active_days // 365, 0)
	return {
		"first_release_date": first_release_date,
		"active_days": active_days,
		"years_active_label": f"{active_years} year" if active_years == 1 else f"{active_years} years",
		"followed_role": (followed_role or "").strip(),
	}


def get_person_comeback_info(
	credits: dict,
	*,
	gap_years: int | None = None,
	followed_role: str | None = None,
	deathday: str | date | None = None,
) -> dict | None:
	"""Return inactivity metadata for a person if their last release is old enough.

	If `deathday` is provided (string in ISO format or `date`), treat the person
	as deceased and always return an inactive record regardless of the normal
	inactivity threshold. For deceased people the displayed "last_release_date"
	will still be the latest release (including posthumous releases), but the
	gap/age label is computed from the deathday (and where possible from the
	last release before death).
	"""
	# Gather per-role release dates so we can compute both the overall last
	# release and the last release that occurred before death (if applicable).
	if followed_role:
		release_dates = extract_movie_release_dates_from_credits_for_role(credits, followed_role)
	else:
		release_dates = extract_movie_release_dates_from_credits(credits)

	last_release_date = _latest_release_date_from_dates(release_dates)
	if last_release_date is None:
		return None

	today = timezone.now().date()

	# Normalize deathday input to a date object if provided.
	died_at: date | None = None
	if deathday:
		if isinstance(deathday, date):
			died_at = deathday
		else:
			died_at = _parse_iso_date(deathday)

	# If person is deceased, compute gap from the deathday (show inactive from
	# the day they died). Still include the last release before death for
	# reference, and the overall `last_release_date` remains the latest release
	# (including posthumous releases).
	if died_at is not None:
		last_release_before_death = _latest_release_date_from_dates(release_dates, today=died_at)
		gap_days = (today - died_at).days

		return {
			"last_release_date": last_release_date,
			"last_release_before_death": last_release_before_death,
			"died_at": died_at,
			"gap_days": gap_days,
			"gap_label": _format_gap_label(gap_days),
			"threshold_days": 0,
			"followed_role": (followed_role or "").strip(),
		}

	# Use inactive threshold for UI/inactivity detection. Support explicit gap_years,
	# then prefer new INACTIVE setting, then fall back to legacy COMEBACK_GAP for
	# backward compatibility.
	threshold_years = (
		gap_years
		or getattr(settings, "TMDB_PERSON_INACTIVE_THRESHOLD_YEARS", None)
		or getattr(settings, "TMDB_PERSON_COMEBACK_GAP_YEARS", 10)
	)
	try:
		threshold_years_int = max(int(threshold_years), 0)
	except (TypeError, ValueError):
		threshold_years_int = 5
	threshold_days = threshold_years_int * 365
	gap_days = (today - last_release_date).days
	if gap_days < threshold_days:
		return None
	return {
		"last_release_date": last_release_date,
		"gap_days": gap_days,
		"gap_label": _format_gap_label(gap_days),
		"threshold_days": threshold_days,
		"followed_role": (followed_role or "").strip(),
	}


def build_person_comeback_event_meta(
	*,
	old_release_dates: dict[int, str],
	new_release_dates: dict[int, str],
	new_movie_ids: set[int],
	gap_years: int | None = None,
) -> dict[int, dict]:
	"""Return per-movie metadata for comeback-style new arrivals."""
	last_release_date = _latest_release_date_from_dates(old_release_dates)
	if last_release_date is None:
		return {}
	# Use comeback threshold for detecting comeback-style arrivals. Support explicit
	# gap_years, then prefer new COMEBACK_THRESHOLD setting, then fall back to
	# legacy COMEBACK_GAP for compatibility.
	threshold_years = (
		gap_years
		or getattr(settings, "TMDB_PERSON_COMEBACK_THRESHOLD_YEARS", None)
		or getattr(settings, "TMDB_PERSON_COMEBACK_GAP_YEARS", 5)
	)
	try:
		threshold_years_int = max(int(threshold_years), 0)
	except (TypeError, ValueError):
		threshold_years_int = 5
	threshold_days = threshold_years_int * 365
	meta_by_movie: dict[int, dict] = {}
	for movie_id in new_movie_ids:
		new_release_date = _parse_iso_date(new_release_dates.get(movie_id))
		if new_release_date is None or new_release_date < last_release_date:
			continue
		gap_days = (new_release_date - last_release_date).days
		if gap_days < threshold_days:
			continue
		meta_by_movie[movie_id] = {
			"kind": "comeback",
			"last_release_date": last_release_date.isoformat(),
			"new_release_date": new_release_date.isoformat(),
			"gap_days": gap_days,
			"gap_label": _format_gap_label(gap_days),
			"threshold_days": threshold_days,
		}
	return meta_by_movie


def extract_movie_ids_from_credits(credits: dict) -> set[int]:
	"""Extract movie IDs from person's credits data."""
	movie_ids = set()
	
	for credit in (credits.get("cast") or []):
		if credit.get("media_type") not in (None, "movie"):
			continue
		movie_id = credit.get("id")
		if isinstance(movie_id, int):
			movie_ids.add(movie_id)
	
	for credit in (credits.get("crew") or []):
		if credit.get("media_type") not in (None, "movie"):
			continue
		movie_id = credit.get("id")
		if isinstance(movie_id, int):
			movie_ids.add(movie_id)
	
	return movie_ids


def extract_movie_ids_from_filmography(filmography: dict) -> set[int]:
	"""Extract movie IDs from company filmography."""
	movie_ids = set()
	
	pages = filmography.get("discover_movies_pages") or {}
	for payload in pages.values():
		if not isinstance(payload, dict):
			continue
		for movie in payload.get("results", []) or []:
			if not isinstance(movie, dict):
				continue
			movie_id = movie.get("id")
			if isinstance(movie_id, int):
				movie_ids.add(movie_id)
	
	return movie_ids


def extract_movie_release_dates_from_credits(credits: dict) -> dict[int, str]:
	"""Extract movie_id -> release_date (raw string) from person's credits."""
	by_id: dict[int, str] = {}
	for credit in (credits.get("cast") or []):
		if not isinstance(credit, dict):
			continue
		if credit.get("media_type") not in (None, "movie"):
			continue
		mid = credit.get("id")
		if not isinstance(mid, int):
			continue
		rd = _norm_date_str(credit.get("release_date"))
		# Prefer a non-empty value if we see it.
		if rd or mid not in by_id:
			by_id[mid] = rd

	for credit in (credits.get("crew") or []):
		if not isinstance(credit, dict):
			continue
		if credit.get("media_type") not in (None, "movie"):
			continue
		mid = credit.get("id")
		if not isinstance(mid, int):
			continue
		rd = _norm_date_str(credit.get("release_date"))
		if rd or mid not in by_id:
			by_id[mid] = rd

	return by_id


def extract_movie_release_dates_from_filmography(filmography: dict) -> dict[int, str]:
	"""Extract movie_id -> release_date (raw string) from company filmography."""
	by_id: dict[int, str] = {}
	pages = filmography.get("discover_movies_pages") or {}
	for payload in pages.values():
		if not isinstance(payload, dict):
			continue
		for movie in payload.get("results", []) or []:
			if not isinstance(movie, dict):
				continue
			mid = movie.get("id")
			if not isinstance(mid, int):
				continue
			rd = _norm_date_str(movie.get("release_date"))
			if rd or mid not in by_id:
				by_id[mid] = rd
	return by_id


def record_new_movie_arrivals(
	user: User,
	source_type: str,
	source_id: int,
	source_name: str,
	old_movie_ids: set[int],
	new_movie_ids: set[int],
	role: str = "",
	*,
	old_release_dates: dict[int, str] | None = None,
	new_release_dates: dict[int, str] | None = None,
	new_event_meta_by_movie: dict[int, dict] | None = None,
	source_last_sync_at: "datetime.datetime" | None = None,
) -> int:
	"""
	Record newly discovered movies.
	Returns count of new arrivals recorded.
	"""
	count = 0
	newly_arrived = new_movie_ids - old_movie_ids

	# Detect updates (currently: release_date changes).
	updated: dict[int, dict] = {}
	if old_release_dates is not None and new_release_dates is not None:
		common_ids = (new_movie_ids & old_movie_ids) - newly_arrived
		for mid in common_ids:
			old_rd = _norm_date_str(old_release_dates.get(mid))
			new_rd = _norm_date_str(new_release_dates.get(mid))
			# Only notify when the new value is non-empty (e.g. TBA -> real date)
			# or when the date actually changes.
			if not new_rd:
				continue
			if old_rd != new_rd:
				updated[mid] = {
					"field": "release_date",
					"old": old_rd,
					"new": new_rd,
				}

	if not newly_arrived and not updated:
		return 0

	# Import here to avoid circular imports
	from .models import Movie

	all_target_ids = set(newly_arrived) | set(updated.keys())

	# If a source sync time is provided, consult TMDb movie change history to
	# ensure the change is recent and actually recorded on TMDb for that movie.
	# This helps avoid surfacing old metadata differences that didn't originate
	# from a fresh edit on TMDb at sync time.
	if source_last_sync_at is not None and all_target_ids:
		from datetime import timedelta
		from .tmdb import TMDbClient
		client = TMDbClient.from_settings()
		allow_after = source_last_sync_at - timedelta(minutes=10)
		allowed_ids: set[int] = set()
		# Map of tmdb movie id -> latest relevant change datetime (aware or naive)
		recent_change_time_by_mid: dict[int, "datetime.datetime"] = {}
		for mid in list(all_target_ids):
			try:
				payload = client.get_movie_changes(mid)
			except Exception:
				# If TMDb fails, fall back to allowing the id so we don't drop
				# legitimate notifications due to transient network issues.
				allowed_ids.add(mid)
				continue
			# TMDb returns change groups in 'changes' or 'results'. Be flexible.
			groups = payload.get("changes") or payload.get("results") or []
			latest_time = None
			for g in groups:
				if not isinstance(g, dict):
					continue
				key = (g.get("key") or "")
				if not _change_key_matches_source(key, source_type=source_type):
					continue
				items = g.get("items") or g.get("changes") or []
				for it in items:
					if not isinstance(it, dict):
						continue
					# item time may be ISO-like or space-separated. Try parsing.
					time_str = it.get("time") or it.get("value") or ""
					if not isinstance(time_str, str) or not time_str:
						continue
					# Try common time parse formats.
					from datetime import datetime as _dt
					parsed = None
					try:
						parsed = _dt.fromisoformat(time_str)
					except Exception:
						try:
							parsed = _dt.strptime(time_str, "%Y-%m-%d %H:%M:%S")
						except Exception:
							parsed = None
					if parsed is None:
						continue
					if not _value_matches_source(it.get("value"), source_type=source_type, source_id=source_id, source_name=source_name, role=role):
						continue
					if latest_time is None or parsed > latest_time:
						latest_time = parsed
			# If we found a recent relevant change, allow this movie id.
			# If no relevant change info was present, allow the id as a safe default
			# so we don't suppress legitimate updates that TMDb doesn't expose as
			# granular change groups.
			if latest_time is None:
				allowed_ids.add(mid)
			elif latest_time is not None:
				from django.utils import timezone as _tz
				# Ensure naive/aware datetimes are comparable.
				if latest_time.tzinfo is None:
					try:
						latest_time = _tz.make_aware(latest_time)
					except Exception:
						pass
				if latest_time >= allow_after:
					allowed_ids.add(mid)
					# Record the latest change time for this movie id so we can attach
					# it to the NewMovieArrival.event_meta when creating notifications.
					recent_change_time_by_mid[mid] = latest_time
			# If no recent relevant TMDb edit is visible, do not promote the row.
			# This keeps both people and companies strict about not surfacing old data.
		# Limit targets to those allowed by TMDb change history.
		all_target_ids &= allowed_ids
		newly_arrived &= allowed_ids
		updated = {m: meta for m, meta in updated.items() if m in allowed_ids}

		# Attach TMDb change time into event_meta for allowed targets where available.
		if recent_change_time_by_mid:
			for mid, dt in list(recent_change_time_by_mid.items()):
				iso = None
				try:
					iso = dt.isoformat()
				except Exception:
					iso = None
				if iso is None:
					continue
				# Attach to new_event_meta_by_movie when present
				if isinstance(new_event_meta_by_movie, dict):
					meta = new_event_meta_by_movie.setdefault(mid, {})
					if isinstance(meta, dict) and "tmdb_edited_at" not in meta:
						meta["tmdb_edited_at"] = iso
				# Also attach to updated metadata entries
				if mid in updated and isinstance(updated.get(mid), dict) and "tmdb_edited_at" not in updated[mid]:
					updated[mid]["tmdb_edited_at"] = iso

	# For "new" events: seen = done across all sources.
	seen_new_tmdb_ids: set[int] = set()
	if newly_arrived:
		seen_new_tmdb_ids = set(
			NewMovieArrival.objects.filter(
				user=user,
				movie__tmdb_id__in=newly_arrived,
				event_type="new",
				is_seen=True,
			).values_list("movie__tmdb_id", flat=True)
		)

	movies_by_tmdb_id = {m.tmdb_id: m for m in Movie.objects.filter(tmdb_id__in=all_target_ids)}

	# Existing update events for this source (to avoid duplicate notifications).
	existing_updates = {
		a.movie.tmdb_id: a
		for a in NewMovieArrival.objects.select_related("movie").filter(
			user=user,
			source_type=source_type,
			source_id=source_id,
			event_type="update",
			movie__tmdb_id__in=updated.keys(),
		)
	}

	def _ensure_movie(mid: int) -> Movie | None:
		movie = movies_by_tmdb_id.get(mid)
		if movie:
			return movie
		try:
			from .services import get_or_sync_movie
			movie = get_or_sync_movie(mid, force=True)
		except Exception:
			movie = None
		if movie:
			movies_by_tmdb_id[mid] = movie
		return movie

	# Record "new" events.
	for movie_id in newly_arrived:
		if movie_id in seen_new_tmdb_ids:
			continue
		movie = _ensure_movie(movie_id)
		if not movie:
			continue
		event_meta = {}
		if isinstance(new_event_meta_by_movie, dict):
			meta = new_event_meta_by_movie.get(movie_id)
			if isinstance(meta, dict):
				event_meta = meta
		_, created = NewMovieArrival.objects.get_or_create(
			user=user,
			movie=movie,
			event_type="new",
			source_type=source_type,
			source_id=source_id,
			defaults={
				"source_name": source_name,
				"role": role,
				"event_meta": event_meta,
			},
		)
		if created:
			count += 1

	# Record "update" events (release_date changes).
	for movie_id, meta in updated.items():
		movie = _ensure_movie(movie_id)
		if not movie:
			continue
		existing = existing_updates.get(movie_id)
		if existing is not None and (existing.event_meta or {}) == meta and existing.is_seen is False:
			continue
		if existing is not None and (existing.event_meta or {}) == meta and existing.is_seen is True:
			# Same update already acknowledged.
			continue
		# Either a new update, or a changed update: replace the old row so it
		# surfaces again and re-notifies.
		if existing is not None:
			existing.delete()
		NewMovieArrival.objects.create(
			user=user,
			movie=movie,
			event_type="update",
			source_type=source_type,
			source_id=source_id,
			source_name=source_name,
			role=role,
			event_meta=meta,
			is_seen=False,
		)
		count += 1

	return count
