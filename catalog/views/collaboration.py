from __future__ import annotations

from datetime import date
import random
import re
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from ..models import Person, PersonFollow
from ..tmdb import TMDbClient
from ._shared import _parse_iso_date

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
	self_variants = ("himself", "herself", "themselves", "archive footage")
	return any(variant in ch_l for variant in self_variants)


def _role_priority(role: str) -> tuple[int, str]:
	value = (role or "").strip()
	value_l = value.lower()
	priority_map = {
		"director": 0,
		"producer": 1,
		"screenplay": 2,
		"story": 3,
		"writer": 4,
		"actor": 5,
	}
	return (priority_map.get(value_l, 50), value_l)


def _is_documentary(item: dict) -> bool:
	"""Check if a movie item is a documentary based on genres."""
	genres = item.get("genre_ids", []) or []
	# Genre ID 99 is typically Documentary in TMDB
	return 99 in genres


def _get_frequent_collaborators(
	user_id: int,
	limit: int | None = None,
	*,
	sort_mode: str = "random",
	min_shared_count: int = 2,
) -> list[dict]:
	"""
	Get frequent collaborators from user's following list.
	Returns pairs of followed people with their shared movie counts.
	Excludes documentaries and self-appearance movies.
	"""
	from itertools import combinations

	# Get all followed people for this user
	followed = PersonFollow.objects.filter(user_id=user_id).select_related("person")
	followed_people = [pf.person for pf in followed]

	if len(followed_people) < 2:
		return []

	# Build movie index for each person
	person_movie_index: dict[int, dict[int, dict]] = {}
	for person in followed_people:
		credits = person.tmdb_credits_raw or {}
		credit_items = []
		credit_items.extend(credits.get("cast", []) or [])
		credit_items.extend(credits.get("crew", []) or [])

		movie_index: dict[int, dict] = {}
		for item in credit_items:
			if item.get("media_type") not in (None, "movie"):
				continue
			mid = item.get("id")
			if not isinstance(mid, int):
				continue
			if _is_documentary(item):
				continue
			# Check if person has a self-appearance role
			if item.get("media_type") == "movie":
				character = str(item.get("character") or "").strip()
				job = str(item.get("job") or "").strip()
				if _is_self_character(character) or _is_self_character(job):
					continue
			movie_index.setdefault(mid, item)

		person_movie_index[person.tmdb_id] = movie_index

	# Find pairs with shared movies, deduplicating people and pairs
	pair_counts: list[tuple[tuple[int, int], int, object, object]] = []
	seen_pairs: set[tuple[int, int]] = set()
	
	for p1, p2 in combinations(followed_people, 2):
		# Skip if same person
		if p1.tmdb_id == p2.tmdb_id:
			continue
		
		# Avoid duplicate pairs (A,B) and (B,A)
		pair_key = tuple(sorted([p1.tmdb_id, p2.tmdb_id]))
		if pair_key in seen_pairs:
			continue
		seen_pairs.add(pair_key)
		
		movies1 = set(person_movie_index.get(p1.tmdb_id, {}).keys())
		movies2 = set(person_movie_index.get(p2.tmdb_id, {}).keys())
		shared_count = len(movies1 & movies2)

		if shared_count >= min_shared_count:
			pair_counts.append(((p1.tmdb_id, p2.tmdb_id), shared_count, p1, p2))

	if sort_mode == "most":
		pair_counts.sort(key=lambda item: (-item[1], item[2].name.lower(), item[3].name.lower()))
	else:
		# Randomize the eligible pairs so the list does not always appear in the same order.
		random.shuffle(pair_counts)

	if limit is not None and limit > 0:
		pair_counts = pair_counts[:limit]

	# Build result
	result = []
	for (pid1, pid2), count, p1, p2 in pair_counts[:limit]:
		result.append(
			{
				"pair_ids": [pid1, pid2],
				"pair_names": [p1.name, p2.name],
				"pair_profiles": [p1.profile_path or "", p2.profile_path or ""],
				"pair_departments": [
					(p1.tmdb_raw or {}).get("known_for_department") or "",
					(p2.tmdb_raw or {}).get("known_for_department") or "",
				],
				"shared_count": count,
			}
		)

	return result


def _roles_for_movie(*, credits_raw: dict, movie_id: int) -> tuple[list[str], bool]:
	roles: list[str] = []
	seen: set[str] = set()
	has_self_role = False

	def _add_role(text: str) -> None:
		role_text = (text or "").strip()
		if not role_text:
			return
		key = role_text.lower()
		if key in seen:
			return
		seen.add(key)
		roles.append(role_text)

	crew_entries = [c for c in (credits_raw.get("crew", []) or []) if c.get("id") == movie_id and c.get("media_type") in (None, "movie")]
	cast_entries = [c for c in (credits_raw.get("cast", []) or []) if c.get("id") == movie_id and c.get("media_type") in (None, "movie")]

	for item in crew_entries:
		job = str(item.get("job") or "").strip()
		department = str(item.get("department") or "").strip()
		label = job or department or "Crew"
		if _is_self_character(label):
			has_self_role = True
			continue
		_add_role(label)

	actor_labels: list[str] = []
	for item in cast_entries:
		character = str(item.get("character") or "").strip()
		if _is_self_character(character):
			has_self_role = True
			continue
		actor_labels.append(character or "Actor")

	for label in actor_labels:
		_add_role(label)

	roles.sort(key=_role_priority)
	return roles, has_self_role


def _build_collaboration_results(
	user_id: int,
	selected_ids: list[int],
	*,
	client: TMDbClient | None = None,
) -> tuple[list[dict], list[dict], int]:
	seen: set[int] = set()
	selected_ids = [pid for pid in selected_ids if not (pid in seen or seen.add(pid))]
	selected_people: list[dict] = []
	results: list[dict] = []
	collaborators_count = 0

	if len(selected_ids) < 2:
		return selected_people, results, collaborators_count

	if client is None:
		client = TMDbClient.from_settings()

	persons = []
	movie_sets: list[set[int]] = []
	per_person_credit_index: dict[int, dict[int, dict]] = {}

	followed_ids = set(
		PersonFollow.objects.filter(user_id=user_id)
		.values_list("person__tmdb_id", flat=True)
		.distinct()
	)

	for pid in selected_ids:
		person_obj = None
		try:
			person_obj = Person.objects.get(tmdb_id=pid)
		except Person.DoesNotExist:
			pass

		if not person_obj:
			try:
				raw = client.get_person(pid)
				credits = client.get_person_credits(pid)
			except Exception:
				continue
			person_obj = SimpleNamespace(
				tmdb_id=pid,
				name=(raw.get("name") or str(pid)),
				profile_path=(raw.get("profile_path") or ""),
				tmdb_raw=raw,
				tmdb_credits_raw=credits,
				tmdb_last_sync_at=None,
			)

		persons.append(person_obj)
		selected_people.append(
			{
				"tmdb_id": person_obj.tmdb_id,
				"name": person_obj.name,
				"profile_path": getattr(person_obj, "profile_path", "") or "",
				"known_for_department": str((getattr(person_obj, "tmdb_raw", {}) or {}).get("known_for_department") or "").strip(),
				"followed": pid in followed_ids,
			}
		)

		credits = person_obj.tmdb_credits_raw or {}
		credit_items = []
		credit_items.extend(credits.get("cast", []) or [])
		credit_items.extend(credits.get("crew", []) or [])

		movie_index: dict[int, dict] = {}
		movie_ids: set[int] = set()
		for item in credit_items:
			if item.get("media_type") not in (None, "movie"):
				continue
			mid = item.get("id")
			if not isinstance(mid, int):
				continue
			movie_ids.add(mid)
			movie_index.setdefault(mid, item)

		movie_sets.append(movie_ids)
		per_person_credit_index[person_obj.tmdb_id] = movie_index

	if len(persons) < 2:
		return selected_people, results, collaborators_count

	collaborators_count = len(persons)
	shared_movie_ids: set[int] = set.intersection(*movie_sets) if movie_sets else set()

	for mid in shared_movie_ids:
		any_item = None
		for p in persons:
			any_item = per_person_credit_index.get(p.tmdb_id, {}).get(mid)
			if any_item:
				break

		title = (any_item or {}).get("title") or (any_item or {}).get("name") or str(mid)
		release_date_str = (any_item or {}).get("release_date") or (any_item or {}).get("first_air_date")
		release_dt = _parse_iso_date(release_date_str)
		poster_path = (any_item or {}).get("poster_path") or ""

		roles = []
		has_self_role = False
		for p in persons:
			role_texts, is_self_role = _roles_for_movie(
				credits_raw=p.tmdb_credits_raw or {},
				movie_id=mid,
			)
			if is_self_role:
				has_self_role = True
			roles.append(
				{
					"person": p,
					"role": ", ".join(role_texts),
				}
			)

		if has_self_role:
			continue

		results.append(
			{
				"tmdb_id": mid,
				"title": title,
				"release_date": release_date_str or "",
				"release_dt": release_dt,
				"poster_path": poster_path,
				"roles": roles,
			}
		)

	results.sort(key=lambda r: (r["release_dt"] is None, r["release_dt"] or date.min), reverse=True)
	return selected_people, results, collaborators_count


@login_required
def collaboration_finder(request: HttpRequest) -> HttpResponse:
	query = (request.GET.get("q") or "").strip()
	frequent_sort = (request.GET.get("collab_sort") or request.POST.get("collab_sort") or "random").strip().lower()
	if frequent_sort not in {"random", "most"}:
		frequent_sort = "random"
	selected_ids: list[int] = []
	selected_people: list[dict] = []
	results: list[dict] = []
	collaborators_count = 0
	
	# Always calculate frequent collaborators so they persist across searches
	frequent_collaborators = _get_frequent_collaborators(
		request.user.id,
		sort_mode=frequent_sort,
		min_shared_count=2,
	)

	if request.method == "GET":
		try:
			selected_ids = [int(x) for x in request.GET.getlist("person_ids")]
		except ValueError:
			selected_ids = []

		if len(selected_ids) >= 2:
			selected_people, results, collaborators_count = _build_collaboration_results(
				request.user.id,
				selected_ids,
			)

	if request.method == "POST":
		try:
			selected_ids = [int(x) for x in request.POST.getlist("person_ids")]
		except ValueError:
			selected_ids = []

		if len(selected_ids) < 2:
			messages.error(request, "Select at least two people.")
		else:
			selected_people, results, collaborators_count = _build_collaboration_results(
				request.user.id,
				selected_ids,
			)
			if len(selected_people) < 2:
				messages.error(request, "Select at least two valid people.")

	return render(
		request,
		"catalog/collaboration.html",
		{
			"q": query,
			"frequent_sort": frequent_sort,
			"selected_ids": selected_ids,
			"selected_people": selected_people,
			"results": results,
			"results_count": len(results),
			"collaborators_count": collaborators_count,
			"has_results_request": len(selected_people) >= 2,
			"frequent_collaborators": frequent_collaborators,
		},
	)


@login_required
def collaboration_suggest(request: HttpRequest) -> HttpResponse:
	query = (request.GET.get("q") or "").strip()
	if len(query) < 2:
		return JsonResponse({"results": []})

	client = TMDbClient.from_settings()
	try:
		results = (client.search_people(query).get("results") or [])[:10]
	except Exception as exc:  # noqa: BLE001
		return JsonResponse({"results": [], "error": str(exc)}, status=200)

	payload = []
	for p in results:
		payload.append(
			{
				"id": p.get("id"),
				"name": p.get("name") or "",
				"profile_path": p.get("profile_path") or "",
				"known_for_department": p.get("known_for_department") or "",
			}
		)
	return JsonResponse({"results": payload})
