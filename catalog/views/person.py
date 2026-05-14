from __future__ import annotations

from datetime import date
import re
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.core.cache import cache

from ..models import PersonFollow
from ..new_movie_helpers import get_person_comeback_info
from ..services import get_or_sync_person
from ..tmdb import TMDbClient
from ._shared import (
	SESSION_KEY_HIDE_SELF_APPEARANCES,
	_countdown_text,
	_get_session_bool,
	_parse_iso_date,
	_person_role_options_from_credits,
)


_SELF_CHARACTER_RE = re.compile(r"\bself\b", re.IGNORECASE)


def _is_self_character(character: str) -> bool:
	ch = (character or "").strip()
	if not ch:
		return False
	ch_l = ch.lower()
	if _SELF_CHARACTER_RE.search(ch_l):
		return True
	# Common TMDb variants that imply a self appearance.
	self_variants = ("himself", "herself", "themselves", "archive footage")
	if any(variant in ch_l for variant in self_variants):
		return True
	return False


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
	note_text = (
		(follows_qs.order_by("-updated_at").values_list("notes", flat=True).first() or "")
		if is_followed
		else ""
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
		except Exception as exc:  # noqa: BLE001
			messages.error(request, f"TMDb error: {exc}")
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
	comeback_info = None
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
				if str(c.get("job") or "").strip().lower() == role_n:
					dept = str(c.get("department") or "").strip()
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
			"filmography_items": filmography_items,
			"comeback_info": comeback_info,
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
		except Exception as exc:  # noqa: BLE001
			messages.error(request, f"TMDb error: {exc}")
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
			"filmography_items": filmography_items,
			"filmography_filters": filmography_filters,
			"default_filmography_filter": default_filmography_filter,
			"hide_self_appearances": hide_self_appearances,
		},
	)


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
