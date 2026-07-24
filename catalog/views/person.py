from __future__ import annotations

from datetime import date
import re
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.cache import cache

from ..movie_accent import DEFAULT_MOVIE_ACCENT_COLOR, fallback_movie_accent_color
from ..models import DiaryEntry, Movie, Person, PersonFollow
from ..related_links import build_person_related_links
from ..new_movie_helpers import (
	build_person_comeback_event_meta,
	extract_movie_ids_from_credits,
	extract_movie_ids_from_credits_for_role,
	extract_movie_release_dates_from_credits_for_role,
	get_person_active_info,
	get_person_comeback_info,
	record_new_movie_arrivals,
)
from ..services import get_or_sync_person, get_or_sync_person_images, get_person_status_label
from ..rate_limit import rate_limit
from ..tmdb import TMDbClient, TMDbError, tmdb_image_url
from ._shared import (
	SESSION_KEY_HIDE_SELF_APPEARANCES,
	_countdown_text,
	_get_session_bool,
	_parse_iso_date,
	_person_role_options_from_credits,
)


_SELF_CHARACTER_RE = re.compile(r"\bself\b", re.IGNORECASE)
_NON_REAL_COLLAB_RE = re.compile(r"\b(?:special\s+)?thanks?\b|\bin\s+memory\s+of\b", re.IGNORECASE)


def _is_self_character(character: str) -> bool:
	ch = (character or "").strip()
	if not ch:
		return False
	ch_l = ch.lower()
	if _SELF_CHARACTER_RE.search(ch_l):
		return True
	if _NON_REAL_COLLAB_RE.search(ch_l):
		return True
	# Common TMDb variants that imply a self appearance.
	self_variants = ("himself", "herself", "themselves", "archive footage")
	if any(variant in ch_l for variant in self_variants):
		return True
	return False


def _is_documentary(item: dict) -> bool:
	genres = item.get("genre_ids", []) or []
	return 99 in genres


def _calculate_age(birthday: date | None, deathday: date | None = None) -> int | None:
	"""Calculate age from birthday to today (or to deathday if provided)."""
	if not birthday:
		return None
	end_date = deathday if deathday else timezone.now().date()
	age = end_date.year - birthday.year
	# Adjust if birthday hasn't occurred yet this year
	if (end_date.month, end_date.day) < (birthday.month, birthday.day):
		age -= 1
	return age if age >= 0 else None


def _normalize_movie_title(value: str) -> str:
	text = (value or "").casefold()
	for token in ("the ", "a ", "an "):
		if text.startswith(token):
			text = text[len(token) :]
			break
	return "".join(ch for ch in text if ch.isalnum() or ch.isspace()).strip()


def _person_profile_images_return_to(request: HttpRequest, tmdb_id: int) -> str:
	return_to = (request.GET.get("return_to") or request.POST.get("return_to") or "").strip()
	fallback = reverse("person_detail", args=[tmdb_id])
	if return_to and url_has_allowed_host_and_scheme(return_to, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
		return return_to
	return fallback


def _person_profile_image_candidates(person: Person) -> list[dict[str, object]]:
	raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
	images = raw.get("images") if isinstance(raw.get("images"), dict) else {}
	profiles = images.get("profiles") if isinstance(images, dict) else []
	if not isinstance(profiles, list):
		return []

	results: list[dict[str, object]] = []
	seen_paths: set[str] = set()
	for profile in profiles:
		if not isinstance(profile, dict):
			continue
		file_path = str(profile.get("file_path") or "").strip()
		if not file_path or file_path in seen_paths:
			continue
		seen_paths.add(file_path)
		results.append(
			{
				"file_path": file_path,
				"width": profile.get("width"),
				"height": profile.get("height"),
				"vote_average": profile.get("vote_average"),
				"vote_count": profile.get("vote_count"),
				"url": tmdb_image_url(file_path, size="w342"),
			}
		)
	return results


@rate_limit(limit=25, window_seconds=60, bucket_name="person_detail")
@login_required
def person_detail(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	hide_self_appearances = _get_session_bool(
		request.session,
		SESSION_KEY_HIDE_SELF_APPEARANCES,
		default=True,
	)

	follows_qs = PersonFollow.objects.select_related("person").filter(
		user=request.user, person__tmdb_id=tmdb_id
	)
	is_followed = follows_qs.exists()
	follow_roles = sorted(set(follows_qs.values_list("role", flat=True))) if is_followed else []
	follow_roles_set = set(follow_roles)
	follow_role_statuses: list[dict[str, str]] = []
	note_text = (
		(follows_qs.order_by("-updated_at").values_list("notes", flat=True).first() or "")
		if is_followed
		else ""
	)

	if is_followed:
		# Followed => store + serve from DB (refresh if stale).
		# If the TTL refresh updates cached credits, record any new arrivals for this user.
		old_person = follows_qs.first().person if follows_qs.exists() else None
		old_last_sync_at = getattr(old_person, "tmdb_last_sync_at", None) if old_person else None
		old_credits = getattr(old_person, "tmdb_credits_raw", None) or {}
		old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(old_credits.get("crew"), list)

		try:
			person = get_or_sync_person(tmdb_id)
		except TMDbError:
			messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
			return redirect("search")
		person_raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
		if not isinstance(person_raw.get("external_ids"), dict):
			client = TMDbClient.from_settings()
			try:
				external_ids = client.get_person_external_ids(tmdb_id)
			except Exception:
				external_ids = {}
			person.tmdb_raw = {**person_raw, "external_ids": external_ids}
			person.save(update_fields=["tmdb_raw", "updated_at"])
		# Keep denormalized follow snapshot fresh.
		PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id).update(name=person.name)

		new_last_sync_at = getattr(person, "tmdb_last_sync_at", None)
		source = (getattr(person, "tmdb_last_sync_source", "") or "").strip().lower()
		new_credits = person.tmdb_credits_raw or {}

		# Only treat TTL refreshes as background updates. Avoid notifying on first baseline cache fill.
		if (
			old_baseline_present
			and old_last_sync_at is not None
			and new_last_sync_at is not None
			and new_last_sync_at != old_last_sync_at
			and source == "ttl"
		):
			follows = PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id)
			for follow in follows:
				role = follow.role or ""
				old_role_movie_ids = extract_movie_ids_from_credits_for_role(old_credits, role)
				new_role_movie_ids = extract_movie_ids_from_credits_for_role(new_credits, role)
				if not old_role_movie_ids:
					continue
				old_role_release_dates = extract_movie_release_dates_from_credits_for_role(old_credits, role)
				new_role_release_dates = extract_movie_release_dates_from_credits_for_role(new_credits, role)
				new_event_meta_by_movie = build_person_comeback_event_meta(
					old_release_dates=old_role_release_dates,
					new_release_dates=new_role_release_dates,
					new_movie_ids=new_role_movie_ids,
				)
				# Add character names and credit jobs from new credits when available.
				if isinstance(new_credits, dict):
					for credit in (new_credits.get("cast") or []):
						if not isinstance(credit, dict):
							continue
						mid = credit.get("id")
						char = credit.get("character") or ""
						if isinstance(mid, int) and isinstance(char, str) and char.strip():
							meta = new_event_meta_by_movie.setdefault(mid, {}) if isinstance(new_event_meta_by_movie, dict) else {}
							if isinstance(meta, dict) and "character" not in meta:
								meta["character"] = char.strip()
							if isinstance(meta, dict) and "credit_job" not in meta:
								meta["credit_job"] = "Actor"
					for credit in (new_credits.get("crew") or []):
						if not isinstance(credit, dict):
							continue
						mid = credit.get("id")
						job = credit.get("job") or ""
						if isinstance(mid, int) and isinstance(job, str) and job.strip():
							meta = new_event_meta_by_movie.setdefault(mid, {}) if isinstance(new_event_meta_by_movie, dict) else {}
							if isinstance(meta, dict) and "credit_job" not in meta:
								meta["credit_job"] = job.strip()

				record_new_movie_arrivals(
					user=request.user,
					source_type="person",
					source_id=tmdb_id,
					source_name=person.name,
					old_movie_ids=old_role_movie_ids,
					new_movie_ids=new_role_movie_ids,
					role=role,
					old_release_dates=old_role_release_dates,
					new_release_dates=new_role_release_dates,
					new_event_meta_by_movie=new_event_meta_by_movie,
					source_last_sync_at=getattr(person, "tmdb_last_sync_at", None),
				)
	else:
		stored_person = Person.objects.filter(tmdb_id=tmdb_id).first()
		if stored_person is not None:
			try:
				person = get_or_sync_person(tmdb_id)
			except Exception:
				person = stored_person
		else:
			# Not followed => live fetch only (do not store in DB).
			client = TMDbClient.from_settings()
			try:
				raw = client.get_person(tmdb_id)
				credits = client.get_person_credits(tmdb_id)
				try:
					external_ids = client.get_person_external_ids(tmdb_id)
				except Exception:
					external_ids = {}
			except Exception:  # noqa: BLE001
				messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
				return redirect("search")
			if isinstance(raw, dict):
				raw = {**raw, "external_ids": external_ids}
			else:
				raw = {"external_ids": external_ids}
			person = SimpleNamespace(
				tmdb_id=tmdb_id,
				name=(raw.get("name") or str(tmdb_id)),
				profile_path=(raw.get("profile_path") or ""),
				tmdb_raw=raw,
				tmdb_credits_raw=credits,
				tmdb_last_sync_at=None,
			)

	credits = person.tmdb_credits_raw or {}
	if is_followed:
		try:
			client = TMDbClient.from_settings()
			live_credits = client.get_person_credits(tmdb_id)
			if isinstance(live_credits, dict):
				credits = live_credits
		except Exception:
			pass
	if is_followed:
		follow_role_statuses = [
			{
				"role": role,
				"status": get_person_status_label(person, followed_role=role),
			}
			for role in follow_roles
		]
	comeback_info = None
	active_info = None
	if is_followed:
		role_infos = [
			get_person_comeback_info(
				credits,
				followed_role=role,
				deathday=(person.tmdb_raw or {}).get("deathday"),
			)
			for role in follow_roles
		]
		role_infos = [info for info in role_infos if info is not None]
		if role_infos:
			comeback_info = max(role_infos, key=lambda info: int(info.get("gap_days") or 0))

		# Also compute active info for the followed roles so we can display
		# a historical "Was Active ..." line (useful for deceased profiles).
		active_role_infos = [
			get_person_active_info(
				credits,
				followed_role=role,
				deathday=(person.tmdb_raw or {}).get("deathday"),
			)
			for role in follow_roles
		]
		active_role_infos = [info for info in active_role_infos if info is not None]
		if active_role_infos:
			active_info = max(active_role_infos, key=lambda info: int(info.get("active_days") or 0))
	raw = person.tmdb_raw or {}
	bd = raw.get("birthday") if isinstance(raw, dict) else None
	pob = raw.get("place_of_birth") if isinstance(raw, dict) else None
	aka = raw.get("also_known_as") if isinstance(raw, dict) else None
	also_known_as: list[str] = []
	if isinstance(aka, list):
		also_known_as = [str(x).strip() for x in aka if str(x).strip()]

	# "Known for": derive from credits, ranked by popularity/votes.
	# If followed, prioritize movies where they had the followed role(s).
	known_for_items: list[dict[str, object]] = []
	try:
		def _normalize_known_for_role(value: str) -> str:
			value_n = (value or "").strip().lower()
			if value_n == "acting":
				return "actor"
			if value_n == "directing":
				return "director"
			if value_n == "writing":
				return "writer"
			if value_n == "production":
				return "producer"
			return value_n

		def _role_matches_followed_role(movie_role: str, followed_role: str) -> bool:
			movie_role_n = _normalize_known_for_role(movie_role)
			followed_role_n = _normalize_known_for_role(followed_role)
			if not movie_role_n or not followed_role_n:
				return False
			return movie_role_n == followed_role_n

		# Build role mapping for each movie
		movie_roles: dict[int, set[str]] = {}  # movie_id -> set of role names
		self_credit_movie_ids: set[int] = set()
		
		cast_items = [c for c in (credits.get("cast", []) or []) if c.get("media_type") in (None, "movie")]
		crew_items = [c for c in (credits.get("crew", []) or []) if c.get("media_type") in (None, "movie")]
		
		# Map cast to "Acting" role
		for item in cast_items:
			mid = item.get("id")
			if not isinstance(mid, int):
				continue
			character = str(item.get("character") or "").strip()
			if _is_self_character(character):
				self_credit_movie_ids.add(mid)
				if hide_self_appearances:
					continue
			if mid not in movie_roles:
				movie_roles[mid] = set()
			movie_roles[mid].add("Actor")
		
		# Map crew to their department/role
		for item in crew_items:
			mid = item.get("id")
			if not isinstance(mid, int):
				continue
			dept = str(item.get("department") or "").strip()
			job = str(item.get("job") or "").strip()
			if _is_self_character(job) or _is_self_character(dept):
				self_credit_movie_ids.add(mid)
				if hide_self_appearances:
					continue
			if not dept:
				job_l = job.lower()
				if job_l == "director":
					dept = "Directing"
				elif job_l == "producer":
					dept = "Production"
				elif job_l == "writer":
					dept = "Writing"
				else:
					dept = "Crew"
			if mid not in movie_roles:
				movie_roles[mid] = set()
			movie_roles[mid].add(dept)
			if job:
				movie_roles[mid].add(job)
		
		# Collect all items with scoring
		items = cast_items + crew_items
		dedup: dict[int, dict] = {}
		for it in items:
			mid = it.get("id")
			if not isinstance(mid, int):
				continue
			media_type = it.get("media_type")
			if media_type not in (None, "movie"):
				continue
			title = it.get("title") or it.get("name") or ""
			if not str(title).strip():
				continue
			if mid in self_credit_movie_ids:
				continue
			
			# If followed, only include if movie has one of the followed roles
			if is_followed and mid in movie_roles:
				if not any(
					_role_matches_followed_role(movie_role, follow_role)
					for movie_role in movie_roles[mid]
					for follow_role in follow_roles_set
				):
					continue
			
			popularity = it.get("popularity")
			vote_count = it.get("vote_count")
			vote_avg = it.get("vote_average")
			score = 0.0
			try:
				score = float(popularity or 0)
			except (TypeError, ValueError):
				score = 0.0
			try:
				score += min(float(vote_count or 0), 5000.0) / 1000.0
			except (TypeError, ValueError):
				pass
			try:
				score += float(vote_avg or 0) / 10.0
			except (TypeError, ValueError):
				pass

			prev = dedup.get(mid)
			if not prev or score > float(prev.get("_score") or 0):
				dedup[mid] = {
					"id": mid,
					"title": str(title),
					"_score": score,
				}

		sorted_items = sorted(dedup.values(), key=lambda d: float(d.get("_score") or 0), reverse=True)
		for it in sorted_items[:6]:
			known_for_items.append(
				{
					"id": it["id"],
					"title": it["title"],
				}
			)
	except Exception:
		known_for_items = []

	born_display = ""
	if bd:
		parsed = _parse_iso_date(str(bd))
		dd = raw.get("deathday")
		deathday_parsed = _parse_iso_date(str(dd)) if dd else None
		if parsed is not None:
			# Windows-friendly day formatting (avoid %-d).
			born_display = parsed.strftime("%B %d, %Y").replace(" 0", " ")
			if pob:
				born_display = f"{born_display} in {pob}"
			age = _calculate_age(parsed, deathday_parsed)
			if age is not None:
				born_display = f"{born_display} | Age: {age}"
		else:
			born_display = str(bd)
			if pob:
				born_display = f"{born_display} in {pob}"
	elif pob:
		born_display = str(pob)
	role_options = _person_role_options_from_credits(credits)
	role_options_remaining = [r for r in role_options if r not in follow_roles_set]

	# Build unified filmography (one row per movie), with roles grouped by department.
	today = timezone.now().date()
	movie_map: dict[int, dict] = {}

	def _ensure_movie(mid: int, item: dict) -> dict:
		m = movie_map.get(mid)
		if not m:
			m = {
				"id": mid,
				"title": str(item.get("title") or item.get("name") or "").strip(),
				"poster_path": str(item.get("poster_path") or ""),
				"release_date": str(item.get("release_date") or "").strip(),
				"release_dt": _parse_iso_date(item.get("release_date")),
				"roles_by_filter": {},  # key -> set[str]
			}
			movie_map[mid] = m
		# Prefer better title / poster / release_date if missing.
		if not m.get("title"):
			m["title"] = str(item.get("title") or item.get("name") or "").strip()
		if not m.get("poster_path") and item.get("poster_path"):
			m["poster_path"] = str(item.get("poster_path") or "")
		if not m.get("release_date") and item.get("release_date"):
			m["release_date"] = str(item.get("release_date") or "").strip()
			m["release_dt"] = _parse_iso_date(item.get("release_date"))
		return m

	def _add_role(mid: int, item: dict, filter_key: str, role_text: str) -> None:
		m = _ensure_movie(mid, item)
		bucket = m["roles_by_filter"].get(filter_key)
		if not bucket:
			bucket = set()
			m["roles_by_filter"][filter_key] = bucket
		if role_text:
			bucket.add(role_text)

	for item in cast_items:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		if _is_documentary(item):
			continue
		character = str(item.get("character") or "").strip()
		if hide_self_appearances and _is_self_character(character):
			continue
		# Acting tab should show character name (if available).
		role_text = character if character else "Actor"
		_add_role(mid, item, "Acting", role_text)

	for item in crew_items:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		if _is_documentary(item):
			continue
		dept = str(item.get("department") or "").strip()
		job = str(item.get("job") or "").strip()
		# Skip self-appearance crew roles when hide_self_appearances is enabled
		if hide_self_appearances and (_is_self_character(job) or _is_self_character(dept)):
			continue
		if not dept:
			job_l = job.lower()
			if job_l == "director":
				dept = "Directing"
			elif job_l == "producer":
				dept = "Production"
			elif job_l == "writer":
				dept = "Writing"
			else:
				dept = "Crew"
		_add_role(mid, item, dept, job or "Crew")

	# Sort and shape for template
	def _sorted_values(values: set[str]) -> list[str]:
		return sorted([str(v).strip() for v in values if str(v).strip()], key=lambda s: s.lower())

	def _role_str(*, dept: str, value: str, mode: str) -> str:
		# mode: "all" (cross-tab) or "acting" (Acting tab)
		v = str(value).strip()
		if not v:
			return ""
		if dept == "Acting":
			if v.lower() == "actor":
				return "Actor"
			# Always show character name plainly.
			return v
		return v

	def _roles_list(*, dept: str, values: set[str], mode: str) -> list[str]:
		return [
			s
			for s in (
				_role_str(dept=dept, value=v, mode=mode) for v in _sorted_values(values)
			)
			if s
		]

	preferred_filter_order = [
		"Acting",
		"Directing",
		"Writing",
		"Production",
		"Camera",
		"Sound",
		"Editing",
		"Art",
	]
	preferred_rank = {k: i for i, k in enumerate(preferred_filter_order)}

	filmography_items: list[dict[str, object]] = []
	for m in movie_map.values():
		release_dt = m.get("release_dt")
		release_dt = release_dt if isinstance(release_dt, date) else None
		year = str(release_dt.year) if release_dt else ""
		roles_by_filter: dict[str, set[str]] = m.get("roles_by_filter") or {}
		keys = list(roles_by_filter.keys())
		keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))
		ordered_keys = keys[:]
		ordered_keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))

		# "All" roles: show the prioritized role first, then the rest on a second line.
		roles_all_primary_parts: list[str] = []
		roles_all_secondary_parts: list[str] = []
		if ordered_keys:
			primary_key = ordered_keys[0]
			roles_all_primary_parts = _roles_list(
				dept=primary_key,
				values=roles_by_filter.get(primary_key) or set(),
				mode="acting" if primary_key == "Acting" else "all",
			)
			for k in ordered_keys[1:]:
				# Do not show Acting character names on crew tabs.
				if primary_key != "Acting" and k == "Acting":
					continue
				roles_all_secondary_parts.extend(
					_roles_list(dept=k, values=roles_by_filter.get(k) or set(), mode="all")
				)
		roles_all_primary_parts = [x for x in roles_all_primary_parts if x]
		roles_all_secondary_parts = [x for x in roles_all_secondary_parts if x]
		roles_all_parts = roles_all_primary_parts + roles_all_secondary_parts

		# Per-filter display: prioritize the active dept, then show other roles on the next line.
		roles_display_by_filter: list[dict[str, object]] = []
		for active in ordered_keys:
			active_mode = "acting" if active == "Acting" else "all"
			primary_parts = _roles_list(
				dept=active,
				values=roles_by_filter.get(active) or set(),
				mode=active_mode,
			)
			secondary_parts: list[str] = []
			for k in ordered_keys:
				if k == active:
					continue
				# Do not show Acting character names on crew tabs.
				if active != "Acting" and k == "Acting":
					continue
				secondary_parts.extend(
					_roles_list(dept=k, values=roles_by_filter.get(k) or set(), mode="all")
				)
			primary_parts = [x for x in primary_parts if x]
			secondary_parts = [x for x in secondary_parts if x]
			if primary_parts or secondary_parts:
				roles_display_by_filter.append(
					{
						"key": active,
						"primary_parts": primary_parts,
						"secondary_parts": secondary_parts,
					}
				)

		filmography_items.append(
			{
				"id": m.get("id"),
				"title": m.get("title") or "-",
				"poster_path": m.get("poster_path") or "",
				"release_dt": release_dt,
				"year": year,
				"countdown_text": _countdown_text(today=today, release_dt=release_dt)
				if release_dt is not None
				else "",
				"filters": keys,
				"roles_all_parts": roles_all_parts,
				"roles_all_primary_parts": roles_all_primary_parts,
				"roles_all_secondary_parts": roles_all_secondary_parts,
				"roles_by_filter": roles_display_by_filter,
			}
		)

	def _film_sort_key(it: dict[str, object]):
		# Newest-to-oldest by release date (including future), unknown dates last.
		rd = it.get("release_dt")
		rd = rd if isinstance(rd, date) else None
		if rd is None:
			group = 1
			ord_key = 0
		else:
			group = 0
			ord_key = -rd.toordinal()
		title = str(it.get("title") or "").lower()
		return (group, ord_key, title)

	filmography_items.sort(key=_film_sort_key)

	# Filter counts (movies per filter)
	filter_counts: dict[str, int] = {}
	for it in filmography_items:
		for k in (it.get("filters") or []):
			filter_counts[k] = filter_counts.get(k, 0) + 1

	def _filter_label(key: str) -> str:
		mapping = {
			"Acting": "Actor",
			"Directing": "Director",
			"Writing": "Writer",
			"Production": "Producer",
			"Camera": "Cinematography",
			"Editing": "Editor",
		}
		return mapping.get(key, key)

	filter_keys = list(filter_counts.keys())
	filter_keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))
	filmography_filters = [{"key": "all", "label": "All", "count": len(filmography_items)}]
	for k in filter_keys:
		filmography_filters.append({"key": k, "label": _filter_label(k), "count": filter_counts.get(k, 0)})

	# Mark watched films using the user's diary.
	# Prefer the TMDb id join, then fall back to a title/year lookup for older
	# diary rows that may only have a matched title.
	current_movie_ids: set[int] = set()
	watched_by_movie_id: dict[int, dict[str, object]] = {}
	accent_color_by_movie_id: dict[int, str] = {}
	if filmography_items:
		current_movie_ids = {
			int(it.get("id"))
			for it in filmography_items
			if isinstance(it, dict) and isinstance(it.get("id"), int)
		}
		accent_color_by_movie_id = dict(
			Movie.objects.filter(tmdb_id__in=current_movie_ids).values_list("tmdb_id", "accent_color")
		)
		filmography_title_lookup: dict[tuple[str, int | None], int] = {}
		for it in filmography_items:
			mid = int(it.get("id") or 0)
			if mid <= 0:
				continue
			title = str(it.get("title") or "").strip()
			title_key = _normalize_movie_title(title)
			if not title_key:
				continue
			year_raw = str(it.get("year") or "").strip()
			year = int(year_raw) if year_raw.isdigit() else None
			filmography_title_lookup.setdefault((title_key, year), mid)
			filmography_title_lookup.setdefault((title_key, None), mid)

		diary_entries = (
			DiaryEntry.objects.filter(user=request.user)
			.only(
				"tmdb_id",
				"official_title",
				"original_title",
				"original_release_year",
				"poster_path",
				"watched_date",
				"created_at",
				"id",
				"release_date",
			)
			.order_by("-watched_date", "-created_at", "-id")
		)
		for entry in diary_entries:
			movie_id = int(entry.tmdb_id or 0)
			if movie_id not in current_movie_ids:
				title = (entry.official_title or entry.original_title or "").strip()
				if title:
					title_key = _normalize_movie_title(title)
					if title_key:
						year = None
						if entry.release_date is not None:
							year = entry.release_date.year
						elif entry.original_release_year is not None:
							year = int(entry.original_release_year)
						movie_id = filmography_title_lookup.get((title_key, year), 0)
						if movie_id <= 0:
							movie_id = filmography_title_lookup.get((title_key, None), 0)
			if movie_id <= 0 or movie_id in watched_by_movie_id:
				continue
			watched_by_movie_id[movie_id] = {
				"poster_path": entry.poster_path or "",
				"watched_date": entry.watched_date,
			}

	for it in filmography_items:
		mid = int(it.get("id") or 0)
		watch_info = watched_by_movie_id.get(mid)
		it["is_watched"] = watch_info is not None
		watched_poster_path = str((watch_info or {}).get("poster_path") or "").strip()
		display_poster_path = watched_poster_path or str(it.get("poster_path") or "").strip()
		it["display_poster_path"] = display_poster_path
		it["watched_date"] = (watch_info or {}).get("watched_date")
		accent_color = str(accent_color_by_movie_id.get(mid) or "").strip()
		if not accent_color or accent_color == DEFAULT_MOVIE_ACCENT_COLOR:
			accent_color = fallback_movie_accent_color(display_poster_path or str(mid))
		it["accent_color"] = accent_color or DEFAULT_MOVIE_ACCENT_COLOR

	shared_followed_people: list[dict[str, object]] = []
	try:
		# Exclude movies where the current person has any self/archive-style credit
		current_credits = credits or {}
		def _current_has_no_self_credit(mid: int) -> bool:
			# If any cast entry for this movie has a self-like character, exclude.
			for c in (current_credits.get("cast") or []):
				if not isinstance(c, dict):
					continue
				if c.get("id") != mid:
					continue
				char = str(c.get("character") or "")
				if _is_self_character(char):
					return False
			# If any crew entry for this movie has a self-like job/department, exclude.
			for c in (current_credits.get("crew") or []):
				if not isinstance(c, dict):
					continue
				if c.get("id") != mid:
					continue
				job = str(c.get("job") or "")
				dept = str(c.get("department") or "")
				if _is_self_character(job) or _is_self_character(dept):
					return False
			return True

		current_movie_ids = {mid for mid in current_movie_ids if _current_has_no_self_credit(mid)}
		followed_people_qs = (
			PersonFollow.objects.select_related("person")
			.filter(user=request.user)
			.exclude(person__tmdb_id=tmdb_id)
			.order_by("person__name", "person__tmdb_id", "role")
		)
		seen_followed_people: set[int] = set()
		for follow in followed_people_qs:
			followed_person = getattr(follow, "person", None)
			followed_tmdb_id = int(getattr(followed_person, "tmdb_id", 0) or 0)
			if followed_person is None or followed_tmdb_id <= 0 or followed_tmdb_id in seen_followed_people:
				continue

			followed_credits = getattr(followed_person, "tmdb_credits_raw", None) or {}
			other_movie_ids = extract_movie_ids_from_credits(followed_credits)

			# Exclude movies where the followed person only appears as "self" / "archive footage"
			def _followed_has_no_self_credit(mid: int) -> bool:
				# Return False if any cast entry is self-like
				for c in (followed_credits.get("cast") or []):
					if not isinstance(c, dict):
						continue
					if c.get("id") != mid:
						continue
					if _is_self_character(str(c.get("character") or "")):
						return False
				# Return False if any crew entry is self-like
				for c in (followed_credits.get("crew") or []):
					if not isinstance(c, dict):
						continue
					if c.get("id") != mid:
						continue
					if _is_self_character(str(c.get("job") or "")) or _is_self_character(str(c.get("department") or "")):
						return False
				return True

			other_movie_ids_filtered = {mid for mid in other_movie_ids if _followed_has_no_self_credit(mid)}
			shared_movie_ids = current_movie_ids & other_movie_ids_filtered
			if not shared_movie_ids:
				continue

			seen_followed_people.add(followed_tmdb_id)
			shared_followed_people.append(
				{
					"tmdb_id": followed_tmdb_id,
					"name": getattr(followed_person, "name", str(followed_tmdb_id)),
					"profile_path": getattr(followed_person, "profile_path", "") or "",
					"deathday": (getattr(followed_person, "tmdb_raw", {}) or {}).get("deathday"),
					"known_for_department": str(
						((getattr(followed_person, "tmdb_raw", {}) or {}).get("known_for_department") or "")
						.strip()
						or (getattr(follow, "role", "") or "").strip()
					),
					"shared_count": len(shared_movie_ids),
				}
			)

		shared_followed_people.sort(
			key=lambda item: (-int(item.get("shared_count") or 0), str(item.get("name") or "").lower())
		)
	except Exception:
		shared_followed_people = []

	def _follow_role_default_filter() -> str:
		if not follow_roles:
			return "all"
		preferred = {"director": 0, "actor": 1}
		ordered = sorted(
			follow_roles,
			key=lambda r: (preferred.get((r or "").strip().lower(), 99), r.lower()),
		)
		# Try to map followed role -> department filter key.
		for role in ordered:
			role_n = (role or "").strip().lower()
			if role_n == "actor":
				if filter_counts.get("Acting"):
					return "Acting"
			if role_n == "director":
				if filter_counts.get("Directing"):
					return "Directing"
			# If it's a crew job, infer department from credits.
			for c in crew_items:
				job = str(c.get("job") or "").strip().lower()
				job_tokens = [token.strip() for token in job.replace("/", ",").replace(";", ",").replace("|", ",").split(",") if token.strip()]
				if role_n == job or role_n in job_tokens:
					dept = str(c.get("department") or "").strip()
					if dept and filter_counts.get(dept):
						return dept
			# As a fallback, allow direct department match.
			if role and filter_counts.get(role):
				return role
		return "all"

	default_filmography_filter = _follow_role_default_filter()
	related_links = build_person_related_links(tmdb_id, raw)

	return render(
		request,
		"catalog/person_detail.html",
		{
			"person": person,
			"is_followed": is_followed,
			"follow_roles": follow_roles,
			"follow_role_statuses": follow_role_statuses,
			"role_options": role_options,
			"role_options_remaining": role_options_remaining,
			"shared_followed_people": shared_followed_people,
			"note_text": note_text,
			"born_display": born_display,
			"also_known_as": also_known_as,
			"known_for_items": known_for_items,
			"related_links": related_links,
			"filmography_items": filmography_items,
			"comeback_info": comeback_info,
			"active_info": active_info,
			"filmography_filters": filmography_filters,
			"default_filmography_filter": default_filmography_filter,
			"hide_self_appearances": hide_self_appearances,
		},
	)

	if is_followed:
		# Followed => store + serve from DB (refresh if stale).
		person = get_or_sync_person(tmdb_id)
	else:
		# Not followed => live fetch only (do not store in DB).
		client = TMDbClient.from_settings()
		try:
			raw = client.get_person(tmdb_id)
			credits = client.get_person_credits(tmdb_id)
		except Exception:  # noqa: BLE001
			messages.error(request, "TMDb data is temporarily unavailable. Please try again soon.")
			return redirect("search")
		person = SimpleNamespace(
			tmdb_id=tmdb_id,
			name=(raw.get("name") or str(tmdb_id)),
			profile_path=(raw.get("profile_path") or ""),
			tmdb_raw=raw,
			tmdb_credits_raw=credits,
			tmdb_last_sync_at=None,
		)

	credits = person.tmdb_credits_raw or {}
	raw = person.tmdb_raw or {}
	bd = raw.get("birthday") if isinstance(raw, dict) else None
	pob = raw.get("place_of_birth") if isinstance(raw, dict) else None
	aka = raw.get("also_known_as") if isinstance(raw, dict) else None
	also_known_as: list[str] = []
	if isinstance(aka, list):
		also_known_as = [str(x).strip() for x in aka if str(x).strip()]

	# Derived payloads (known-for + filmography) can be expensive; cache them.
	last_sync = getattr(person, "tmdb_last_sync_at", None)
	last_sync_key = last_sync.isoformat() if last_sync else "live"
	derived_cache_key = f"person:derived:v2:{tmdb_id}:{int(hide_self_appearances)}:{last_sync_key}"
	derived = cache.get(derived_cache_key)
	if is_followed:
		derived = None
	born_display = ""
	if bd:
		parsed = _parse_iso_date(str(bd))
		dd = raw.get("deathday")
		deathday_parsed = _parse_iso_date(str(dd)) if dd else None
		if parsed is not None:
			# Windows-friendly day formatting (avoid %-d).
			born_display = parsed.strftime("%B %d, %Y").replace(" 0", " ")
			if pob:
				born_display = f"{born_display} in {pob}"
			age = _calculate_age(parsed, deathday_parsed)
			if age is not None:
				born_display = f"{born_display} | Age: {age}"
		else:
			born_display = str(bd)
			if pob:
				born_display = f"{born_display} in {pob}"
	elif pob:
		born_display = str(pob)
	role_options = _person_role_options_from_credits(credits)
	role_options_remaining = [r for r in role_options if r not in follow_roles_set]
	related_links = build_person_related_links(tmdb_id, raw)

	known_for_items: list[dict[str, object]] = []
	filmography_items: list[dict[str, object]] = []
	filmography_filters: list[dict[str, object]] = []
	filter_counts: dict[str, int] = {}
	job_to_dept: dict[str, str] = {}

	if isinstance(derived, dict):
		known_for_items = list(derived.get("known_for_items") or [])
		filmography_items = list(derived.get("filmography_items") or [])
		filmography_filters = list(derived.get("filmography_filters") or [])
		filter_counts = dict(derived.get("filter_counts") or {})
		job_to_dept = dict(derived.get("job_to_dept") or {})
	else:
		cast_items = [c for c in (credits.get("cast", []) or []) if c.get("media_type") in (None, "movie")]
		crew_items = [c for c in (credits.get("crew", []) or []) if c.get("media_type") in (None, "movie")]
		job_to_dept = {
			str(c.get("job") or "").strip().lower(): str(c.get("department") or "").strip()
			for c in crew_items
			if isinstance(c, dict) and str(c.get("job") or "").strip() and str(c.get("department") or "").strip()
		}

		# "Known for": derive from combined credits, ranked by popularity/votes.
		known_for_items = []
		try:
			items = list(cast_items) + list(crew_items)
			dedup: dict[int, dict] = {}
			for it in items:
				mid = it.get("id")
				if not isinstance(mid, int):
					continue
				media_type = it.get("media_type")
				if media_type not in (None, "movie"):
					continue
				title = it.get("title") or it.get("name") or ""
				if not str(title).strip():
					continue
				popularity = it.get("popularity")
				vote_count = it.get("vote_count")
				vote_avg = it.get("vote_average")
				score = 0.0
				try:
					score = float(popularity or 0)
				except (TypeError, ValueError):
					score = 0.0
				try:
					score += min(float(vote_count or 0), 5000.0) / 1000.0
				except (TypeError, ValueError):
					pass
				try:
					score += float(vote_avg or 0) / 10.0
				except (TypeError, ValueError):
					pass

				prev = dedup.get(mid)
				if not prev or score > float(prev.get("_score") or 0):
					dedup[mid] = {
						"id": mid,
						"title": str(title),
						"_score": score,
					}

			sorted_items = sorted(dedup.values(), key=lambda d: float(d.get("_score") or 0), reverse=True)
			for it in sorted_items[:6]:
				known_for_items.append(
					{
						"id": it["id"],
						"title": it["title"],
					}
				)
		except Exception:
			known_for_items = []

		# Build unified filmography (one row per movie), with roles grouped by department.
		today = timezone.now().date()
		movie_map: dict[int, dict] = {}

	def _ensure_movie(mid: int, item: dict) -> dict:
		m = movie_map.get(mid)
		if not m:
			m = {
				"id": mid,
				"title": str(item.get("title") or item.get("name") or "").strip(),
				"poster_path": str(item.get("poster_path") or ""),
				"release_date": str(item.get("release_date") or "").strip(),
				"release_dt": _parse_iso_date(item.get("release_date")),
				"roles_by_filter": {},  # key -> set[str]
			}
			movie_map[mid] = m
		# Prefer better title / poster / release_date if missing.
		if not m.get("title"):
			m["title"] = str(item.get("title") or item.get("name") or "").strip()
		if not m.get("poster_path") and item.get("poster_path"):
			m["poster_path"] = str(item.get("poster_path") or "")
		if not m.get("release_date") and item.get("release_date"):
			m["release_date"] = str(item.get("release_date") or "").strip()
			m["release_dt"] = _parse_iso_date(item.get("release_date"))
		return m

	def _add_role(mid: int, item: dict, filter_key: str, role_text: str) -> None:
		m = _ensure_movie(mid, item)
		bucket = m["roles_by_filter"].get(filter_key)
		if not bucket:
			bucket = set()
			m["roles_by_filter"][filter_key] = bucket
		if role_text:
			bucket.add(role_text)

	for item in cast_items:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		character = str(item.get("character") or "").strip()
		if hide_self_appearances and _is_self_character(character):
			continue
		# Acting tab should show character name (if available).
		role_text = character if character else "Actor"
		_add_role(mid, item, "Acting", role_text)

	for item in crew_items:
		mid = item.get("id")
		if not isinstance(mid, int):
			continue
		dept = str(item.get("department") or "").strip()
		job = str(item.get("job") or "").strip()
		# Skip self-appearance crew roles when hide_self_appearances is enabled
		if hide_self_appearances and (_is_self_character(job) or _is_self_character(dept)):
			continue
		if not dept:
			job_l = job.lower()
			if job_l == "director":
				dept = "Directing"
			elif job_l == "producer":
				dept = "Production"
			elif job_l == "writer":
				dept = "Writing"
			else:
				dept = "Crew"
		_add_role(mid, item, dept, job or "Crew")

	# Sort and shape for template
	def _sorted_values(values: set[str]) -> list[str]:
		return sorted([str(v).strip() for v in values if str(v).strip()], key=lambda s: s.lower())

	def _role_str(*, dept: str, value: str, mode: str) -> str:
		# mode: "all" (cross-tab) or "acting" (Acting tab)
		v = str(value).strip()
		if not v:
			return ""
		if dept == "Acting":
			if v.lower() == "actor":
				return "Actor"
			# Always show character name plainly.
			return v
		return v

	def _roles_list(*, dept: str, values: set[str], mode: str) -> list[str]:
		return [
			s
			for s in (
				_role_str(dept=dept, value=v, mode=mode) for v in _sorted_values(values)
			)
			if s
		]

	preferred_filter_order = [
		"Acting",
		"Directing",
		"Writing",
		"Production",
		"Camera",
		"Sound",
		"Editing",
		"Art",
	]
	preferred_rank = {k: i for i, k in enumerate(preferred_filter_order)}

	filmography_items: list[dict[str, object]] = []
	for m in movie_map.values():
		release_dt = m.get("release_dt")
		release_dt = release_dt if isinstance(release_dt, date) else None
		year = str(release_dt.year) if release_dt else ""
		roles_by_filter: dict[str, set[str]] = m.get("roles_by_filter") or {}
		keys = list(roles_by_filter.keys())
		keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))
		ordered_keys = keys[:]
		ordered_keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))

		# "All" roles: show all credited roles across departments.
		roles_all_list: list[str] = []
		for k in ordered_keys:
			roles_all_list.extend(_roles_list(dept=k, values=roles_by_filter.get(k) or set(), mode="all"))
		roles_all_parts = [x for x in roles_all_list if x]

		# Per-filter display: prioritize the active dept, but include other roles too.
		roles_display_by_filter: list[dict[str, object]] = []
		for active in ordered_keys:
			active_mode = "acting" if active == "Acting" else "all"
			parts: list[str] = []
			parts.extend(
				_roles_list(
					dept=active,
					values=roles_by_filter.get(active) or set(),
					mode=active_mode,
				)
			)
			for k in ordered_keys:
				if k == active:
					continue
				# Do not show Acting character names on crew tabs.
				if active != "Acting" and k == "Acting":
					continue
				parts.extend(_roles_list(dept=k, values=roles_by_filter.get(k) or set(), mode="all"))
			parts_clean = [x for x in parts if x]
			if parts_clean:
				roles_display_by_filter.append({"key": active, "parts": parts_clean})

		filmography_items.append(
			{
				"id": m.get("id"),
				"title": m.get("title") or "-",
				"poster_path": m.get("poster_path") or "",
				"release_dt": release_dt,
				"year": year,
				"countdown_text": _countdown_text(today=today, release_dt=release_dt)
				if release_dt is not None
				else "",
				"filters": keys,
				"roles_all_parts": roles_all_parts,
				"roles_by_filter": roles_display_by_filter,
			}
		)

	def _film_sort_key(it: dict[str, object]):
		# Newest-to-oldest by release date (including future), unknown dates last.
		rd = it.get("release_dt")
		rd = rd if isinstance(rd, date) else None
		if rd is None:
			group = 1
			ord_key = 0
		else:
			group = 0
			ord_key = -rd.toordinal()
		title = str(it.get("title") or "").lower()
		return (group, ord_key, title)

		filmography_items.sort(key=_film_sort_key)

		# Filter counts (movies per filter)
		filter_counts = {}
		for it in filmography_items:
			for k in (it.get("filters") or []):
				filter_counts[k] = filter_counts.get(k, 0) + 1

	def _filter_label(key: str) -> str:
		mapping = {
			"Acting": "Actor",
			"Directing": "Director",
			"Writing": "Writer",
			"Production": "Producer",
			"Camera": "Cinematography",
			"Editing": "Editor",
		}
		return mapping.get(key, key)

		filter_keys = list(filter_counts.keys())
		filter_keys.sort(key=lambda k: (preferred_rank.get(k, 99), k.lower()))
		filmography_filters = [{"key": "all", "label": "All", "count": len(filmography_items)}]
		for k in filter_keys:
			filmography_filters.append({"key": k, "label": _filter_label(k), "count": filter_counts.get(k, 0)})

		# Cache derived result (no user-specific follow data).
		try:
			cache.set(
				derived_cache_key,
				{
					"known_for_items": known_for_items,
					"filmography_items": filmography_items,
					"filmography_filters": filmography_filters,
					"filter_counts": filter_counts,
					"job_to_dept": job_to_dept,
				},
				timeout=6 * 60 * 60,
			)
		except Exception:
			pass

	# countdown_text changes with date; compute on every request.
	today = timezone.now().date()
	for it in filmography_items:
		rd = it.get("release_dt")
		rd = rd if isinstance(rd, date) else None
		it["countdown_text"] = _countdown_text(today=today, release_dt=rd) if rd is not None else ""

	def _follow_role_default_filter() -> str:
		if not follow_roles:
			return "all"
		preferred = {"director": 0, "actor": 1}
		ordered = sorted(follow_roles, key=lambda r: (preferred.get((r or "").strip().lower(), 99), r.lower()))
		# Try to map followed role -> department filter key.
		for role in ordered:
			role_n = (role or "").strip().lower()
			if role_n == "actor":
				if filter_counts.get("Acting"):
					return "Acting"
			if role_n == "director":
				if filter_counts.get("Directing"):
					return "Directing"
			# If it's a crew job, infer department from credits.
			dept = (job_to_dept.get(role_n) or "").strip()
			if dept and filter_counts.get(dept):
				return dept
			# As a fallback, allow direct department match.
			if role and filter_counts.get(role):
				return role
		return "all"

	default_filmography_filter = _follow_role_default_filter()

	return render(
		request,
		"catalog/person_detail.html",
		{
			"person": person,
			"is_followed": is_followed,
			"follow_roles": follow_roles,
			"role_options": role_options,
			"role_options_remaining": role_options_remaining,
			"note_text": note_text,
			"born_display": born_display,
			"also_known_as": also_known_as,
			"known_for_items": known_for_items,
			"related_links": related_links,
			"filmography_items": filmography_items,
			"filmography_filters": filmography_filters,
			"default_filmography_filter": default_filmography_filter,
			"hide_self_appearances": hide_self_appearances,
		},
	)


@login_required
def person_profile_images(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	return_to = _person_profile_images_return_to(request, tmdb_id)
	load_error = ""
	is_followed = PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id).exists()
	if not is_followed:
		messages.error(request, "Profile pics are available only for followed people.")
		return redirect(return_to)

	if request.method == "POST":
		try:
			person = get_or_sync_person_images(tmdb_id)
		except Exception:
			messages.error(request, "TMDb profile images are temporarily unavailable. Please try again soon.")
			return redirect(return_to)

		selected_profile_path = (request.POST.get("profile_path") or "").strip()
		candidates = _person_profile_image_candidates(person)
		allowed_paths = {str(candidate.get("file_path") or "").strip() for candidate in candidates}
		if selected_profile_path not in allowed_paths:
			messages.error(request, "Selected profile image is no longer available.")
			return redirect(reverse("person_profile_images", args=[tmdb_id]))

		person.profile_path = selected_profile_path
		person.save(update_fields=["profile_path", "updated_at"])
		try:
			cache.delete(f"db:person:v1:{int(tmdb_id)}")
		except Exception:
			pass
		return redirect(return_to)

	try:
		person = get_or_sync_person_images(tmdb_id)
		candidates = _person_profile_image_candidates(person)
	except Exception:
		load_error = "TMDb profile images are temporarily unavailable right now."
		candidates = []
		stored_person = Person.objects.filter(tmdb_id=tmdb_id).first()
		if stored_person is not None:
			person = stored_person
		else:
			try:
				person = get_or_sync_person(tmdb_id)
			except Exception:
				person = SimpleNamespace(
					tmdb_id=tmdb_id,
					name=str(tmdb_id),
					profile_path="",
					tmdb_raw={},
				)

	selected_profile_path = str(getattr(person, "profile_path", "") or "").strip()
	current_image = next((item for item in candidates if item.get("file_path") == selected_profile_path), None)
	current_profile_image_url = tmdb_image_url(selected_profile_path, size="w342") if selected_profile_path else ""

	return render(
		request,
		"catalog/person_profile_images.html",
		{
			"person": person,
			"images": candidates,
			"image_count": len(candidates),
			"current_image": current_image,
			"selected_profile_path": selected_profile_path,
			"current_profile_image_url": current_profile_image_url,
			"return_to": return_to,
			"load_error": load_error,
		},
	)


@rate_limit(limit=20, window_seconds=60, bucket_name="person_toggle_self_appearances")
@login_required
def person_toggle_self_appearances(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("person_detail", tmdb_id=tmdb_id)

	current = _get_session_bool(
		request.session,
		SESSION_KEY_HIDE_SELF_APPEARANCES,
		default=True,
	)
	request.session[SESSION_KEY_HIDE_SELF_APPEARANCES] = not current
	request.session.modified = True

	return redirect("person_detail", tmdb_id=tmdb_id)
