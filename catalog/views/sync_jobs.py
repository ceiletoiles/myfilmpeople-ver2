from __future__ import annotations

import threading
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import CompanyFollow, PersonFollow
from ..new_movie_helpers import (
	build_person_comeback_event_meta,
	extract_movie_ids_from_credits,
	extract_movie_ids_from_credits_for_role,
	extract_movie_ids_from_filmography,
	extract_movie_release_dates_from_credits,
	extract_movie_release_dates_from_credits_for_role,
	extract_movie_release_dates_from_filmography,
	record_new_movie_arrivals,
)
from ..services import (
	get_or_sync_company,
	get_or_sync_person,
	prefetch_company_filmography,
	prefetch_company_movies,
)

SYNC_JOB_TTL_SECONDS = 60 * 60

SYNC_SCOPE_ALL = "all"
SYNC_SCOPE_PEOPLE = "people"
SYNC_SCOPE_STUDIOS = "studios"
SYNC_SCOPE_VALUES = {SYNC_SCOPE_ALL, SYNC_SCOPE_PEOPLE, SYNC_SCOPE_STUDIOS}


def _sync_job_key(job_id: UUID) -> str:
	return f"syncjob:v1:{str(job_id)}"


def _sync_job_active_key(user_id: int) -> str:
	return f"syncjob:v1:active:{int(user_id)}"


def _sync_job_active_key_scoped(user_id: int, scope: str) -> str:
	# scope examples: all, person:525, company:999
	return f"syncjob:v1:active:{int(user_id)}:{scope}"


def _sync_job_get(job_id: UUID) -> dict | None:
	value = cache.get(_sync_job_key(job_id))
	return value if isinstance(value, dict) else None


def _sync_job_set(job_id: UUID, data: dict) -> None:
	cache.set(_sync_job_key(job_id), data, timeout=SYNC_JOB_TTL_SECONDS)


def _sync_job_patch(job_id: UUID, **updates) -> dict | None:
	data = _sync_job_get(job_id)
	if data is None:
		return None
	data = {**data, **updates}
	_sync_job_set(job_id, data)
	return data



def _sync_job_cancel_url(job_id: UUID) -> str:
	return reverse("sync_job_cancel", kwargs={"job_id": str(job_id)})


def _sync_job_is_cancel_requested(job_id: UUID) -> bool:
	data = _sync_job_get(job_id)
	return bool(data and data.get("cancel_requested"))


def _sync_job_request_cancel(job_id: UUID) -> dict | None:
	return _sync_job_patch(
		job_id,
		status="cancel_requested",
		cancel_requested=True,
		current_label="Cancel requested…",
	)


def _sync_job_mark_canceled(job_id: UUID) -> dict | None:
	data = _sync_job_get(job_id) or {}
	return _sync_job_patch(
		job_id,
		status="canceled",
		finished_at=timezone.now().isoformat(),
		cancel_requested=True,
		current_label="Canceled",
		current_sub_done=int(data.get("current_sub_done") or 0),
		current_sub_total=int(data.get("current_sub_total") or 0),
	)


def _sync_job_abort_if_cancel_requested(job_id: UUID) -> bool:
	if not _sync_job_is_cancel_requested(job_id):
		return False
	_sync_job_mark_canceled(job_id)
	return True


def _sync_scope_title(sync_scope: str) -> str:
	return {
		SYNC_SCOPE_PEOPLE: "people",
		SYNC_SCOPE_STUDIOS: "studios",
	}.get(sync_scope, "all data")


def _run_sync_all_followed_job(
	*,
	job_id: UUID,
	user_id: int,
	person_ids: list[int],
	company_ids: list[int],
	max_company_pages: int | None,
	sync_scope: str,
) -> None:
	"""Background thread job that syncs all followed people/companies and updates cache progress."""
	close_old_connections()
	try:
		from django.contrib.auth import get_user_model

		User = get_user_model()
		user = User.objects.get(pk=user_id)

		total_people = len(person_ids)
		total_companies = len(company_ids)
		total_entities = total_people + total_companies

		synced_people = 0
		synced_companies = 0
		fail_people = 0
		fail_companies = 0
		notifications_created = 0
		scope_title = _sync_scope_title(sync_scope)
		person_name_by_id = dict(
			PersonFollow.objects.filter(user=user)
			.values_list("person__tmdb_id", "person__name")
			.distinct()
		)
		company_name_by_id = dict(
			CompanyFollow.objects.filter(user=user)
			.values_list("company__tmdb_id", "company__name")
			.distinct()
		)

		# Reset current sub-progress
		_sync_job_patch(
			job_id,
			status="running",
			started_at=timezone.now().isoformat(),
			total_people=total_people,
			total_companies=total_companies,
			total_entities=total_entities,
			synced_people=0,
			synced_companies=0,
			fail_people=0,
			fail_companies=0,
			notifications_created=0,
			current_label=f"Starting {scope_title} sync…",
			current_sub_done=0,
			current_sub_total=0,
		)

		for i, pid in enumerate(person_ids, start=1):
			if _sync_job_abort_if_cancel_requested(job_id):
				return
			person_label = person_name_by_id.get(pid) or f"Person {pid}"
			_sync_job_patch(
				job_id,
				current_label=f"Loading person {i}/{total_people}: {person_label}…",
				current_sub_done=0,
				current_sub_total=0,
			)
			try:
				person = get_or_sync_person(pid, force=False)
				if _sync_job_abort_if_cancel_requested(job_id):
					return
				try:
					person.refresh_from_db(fields=["tmdb_credits_raw", "tmdb_last_sync_at"])
				except Exception:
					pass
				old_credits = person.tmdb_credits_raw or {}
				old_movie_ids = extract_movie_ids_from_credits(old_credits)
				old_release_dates = extract_movie_release_dates_from_credits(old_credits)
				old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(
					old_credits.get("crew"), list
				)

				person = get_or_sync_person(pid, force=True)
				person_label = person.name or person_label
				_sync_job_patch(job_id, current_label=f"Syncing person {person_label}…")
				if _sync_job_abort_if_cancel_requested(job_id):
					return
				PersonFollow.objects.filter(user=user, person__tmdb_id=pid).update(name=person.name)

				new_credits = person.tmdb_credits_raw or {}
				new_movie_ids = extract_movie_ids_from_credits(new_credits)
				new_release_dates = extract_movie_release_dates_from_credits(new_credits)

				if old_baseline_present:
					follows = PersonFollow.objects.filter(user=user, person__tmdb_id=pid)
					for follow in follows:
						old_role_movie_ids = extract_movie_ids_from_credits_for_role(old_credits, follow.role or "")
						new_role_movie_ids = extract_movie_ids_from_credits_for_role(new_credits, follow.role or "")
						if not old_role_movie_ids:
							continue
						old_role_release_dates = extract_movie_release_dates_from_credits_for_role(old_credits, follow.role or "")
						new_role_release_dates = extract_movie_release_dates_from_credits_for_role(new_credits, follow.role or "")
						new_event_meta_by_movie = build_person_comeback_event_meta(
							old_release_dates=old_role_release_dates,
							new_release_dates=new_role_release_dates,
							new_movie_ids=new_role_movie_ids,
						)
						# Augment event meta with character names from the new credits (when available).
						person_movie_display_by_movie: dict[int, dict] = {}
						if isinstance(new_credits, dict):
							for credit in (new_credits.get("cast") or []):
								if not isinstance(credit, dict):
									continue
								mid = credit.get("id")
								char = credit.get("character") or ""
								if isinstance(mid, int) and isinstance(char, str) and char.strip():
									meta = (new_event_meta_by_movie.setdefault(mid, {}) if isinstance(new_event_meta_by_movie, dict) else {})
									if isinstance(meta, dict) and "character" not in meta:
										meta["character"] = char.strip()
									# For cast entries, set the exact credit job to 'Actor' (preserve case/title).
									if isinstance(meta, dict) and "credit_job" not in meta:
										meta["credit_job"] = "Actor"
									display = person_movie_display_by_movie.setdefault(mid, {})
									title = str(credit.get("title") or credit.get("name") or "").strip()
									if title and "title" not in display:
										display["title"] = title
									poster_path = str(credit.get("poster_path") or "").strip()
									if poster_path and "poster_path" not in display:
										display["poster_path"] = poster_path
									display = person_movie_display_by_movie.setdefault(mid, {})
									title = str(credit.get("title") or credit.get("name") or "").strip()
									if title and "title" not in display:
										display["title"] = title
									poster_path = str(credit.get("poster_path") or "").strip()
									if poster_path and "poster_path" not in display:
										display["poster_path"] = poster_path

							for credit in (new_credits.get("crew") or []):
								if not isinstance(credit, dict):
									continue
								mid = credit.get("id")
								job = credit.get("job") or ""
								if isinstance(mid, int) and isinstance(job, str) and job.strip():
									meta = (new_event_meta_by_movie.setdefault(mid, {}) if isinstance(new_event_meta_by_movie, dict) else {})
									if isinstance(meta, dict) and "credit_job" not in meta:
										meta["credit_job"] = job.strip()
									display = person_movie_display_by_movie.setdefault(mid, {})
									title = str(credit.get("title") or credit.get("name") or "").strip()
									if title and "title" not in display:
										display["title"] = title
									poster_path = str(credit.get("poster_path") or "").strip()
									if poster_path and "poster_path" not in display:
										display["poster_path"] = poster_path
									display = person_movie_display_by_movie.setdefault(mid, {})
									title = str(credit.get("title") or credit.get("name") or "").strip()
									if title and "title" not in display:
										display["title"] = title
									poster_path = str(credit.get("poster_path") or "").strip()
									if poster_path and "poster_path" not in display:
										display["poster_path"] = poster_path
						notifications_created += record_new_movie_arrivals(
							user=user,
							source_type="person",
							source_id=pid,
							source_name=person_label,
							old_movie_ids=old_role_movie_ids,
							new_movie_ids=new_role_movie_ids,
							role=follow.role or "",
							old_release_dates=old_role_release_dates,
							new_release_dates=new_role_release_dates,
							new_movie_display_by_movie=person_movie_display_by_movie,
							new_event_meta_by_movie=new_event_meta_by_movie,
							source_last_sync_at=getattr(person, "tmdb_last_sync_at", None),
							should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
						)

				synced_people += 1
				if _sync_job_abort_if_cancel_requested(job_id):
					return
			except Exception:
				_sync_job_patch(
					job_id,
					current_label=f"Failed person {person_label}…",
					current_sub_done=0,
					current_sub_total=0,
				)
				fail_people += 1
			finally:
				_sync_job_patch(
					job_id,
					synced_people=synced_people,
					fail_people=fail_people,
					notifications_created=notifications_created,
				)

		for i, cid in enumerate(company_ids, start=1):
			if _sync_job_abort_if_cancel_requested(job_id):
				return
			company_label = company_name_by_id.get(cid) or f"Studio {cid}"
			_sync_job_patch(
				job_id,
				current_label=f"Loading studio {i}/{total_companies}: {company_label}…",
				current_sub_done=0,
				current_sub_total=0,
			)
			try:
				company = get_or_sync_company(cid, force=False)
				if _sync_job_abort_if_cancel_requested(job_id):
					return
				try:
					company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
				except Exception:
					pass
				old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
				old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw, pages_key="company_movies_pages")
				old_pages = old_tmdb_raw.get("company_movies_pages")
				old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
				old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw, pages_key="company_movies_pages")

				company = get_or_sync_company(cid, force=True)
				company_label = company.name or company_label
				_sync_job_patch(job_id, current_label=f"Syncing studio {company_label}…")
				if _sync_job_abort_if_cancel_requested(job_id):
					return

				try:
					prefetch_company_filmography(company, force=True, max_pages=1)
				except Exception:
					pass

				def _on_pages_progress(done: int, total: int) -> None:
					if _sync_job_is_cancel_requested(job_id):
						return
					_sync_job_patch(
						job_id,
						current_label=f"{company_label}: page {done}/{total}",
						current_sub_done=int(done or 0),
						current_sub_total=int(total or 0),
					)

				try:
					prefetch_company_movies(
						company,
						force=True,
						max_pages=max_company_pages,
						progress_cb=_on_pages_progress,
						should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
					)
				except Exception:
					pass

				if _sync_job_abort_if_cancel_requested(job_id):
					return

				new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
				new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw, pages_key="company_movies_pages")
				new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw, pages_key="company_movies_pages")
				company_movie_display_by_movie: dict[int, dict] = {}
				if isinstance(new_tmdb_raw, dict):
					pages = new_tmdb_raw.get("company_movies_pages") or {}
					for payload in pages.values():
						if not isinstance(payload, dict):
							continue
						for movie in payload.get("results", []) or []:
							if not isinstance(movie, dict):
								continue
							mid = movie.get("id")
							if not isinstance(mid, int):
								continue
							display = company_movie_display_by_movie.setdefault(mid, {})
							title = str(movie.get("title") or movie.get("name") or "").strip()
							if title and "title" not in display:
								display["title"] = title
							poster_path = str(movie.get("poster_path") or "").strip()
							if poster_path and "poster_path" not in display:
								display["poster_path"] = poster_path

				if old_baseline_present:
					notifications_created += record_new_movie_arrivals(
						user=user,
						source_type="company",
						source_id=cid,
						source_name=company_label,
						old_movie_ids=old_movie_ids,
						new_movie_ids=new_movie_ids,
						role="studio",
						old_release_dates=old_release_dates,
						new_release_dates=new_release_dates,
						new_movie_display_by_movie=company_movie_display_by_movie,
						# Use the current company sync timestamp so older TMDb edits
						# do not surface as fresh arrivals.
						source_last_sync_at=getattr(company, "tmdb_last_sync_at", None),
						should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
					)

				CompanyFollow.objects.filter(user=user, company__tmdb_id=cid).update(name=company.name)
				synced_companies += 1
				if _sync_job_abort_if_cancel_requested(job_id):
					return
			except Exception:
				_sync_job_patch(
					job_id,
					current_label=f"Failed studio {company_label}…",
					current_sub_done=0,
					current_sub_total=0,
				)
				fail_companies += 1
			finally:
				_sync_job_patch(
					job_id,
					synced_companies=synced_companies,
					fail_companies=fail_companies,
					notifications_created=notifications_created,
					current_sub_done=0,
					current_sub_total=0,
				)

		finished_at = timezone.now().isoformat()
		fail_total = fail_people + fail_companies
		if fail_total == 0:
			completion_message = f"Sync complete. Notifications: {notifications_created}."
		else:
			completion_message = f"Sync completed with errors. Notifications: {notifications_created}. Failed: {fail_total}."
		_sync_job_patch(
			job_id,
			status="done" if fail_total == 0 else "done_with_errors",
			finished_at=finished_at,
			current_label="Complete",
			message=completion_message,
		)
	finally:
		# Clear active job pointer.
		try:
			cache.delete(_sync_job_active_key(user_id))
		except Exception:
			pass
		close_old_connections()


def _run_person_sync_job(*, job_id: UUID, user_id: int, tmdb_id: int) -> None:
	close_old_connections()
	try:
		from django.contrib.auth import get_user_model

		User = get_user_model()
		user = User.objects.get(pk=user_id)

		_sync_job_patch(
			job_id,
			status="running",
			started_at=timezone.now().isoformat(),
			total_people=1,
			total_companies=0,
			total_entities=1,
			synced_people=0,
			synced_companies=0,
			fail_people=0,
			fail_companies=0,
			notifications_created=0,
			current_label="Starting person sync…",
			current_sub_done=5,
			current_sub_total=100,
		)

		if _sync_job_abort_if_cancel_requested(job_id):
			return

		person_label = f"Person {tmdb_id}"

		if not PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id).exists():
			_sync_job_patch(
				job_id,
				status="done_with_errors",
				finished_at=timezone.now().isoformat(),
				current_label="Not followed",
				fail_people=1,
				current_sub_done=100,
			)
			return

		person = get_or_sync_person(tmdb_id, force=False)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		try:
			person.refresh_from_db(fields=["tmdb_credits_raw", "tmdb_last_sync_at"])
		except Exception:
			pass
		old_credits = person.tmdb_credits_raw or {}
		old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(old_credits.get("crew"), list)

		person_label = person.name or person_label
		_sync_job_patch(job_id, current_label=f"Syncing person {person_label}…", current_sub_done=35)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		person = get_or_sync_person(tmdb_id, force=True)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id).update(name=person.name)
		new_credits = person.tmdb_credits_raw or {}

		notifications_created = 0
		_sync_job_patch(job_id, current_label="Recording updates…", current_sub_done=75)
		if old_baseline_present:
			follows = PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id)
			person_movie_display_by_movie: dict[int, dict] = {}
			if isinstance(new_credits, dict):
				for credit in (new_credits.get("cast") or []):
					if not isinstance(credit, dict):
						continue
					mid = credit.get("id")
					if not isinstance(mid, int):
						continue
					display = person_movie_display_by_movie.setdefault(mid, {})
					title = str(credit.get("title") or credit.get("name") or "").strip()
					if title and "title" not in display:
						display["title"] = title
					poster_path = str(credit.get("poster_path") or "").strip()
					if poster_path and "poster_path" not in display:
						display["poster_path"] = poster_path
				for credit in (new_credits.get("crew") or []):
					if not isinstance(credit, dict):
						continue
					mid = credit.get("id")
					if not isinstance(mid, int):
						continue
					display = person_movie_display_by_movie.setdefault(mid, {})
					title = str(credit.get("title") or credit.get("name") or "").strip()
					if title and "title" not in display:
						display["title"] = title
					poster_path = str(credit.get("poster_path") or "").strip()
					if poster_path and "poster_path" not in display:
						display["poster_path"] = poster_path
			for follow in follows:
				old_role_movie_ids = extract_movie_ids_from_credits_for_role(old_credits, follow.role or "")
				new_role_movie_ids = extract_movie_ids_from_credits_for_role(new_credits, follow.role or "")
				if not old_role_movie_ids:
					continue
				old_role_release_dates = extract_movie_release_dates_from_credits_for_role(old_credits, follow.role or "")
				new_role_release_dates = extract_movie_release_dates_from_credits_for_role(new_credits, follow.role or "")
				new_event_meta_by_movie = build_person_comeback_event_meta(
					old_release_dates=old_role_release_dates,
					new_release_dates=new_role_release_dates,
					new_movie_ids=new_role_movie_ids,
				)
				notifications_created += record_new_movie_arrivals(
					user=user,
					source_type="person",
					source_id=tmdb_id,
					source_name=person_label,
					old_movie_ids=old_role_movie_ids,
					new_movie_ids=new_role_movie_ids,
					role=follow.role or "",
					old_release_dates=old_role_release_dates,
					new_release_dates=new_role_release_dates,
					new_movie_display_by_movie=person_movie_display_by_movie,
					new_event_meta_by_movie=new_event_meta_by_movie,
					should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
				)

		if _sync_job_abort_if_cancel_requested(job_id):
			return

		_sync_job_patch(
			job_id,
			status="done",
			finished_at=timezone.now().isoformat(),
			synced_people=1,
			notifications_created=notifications_created,
			current_label="Complete",
			current_sub_done=100,
			message=f"Sync complete. Notifications: {notifications_created}.",
		)
	except Exception:
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		_sync_job_patch(
			job_id,
			status="done_with_errors",
			finished_at=timezone.now().isoformat(),
			fail_people=1,
			current_label=f"Failed person {person_label}…",
			current_sub_done=100,
			message="Sync completed with errors.",
		)
	finally:
		try:
			cache.delete(_sync_job_active_key_scoped(user_id, f"person:{int(tmdb_id)}"))
		except Exception:
			pass
		close_old_connections()


def _run_company_sync_job(*, job_id: UUID, user_id: int, tmdb_id: int, max_company_pages: int | None) -> None:
	close_old_connections()
	try:
		from django.contrib.auth import get_user_model

		User = get_user_model()
		user = User.objects.get(pk=user_id)

		_sync_job_patch(
			job_id,
			status="running",
			started_at=timezone.now().isoformat(),
			total_people=0,
			total_companies=1,
			total_entities=1,
			synced_people=0,
			synced_companies=0,
			fail_people=0,
			fail_companies=0,
			notifications_created=0,
			current_label="Starting studio sync…",
			current_sub_done=0,
			current_sub_total=0,
		)

		if _sync_job_abort_if_cancel_requested(job_id):
			return

		company_label = f"Studio {tmdb_id}"

		if not CompanyFollow.objects.filter(user=user, company__tmdb_id=tmdb_id).exists():
			_sync_job_patch(
				job_id,
				status="done_with_errors",
				finished_at=timezone.now().isoformat(),
				current_label="Not followed",
				fail_companies=1,
			)
			return

		company = get_or_sync_company(tmdb_id, force=False)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		try:
			company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
		except Exception:
			pass
		old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw, pages_key="company_movies_pages")
		old_pages = old_tmdb_raw.get("company_movies_pages")
		old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
		old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw, pages_key="company_movies_pages")

		company = get_or_sync_company(tmdb_id, force=True)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		company_label = company.name or company_label
		_sync_job_patch(job_id, current_label=f"Syncing studio {company_label}…")

		try:
			prefetch_company_filmography(company, force=True, max_pages=1)
		except Exception:
			pass

		def _on_pages_progress(done: int, total: int) -> None:
			if _sync_job_is_cancel_requested(job_id):
				return
			_sync_job_patch(
				job_id,
				current_label=f"{company_label}: page {done}/{total}",
				current_sub_done=int(done or 0),
				current_sub_total=int(total or 0),
			)

		try:
			prefetch_company_movies(
				company,
				force=True,
				max_pages=max_company_pages,
				progress_cb=_on_pages_progress,
				should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
			)
		except Exception:
			pass

		if _sync_job_abort_if_cancel_requested(job_id):
			return

		new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw, pages_key="company_movies_pages")
		new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw, pages_key="company_movies_pages")
		company_movie_display_by_movie: dict[int, dict] = {}
		if isinstance(new_tmdb_raw, dict):
			pages = new_tmdb_raw.get("company_movies_pages") or {}
			for payload in pages.values():
				if not isinstance(payload, dict):
					continue
				for movie in payload.get("results", []) or []:
					if not isinstance(movie, dict):
						continue
					mid = movie.get("id")
					if not isinstance(mid, int):
						continue
					display = company_movie_display_by_movie.setdefault(mid, {})
					title = str(movie.get("title") or movie.get("name") or "").strip()
					if title and "title" not in display:
						display["title"] = title
					poster_path = str(movie.get("poster_path") or "").strip()
					if poster_path and "poster_path" not in display:
						display["poster_path"] = poster_path

		notifications_created = 0
		if old_baseline_present:
			notifications_created = record_new_movie_arrivals(
				user=user,
				source_type="company",
				source_id=tmdb_id,
				source_name=company_label,
				old_movie_ids=old_movie_ids,
				new_movie_ids=new_movie_ids,
				role="studio",
				old_release_dates=old_release_dates,
				new_release_dates=new_release_dates,
				new_movie_display_by_movie=company_movie_display_by_movie,
				should_stop_cb=lambda: _sync_job_is_cancel_requested(job_id),
			)

		CompanyFollow.objects.filter(user=user, company__tmdb_id=tmdb_id).update(name=company.name)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
		_sync_job_patch(
			job_id,
			status="done",
			finished_at=timezone.now().isoformat(),
			synced_companies=1,
			notifications_created=notifications_created,
			current_label="Complete",
			current_sub_done=0,
			current_sub_total=0,
			message=f"Sync complete. Notifications: {notifications_created}.",
		)
	except Exception:
		_sync_job_patch(
			job_id,
			status="done_with_errors",
			finished_at=timezone.now().isoformat(),
			fail_companies=1,
			current_label=f"Failed studio {company_label}…",
			message="Sync completed with errors.",
		)
		if _sync_job_abort_if_cancel_requested(job_id):
			return
	finally:
		try:
			cache.delete(_sync_job_active_key_scoped(user_id, f"company:{int(tmdb_id)}"))
		except Exception:
			pass
		close_old_connections()


@login_required
def person_sync_start(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return JsonResponse({"ok": False, "error": "POST required."}, status=405)

	scope = f"person:{int(tmdb_id)}"
	active = cache.get(_sync_job_active_key_scoped(request.user.id, scope))
	try:
		active_uuid = UUID(str(active)) if active else None
	except Exception:
		active_uuid = None
	if active_uuid is not None:
		existing = _sync_job_get(active_uuid)
		if existing and existing.get("user_id") == request.user.id and existing.get("status") == "running":
			return JsonResponse(
				{
					"ok": True,
					"status": "running",
					"job_id": str(active_uuid),
					"progress_url": reverse("person_sync_progress", kwargs={"job_id": str(active_uuid)}),
					"cancel_url": _sync_job_cancel_url(active_uuid),
				}
			)

	job_id = uuid4()
	cache.set(_sync_job_active_key_scoped(request.user.id, scope), str(job_id), timeout=SYNC_JOB_TTL_SECONDS)
	_sync_job_set(
		job_id,
		{
			"job_id": str(job_id),
			"user_id": request.user.id,
			"status": "running",
			"started_at": timezone.now().isoformat(),
			"finished_at": None,
			"total_people": 1,
			"total_companies": 0,
			"total_entities": 1,
			"synced_people": 0,
			"synced_companies": 0,
			"fail_people": 0,
			"fail_companies": 0,
			"notifications_created": 0,
			"current_label": "Queued…",
			"current_sub_done": 0,
			"current_sub_total": 100,
			"cancel_url": _sync_job_cancel_url(job_id),
		},
	)

	thread = threading.Thread(
		target=_run_person_sync_job,
		kwargs={"job_id": job_id, "user_id": request.user.id, "tmdb_id": int(tmdb_id)},
		daemon=True,
	)
	thread.start()

	return JsonResponse(
		{
			"ok": True,
			"status": "running",
			"job_id": str(job_id),
			"progress_url": reverse("person_sync_progress", kwargs={"job_id": str(job_id)}),
			"cancel_url": _sync_job_cancel_url(job_id),
		}
	)


@login_required
def company_sync_start(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return JsonResponse({"ok": False, "error": "POST required."}, status=405)

	scope = f"company:{int(tmdb_id)}"
	active = cache.get(_sync_job_active_key_scoped(request.user.id, scope))
	try:
		active_uuid = UUID(str(active)) if active else None
	except Exception:
		active_uuid = None
	if active_uuid is not None:
		existing = _sync_job_get(active_uuid)
		if existing and existing.get("user_id") == request.user.id and existing.get("status") == "running":
			return JsonResponse(
				{
					"ok": True,
					"status": "running",
					"job_id": str(active_uuid),
					"progress_url": reverse("company_sync_progress", kwargs={"job_id": str(active_uuid)}),
					"cancel_url": _sync_job_cancel_url(active_uuid),
				}
			)

	job_id = uuid4()
	cache.set(_sync_job_active_key_scoped(request.user.id, scope), str(job_id), timeout=SYNC_JOB_TTL_SECONDS)
	_sync_job_set(
		job_id,
		{
			"job_id": str(job_id),
			"user_id": request.user.id,
			"status": "running",
			"started_at": timezone.now().isoformat(),
			"finished_at": None,
			"total_people": 0,
			"total_companies": 1,
			"total_entities": 1,
			"synced_people": 0,
			"synced_companies": 0,
			"fail_people": 0,
			"fail_companies": 0,
			"notifications_created": 0,
			"current_label": "Queued…",
			"current_sub_done": 0,
			"current_sub_total": 0,
			"cancel_url": _sync_job_cancel_url(job_id),
		},
	)

	max_pages = getattr(settings, "TMDB_COMPANY_FILMOGRAPHY_PREFETCH_MAX_PAGES", 0)
	try:
		max_pages_int = int(max_pages)
	except (TypeError, ValueError):
		max_pages_int = 0
	max_company_pages = None if max_pages_int <= 0 else max_pages_int

	thread = threading.Thread(
		target=_run_company_sync_job,
		kwargs={
			"job_id": job_id,
			"user_id": request.user.id,
			"tmdb_id": int(tmdb_id),
			"max_company_pages": max_company_pages,
		},
		daemon=True,
	)
	thread.start()

	return JsonResponse(
		{
			"ok": True,
			"status": "running",
			"job_id": str(job_id),
			"progress_url": reverse("company_sync_progress", kwargs={"job_id": str(job_id)}),
			"cancel_url": _sync_job_cancel_url(job_id),
		}
	)


@login_required
def person_sync_progress(request: HttpRequest, job_id: str) -> HttpResponse:
	try:
		jid = UUID(str(job_id))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid job id."}, status=400)
	data = _sync_job_get(jid)
	if not data:
		return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
	if int(data.get("user_id") or 0) != int(request.user.id):
		return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)
	return JsonResponse({"ok": True, **data})


@login_required
def company_sync_progress(request: HttpRequest, job_id: str) -> HttpResponse:
	try:
		jid = UUID(str(job_id))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid job id."}, status=400)
	data = _sync_job_get(jid)
	if not data:
		return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
	if int(data.get("user_id") or 0) != int(request.user.id):
		return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)
	return JsonResponse({"ok": True, **data})


@login_required
def sync_all_followed_start(request: HttpRequest) -> HttpResponse:
	"""Start a background sync job and return a job id for polling."""
	if request.method != "POST":
		return JsonResponse({"ok": False, "error": "POST required."}, status=405)

	sync_scope = (request.POST.get("sync_scope") or SYNC_SCOPE_ALL).strip().lower()
	if sync_scope not in SYNC_SCOPE_VALUES:
		sync_scope = SYNC_SCOPE_ALL

	next_url = (request.POST.get("next") or "").strip()
	if not next_url:
		next_url = (request.META.get("HTTP_REFERER") or "").strip()
	if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
		next_url = ""

	person_ids: list[int] = []
	company_ids: list[int] = []
	if sync_scope in {SYNC_SCOPE_ALL, SYNC_SCOPE_PEOPLE}:
		person_ids = list(
			PersonFollow.objects.filter(user=request.user)
			.values_list("person__tmdb_id", flat=True)
			.distinct()
		)
	if sync_scope in {SYNC_SCOPE_ALL, SYNC_SCOPE_STUDIOS}:
		company_ids = list(
			CompanyFollow.objects.filter(user=request.user)
			.values_list("company__tmdb_id", flat=True)
			.distinct()
		)

	if not person_ids and not company_ids:
		if sync_scope == SYNC_SCOPE_PEOPLE:
			message = "Nothing to sync yet."
			if not PersonFollow.objects.filter(user=request.user).exists():
				message = "You are not following any people yet."
		elif sync_scope == SYNC_SCOPE_STUDIOS:
			message = "You are not following any studios yet."
		else:
			message = "Nothing to sync yet."
		payload = {
			"ok": True,
			"status": "done",
			"job_id": None,
			"message": message,
			"total_people": 0,
			"total_companies": 0,
			"total_entities": 0,
		}
		return JsonResponse(payload)

	# Only one active job per user.
	active = cache.get(_sync_job_active_key(request.user.id))
	try:
		active_uuid = UUID(str(active)) if active else None
	except Exception:
		active_uuid = None
	if active_uuid is not None:
		existing = _sync_job_get(active_uuid)
		if existing and existing.get("user_id") == request.user.id and existing.get("status") == "running":
			return JsonResponse(
				{
					"ok": True,
					"status": "running",
					"job_id": str(active_uuid),
					"progress_url": reverse("sync_all_followed_progress", kwargs={"job_id": str(active_uuid)}),
					"cancel_url": _sync_job_cancel_url(active_uuid),
					"total_people": int(existing.get("total_people") or 0),
					"total_companies": int(existing.get("total_companies") or 0),
					"total_entities": int(existing.get("total_entities") or 0),
				}
			)

	job_id = uuid4()
	cache.set(_sync_job_active_key(request.user.id), str(job_id), timeout=SYNC_JOB_TTL_SECONDS)
	_sync_job_set(
		job_id,
		{
			"job_id": str(job_id),
			"user_id": request.user.id,
			"status": "running",
			"started_at": timezone.now().isoformat(),
			"finished_at": None,
			"total_people": len(person_ids),
			"total_companies": len(company_ids),
			"total_entities": len(person_ids) + len(company_ids),
			"synced_people": 0,
			"synced_companies": 0,
			"fail_people": 0,
			"fail_companies": 0,
			"notifications_created": 0,
			"current_label": f"Queued {_sync_scope_title(sync_scope)} sync…",
			"current_sub_done": 0,
			"current_sub_total": 0,
			"next_url": next_url,
			"sync_scope": sync_scope,
			"cancel_url": _sync_job_cancel_url(job_id),
		},
	)

	max_pages = getattr(settings, "TMDB_COMPANY_FILMOGRAPHY_PREFETCH_MAX_PAGES", 0)
	try:
		max_pages_int = int(max_pages)
	except (TypeError, ValueError):
		max_pages_int = 0
	max_company_pages = None if max_pages_int <= 0 else max_pages_int

	thread = threading.Thread(
		target=_run_sync_all_followed_job,
		kwargs={
			"job_id": job_id,
			"user_id": request.user.id,
			"person_ids": person_ids,
			"company_ids": company_ids,
			"max_company_pages": max_company_pages,
			"sync_scope": sync_scope,
		},
		daemon=True,
	)
	thread.start()

	return JsonResponse(
		{
			"ok": True,
			"status": "running",
			"job_id": str(job_id),
			"progress_url": reverse("sync_all_followed_progress", kwargs={"job_id": str(job_id)}),
			"cancel_url": _sync_job_cancel_url(job_id),
			"total_people": len(person_ids),
			"total_companies": len(company_ids),
			"total_entities": len(person_ids) + len(company_ids),
		}
	)


@login_required
def sync_job_cancel(request: HttpRequest, job_id: str) -> HttpResponse:
	if request.method != "POST":
		return JsonResponse({"ok": False, "error": "POST required."}, status=405)

	try:
		jid = UUID(str(job_id))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid job id."}, status=400)

	data = _sync_job_get(jid)
	if not data:
		return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
	if int(data.get("user_id") or 0) != int(request.user.id):
		return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

	status = str(data.get("status") or "")
	if status in {"done", "done_with_errors", "canceled"}:
		return JsonResponse({"ok": True, "status": status, "job_id": str(jid), "message": "Job already finished."})

	_sync_job_request_cancel(jid)
	return JsonResponse(
		{
			"ok": True,
			"status": "cancel_requested",
			"job_id": str(jid),
			"message": "Cancel requested.",
			"progress_url": data.get("progress_url"),
		}
	)


@login_required
def sync_all_followed_progress(request: HttpRequest, job_id: str) -> HttpResponse:
	"""Poll sync progress for a given job."""
	try:
		jid = UUID(str(job_id))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid job id."}, status=400)

	data = _sync_job_get(jid)
	if not data:
		return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
	if int(data.get("user_id") or 0) != int(request.user.id):
		return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

	return JsonResponse({"ok": True, **data})
