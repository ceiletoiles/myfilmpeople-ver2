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
)

SYNC_JOB_TTL_SECONDS = 60 * 60


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


def _run_sync_all_followed_job(
	*,
	job_id: UUID,
	user_id: int,
	person_ids: list[int],
	company_ids: list[int],
	max_company_pages: int | None,
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
			current_label="Starting…",
			current_sub_done=0,
			current_sub_total=0,
		)

		for i, pid in enumerate(person_ids, start=1):
			_sync_job_patch(
				job_id,
				current_label=f"People {i}/{total_people}",
				current_sub_done=0,
				current_sub_total=0,
			)
			try:
				person = get_or_sync_person(pid, force=False)
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
						notifications_created += record_new_movie_arrivals(
							user=user,
							source_type="person",
							source_id=pid,
							source_name=person.name,
							old_movie_ids=old_role_movie_ids,
							new_movie_ids=new_role_movie_ids,
							role=follow.role or "",
							old_release_dates=old_role_release_dates,
							new_release_dates=new_role_release_dates,
							new_event_meta_by_movie=new_event_meta_by_movie,
						)

				synced_people += 1
			except Exception:
				fail_people += 1
			finally:
				_sync_job_patch(
					job_id,
					synced_people=synced_people,
					fail_people=fail_people,
					notifications_created=notifications_created,
				)

		for i, cid in enumerate(company_ids, start=1):
			_sync_job_patch(
				job_id,
				current_label=f"Companies {i}/{total_companies}",
				current_sub_done=0,
				current_sub_total=0,
			)
			try:
				company = get_or_sync_company(cid, force=False)
				try:
					company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
				except Exception:
					pass
				old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
				old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
				old_pages = old_tmdb_raw.get("discover_movies_pages")
				old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
				old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)

				company = get_or_sync_company(cid, force=True)

				def _on_pages_progress(done: int, total: int) -> None:
					_sync_job_patch(
						job_id,
						current_label=f"{company.name or 'Company'}: pages {done}/{total}",
						current_sub_done=int(done or 0),
						current_sub_total=int(total or 0),
					)

				try:
					prefetch_company_filmography(
						company,
						force=True,
						max_pages=max_company_pages,
						progress_cb=_on_pages_progress,
					)
				except Exception:
					pass

				new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
				new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw)
				new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw)

				if old_baseline_present:
					notifications_created += record_new_movie_arrivals(
						user=user,
						source_type="company",
						source_id=cid,
						source_name=company.name,
						old_movie_ids=old_movie_ids,
						new_movie_ids=new_movie_ids,
						role="studio",
						old_release_dates=old_release_dates,
						new_release_dates=new_release_dates,
					)

				CompanyFollow.objects.filter(user=user, company__tmdb_id=cid).update(name=company.name)
				synced_companies += 1
			except Exception:
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
		_sync_job_patch(
			job_id,
			status="done" if fail_total == 0 else "done_with_errors",
			finished_at=finished_at,
			current_label="Complete",
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
			current_label="Person sync…",
			current_sub_done=5,
			current_sub_total=100,
		)

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
		try:
			person.refresh_from_db(fields=["tmdb_credits_raw", "tmdb_last_sync_at"])
		except Exception:
			pass
		old_credits = person.tmdb_credits_raw or {}
		old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(old_credits.get("crew"), list)

		_sync_job_patch(job_id, current_label=f"Syncing {person.name or 'person'}…", current_sub_done=35)
		person = get_or_sync_person(tmdb_id, force=True)
		PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id).update(name=person.name)
		new_credits = person.tmdb_credits_raw or {}

		notifications_created = 0
		_sync_job_patch(job_id, current_label="Recording updates…", current_sub_done=75)
		if old_baseline_present:
			follows = PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id)
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
					source_name=person.name,
					old_movie_ids=old_role_movie_ids,
					new_movie_ids=new_role_movie_ids,
					role=follow.role or "",
					old_release_dates=old_role_release_dates,
					new_release_dates=new_role_release_dates,
					new_event_meta_by_movie=new_event_meta_by_movie,
				)

		_sync_job_patch(
			job_id,
			status="done",
			finished_at=timezone.now().isoformat(),
			synced_people=1,
			notifications_created=notifications_created,
			current_label="Complete",
			current_sub_done=100,
		)
	except Exception:
		_sync_job_patch(
			job_id,
			status="done_with_errors",
			finished_at=timezone.now().isoformat(),
			fail_people=1,
			current_label="Failed",
			current_sub_done=100,
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
			current_label="Company sync…",
			current_sub_done=0,
			current_sub_total=0,
		)

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
		try:
			company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
		except Exception:
			pass
		old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
		old_pages = old_tmdb_raw.get("discover_movies_pages")
		old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
		old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)

		company = get_or_sync_company(tmdb_id, force=True)

		def _on_pages_progress(done: int, total: int) -> None:
			_sync_job_patch(
				job_id,
				current_label=f"{company.name or 'Company'}: pages {done}/{total}",
				current_sub_done=int(done or 0),
				current_sub_total=int(total or 0),
			)

		try:
			prefetch_company_filmography(
				company,
				force=True,
				max_pages=max_company_pages,
				progress_cb=_on_pages_progress,
			)
		except Exception:
			pass

		new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw)
		new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw)

		notifications_created = 0
		if old_baseline_present:
			notifications_created = record_new_movie_arrivals(
				user=user,
				source_type="company",
				source_id=tmdb_id,
				source_name=company.name,
				old_movie_ids=old_movie_ids,
				new_movie_ids=new_movie_ids,
				role="studio",
				old_release_dates=old_release_dates,
				new_release_dates=new_release_dates,
			)

		CompanyFollow.objects.filter(user=user, company__tmdb_id=tmdb_id).update(name=company.name)
		_sync_job_patch(
			job_id,
			status="done",
			finished_at=timezone.now().isoformat(),
			synced_companies=1,
			notifications_created=notifications_created,
			current_label="Complete",
			current_sub_done=0,
			current_sub_total=0,
		)
	except Exception:
		_sync_job_patch(
			job_id,
			status="done_with_errors",
			finished_at=timezone.now().isoformat(),
			fail_companies=1,
			current_label="Failed",
		)
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

	next_url = (request.POST.get("next") or "").strip()
	if not next_url:
		next_url = (request.META.get("HTTP_REFERER") or "").strip()
	if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
		next_url = ""

	person_ids = list(
		PersonFollow.objects.filter(user=request.user)
		.values_list("person__tmdb_id", flat=True)
		.distinct()
	)
	company_ids = list(
		CompanyFollow.objects.filter(user=request.user)
		.values_list("company__tmdb_id", flat=True)
		.distinct()
	)

	if not person_ids and not company_ids:
		payload = {
			"ok": True,
			"status": "done",
			"job_id": None,
			"message": "Nothing to sync yet.",
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
			"current_label": "Queued…",
			"current_sub_done": 0,
			"current_sub_total": 0,
			"next_url": next_url,
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
			"total_people": len(person_ids),
			"total_companies": len(company_ids),
			"total_entities": len(person_ids) + len(company_ids),
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
