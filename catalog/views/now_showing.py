from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Max
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.urls import reverse
from uuid import UUID, uuid4

from ..models import NowShowingMovie
from ..now_showing_sync import sync_now_showing
from ._shared import run_background_job

NOW_SHOWING_JOB_TTL = 60 * 60


def _job_key(job_id: UUID) -> str:
	return f"now-showing:refresh:{job_id}"


def _active_job_key(city: str) -> str:
	return f"now-showing:refresh:active:{city.casefold()}"


def _job_get(job_id: UUID):
	value = cache.get(_job_key(job_id))
	return value if isinstance(value, dict) else None


def _job_set(job_id: UUID, value: dict) -> None:
	cache.set(_job_key(job_id), value, timeout=NOW_SHOWING_JOB_TTL)


def _run_refresh_job(*, job_id: UUID, city: str, source_url: str, sync_key: str) -> None:
	try:
		result = sync_now_showing(city=city, source_url=source_url, sync_key=sync_key)
		now = timezone.now().isoformat()
		_job_set(job_id, {"status": "succeeded", "finished_at": now, "result": result})
	except Exception as exc:
		_job_set(job_id, {"status": "failed", "finished_at": timezone.now().isoformat(), "error": str(exc)[:500]})
	finally:
		cache.delete(_active_job_key(city))


@login_required
def now_showing(request):
	city = (request.GET.get("city") or getattr(settings, "BOOKYOURSHOW_CITY", "Bengaluru")).strip()
	if not city:
		city = "Bengaluru"
	movies = NowShowingMovie.objects.filter(city__iexact=city, is_active=True)
	last_synced_at = movies.aggregate(value=Max("last_seen_at"))["value"]
	return render(
		request,
		"catalog/now_showing.html",
		{
			"city": city,
			"movies": movies,
			"last_synced_at": last_synced_at,
		},
	)


@login_required
def now_showing_refresh(request: HttpRequest) -> JsonResponse:
	if request.method != "POST":
		return JsonResponse({"error": "POST required"}, status=405)
	city = (request.POST.get("city") or getattr(settings, "BOOKYOURSHOW_CITY", "Bengaluru")).strip() or "Bengaluru"
	active_id = cache.get(_active_job_key(city))

	def progress_url(job_id: UUID) -> str:
		return reverse("now_showing_refresh_progress", kwargs={"job_id": str(job_id)})

	if active_id:
		try:
			job_id = UUID(str(active_id))
			if _job_get(job_id):
				return JsonResponse({"status": "already_running", "job_id": str(job_id), "progress_url": progress_url(job_id)})
		except ValueError:
			pass

	job_id = uuid4()
	_job_set(job_id, {"status": "running", "started_at": timezone.now().isoformat()})
	cache.set(_active_job_key(city), str(job_id), timeout=NOW_SHOWING_JOB_TTL)
	run_background_job(
		_run_refresh_job,
		job_id=job_id,
		city=city,
		source_url=getattr(settings, "BOOKYOURSHOW_SYNC_URL", ""),
		sync_key=getattr(settings, "BOOKYOURSHOW_SYNC_KEY", ""),
	)
	return JsonResponse({"status": "started", "job_id": str(job_id), "progress_url": progress_url(job_id)})


@login_required
def now_showing_refresh_progress(request: HttpRequest, job_id: UUID) -> JsonResponse:
	job = _job_get(job_id)
	if job is None:
		return JsonResponse({"status": "missing", "error": "Refresh job not found or expired."}, status=404)
	return JsonResponse(job)
