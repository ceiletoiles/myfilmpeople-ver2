from __future__ import annotations

import csv
import io
import os
import shutil
import tempfile
import threading
import zipfile
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from uuid import UUID, uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from ..forms import DiaryAccountForm, DiaryImportForm
from ..models import DiaryAccount, DiaryEntry


DIARY_IMPORT_JOB_TTL_SECONDS = 60 * 60
DIARY_IMPORT_MAX_BYTES = 25 * 1024 * 1024


def _get_diary_account(user) -> DiaryAccount:
	account, _ = DiaryAccount.objects.get_or_create(user=user)
	return account


def _format_last_sync_label(account: DiaryAccount) -> str:
	if account.last_successful_sync_at is None:
		return "No sync yet"
	local_dt = timezone.localtime(account.last_successful_sync_at)
	day = local_dt.day
	hour = local_dt.hour % 12 or 12
	minute = local_dt.minute
	ampm = "AM" if local_dt.hour < 12 else "PM"
	return f"{local_dt.strftime('%b')} {day}, {local_dt.year} at {hour}:{minute:02d} {ampm}"


def _normalize_header_key(value: str) -> str:
	return " ".join((value or "").strip().lower().replace("_", " ").split())


def _first_nonempty(row: dict[str, str], names: set[str]) -> str:
	for key, value in row.items():
		if _normalize_header_key(key) in names:
			text = (value or "").strip()
			if text:
				return text
	return ""


def _parse_bool(value: str) -> bool:
	norm = (value or "").strip().lower()
	return norm in {"1", "true", "t", "yes", "y", "on", "liked", "like", "watched"}


def _parse_rating(value: str) -> Decimal | None:
	text = (value or "").strip()
	if not text:
		return None
	text = text.replace("½", ".5")
	try:
		return Decimal(text)
	except (InvalidOperation, ValueError):
		return None


def _parse_release_year(value: str) -> int | None:
	text = (value or "").strip()
	if not text:
		return None
	try:
		year = int(text[:4])
	except ValueError:
		return None
	return year if year > 0 else None


def _parse_watch_date(value: str) -> date | None:
	text = (value or "").strip()
	if not text:
		return None
	for fmt in ("%Y-%m-%d", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%m/%d/%Y"):
		try:
			return datetime.strptime(text, fmt).date()
		except ValueError:
			continue
	return None


def _parse_diary_row(row: dict[str, str]) -> dict[str, object] | None:
	title = _first_nonempty(row, {"name", "title", "film", "movie"})
	watched_date = _parse_watch_date(_first_nonempty(row, {"date", "watched date", "watched at", "watched"}))
	if not title or watched_date is None:
		return None

	release_year = _parse_release_year(_first_nonempty(row, {"year", "release year", "release year (film)", "release"}))
	return {
		"original_title": title,
		"original_release_year": release_year,
		"watched_date": watched_date,
		"rating": _parse_rating(_first_nonempty(row, {"rating", "score"})),
		"liked": _parse_bool(_first_nonempty(row, {"liked", "like"})),
		"rewatch": _parse_bool(_first_nonempty(row, {"rewatch", "rewatched"})),
		"review": _first_nonempty(row, {"review", "comment", "notes"}),
		"rss_guid": _first_nonempty(row, {"guid", "uri", "url", "letterboxd uri", "letterboxd url"}),
	}


def _load_diary_import_rows(temp_path: str) -> tuple[list[dict[str, object]], str]:
	if not os.path.exists(temp_path):
		raise FileNotFoundError("Upload file no longer exists.")

	source_name = os.path.basename(temp_path)
	rows: list[dict[str, object]] = []
	if temp_path.lower().endswith(".zip"):
		with zipfile.ZipFile(temp_path) as archive:
			csv_name = ""
			for candidate in archive.namelist():
				if candidate.lower().endswith(".csv"):
					csv_name = candidate
					if os.path.basename(candidate).lower() == "diary.csv":
						break
			if not csv_name:
				raise ValueError("ZIP file does not contain a CSV export.")
			source_name = os.path.basename(csv_name)
			with archive.open(csv_name) as raw:
				text = raw.read().decode("utf-8-sig", errors="replace")
				reader = csv.DictReader(io.StringIO(text))
				for row in reader:
					rows.append({str(k or ""): str(v or "") for k, v in row.items()})
	else:
		with open(temp_path, "r", encoding="utf-8-sig", newline="") as handle:
			reader = csv.DictReader(handle)
			for row in reader:
				rows.append({str(k or ""): str(v or "") for k, v in row.items()})

	return rows, source_name


def _diary_import_job_key(job_id: UUID) -> str:
	return f"diaryimport:v1:{str(job_id)}"


def _diary_import_active_key(user_id: int) -> str:
	return f"diaryimport:v1:active:{int(user_id)}"


def _diary_import_get(job_id: UUID) -> dict | None:
	value = cache.get(_diary_import_job_key(job_id))
	return value if isinstance(value, dict) else None


def _diary_import_set(job_id: UUID, data: dict) -> None:
	cache.set(_diary_import_job_key(job_id), data, timeout=DIARY_IMPORT_JOB_TTL_SECONDS)


def _diary_import_patch(job_id: UUID, **updates) -> dict | None:
	data = _diary_import_get(job_id)
	if data is None:
		return None
	data = {**data, **updates}
	_diary_import_set(job_id, data)
	return data


def _diary_import_progress_url(job_id: UUID) -> str:
	return reverse("diary_import_progress", kwargs={"job_id": str(job_id)})


def _upsert_diary_entry(user, row: dict[str, object]) -> tuple[str, DiaryEntry]:
	lookup = {
		"user": user,
		"original_title": str(row["original_title"]),
		"original_release_year": row["original_release_year"],
		"watched_date": row["watched_date"],
	}
	entry = DiaryEntry.objects.filter(**lookup).first()
	if entry is None:
		entry = DiaryEntry.objects.create(
			**lookup,
			rating=row["rating"],
			liked=bool(row["liked"]),
			rewatch=bool(row["rewatch"]),
			review=str(row["review"] or ""),
			rss_guid=str(row["rss_guid"] or ""),
		)
		return "created", entry

	changed_fields: list[str] = []
	for field_name, value in (
		("rating", row["rating"]),
		("liked", bool(row["liked"])),
		("rewatch", bool(row["rewatch"])),
		("review", str(row["review"] or "")),
		("rss_guid", str(row["rss_guid"] or "")),
	):
		if getattr(entry, field_name) != value:
			setattr(entry, field_name, value)
			changed_fields.append(field_name)

	if changed_fields:
		changed_fields.append("updated_at")
		entry.save(update_fields=changed_fields)
		return "updated", entry

	return "skipped", entry


def _run_diary_import_job(*, job_id: UUID, user_id: int, temp_path: str, source_name: str) -> None:
	close_old_connections()
	try:
		from django.contrib.auth import get_user_model

		User = get_user_model()
		user = User.objects.get(pk=user_id)

		try:
			rows, detected_source = _load_diary_import_rows(temp_path)
		except Exception as exc:
			_diary_import_patch(
				job_id,
				status="failed",
				finished_at=timezone.now().isoformat(),
				message=str(exc),
				current_label="Import failed",
			)
			return

		total_rows = len(rows)
		_diary_import_patch(
			job_id,
			status="running",
			started_at=timezone.now().isoformat(),
			finished_at=None,
			source_name=detected_source or source_name,
			total_rows=total_rows,
			processed_rows=0,
			created_entries=0,
			updated_entries=0,
			skipped_rows=0,
			current_label="Starting import...",
			current_title="",
		)

		created_entries = 0
		updated_entries = 0
		skipped_rows = 0
		processed_rows = 0
		require_review = 0
		last_guid = ""

		for idx, raw_row in enumerate(rows, start=1):
			parsed = _parse_diary_row(raw_row)
			if parsed is None:
				skipped_rows += 1
				processed_rows += 1
				_diary_import_patch(
					job_id,
					processed_rows=processed_rows,
					skipped_rows=skipped_rows,
					current_label=f"Skipping row {idx}/{total_rows}...",
				)
				continue

			title = str(parsed["original_title"])
			year = parsed["original_release_year"]
			last_guid = str(parsed["rss_guid"] or last_guid)
			_diary_import_patch(
				job_id,
				current_label=f"Importing {idx}/{total_rows}...",
				current_title=title + (f" ({year})" if year else ""),
			)
			status, _entry = _upsert_diary_entry(user, parsed)
			if status == "created":
				created_entries += 1
			elif status == "updated":
				updated_entries += 1
			else:
				skipped_rows += 1

			if not _entry.tmdb_id:
				require_review += 1

			processed_rows += 1
			_diary_import_patch(
				job_id,
				processed_rows=processed_rows,
				created_entries=created_entries,
				updated_entries=updated_entries,
				skipped_rows=skipped_rows,
				require_review=require_review,
				newest_processed_guid=last_guid,
			)

		account = _get_diary_account(user)
		if last_guid:
			account.newest_processed_guid = last_guid
			account.save(update_fields=["newest_processed_guid", "updated_at"])

		finished_at = timezone.now().isoformat()
		message = f"Imported {created_entries + updated_entries} entries."
		if require_review:
			message += f" {require_review} require review."
		_diary_import_patch(
			job_id,
			status="done",
			finished_at=finished_at,
			current_label="Complete",
			message=message,
		)
	finally:
		try:
			os.remove(temp_path)
		except OSError:
			pass
		try:
			cache.delete(_diary_import_active_key(user_id))
		except Exception:
			pass
		close_old_connections()


def _diary_import_context(account: DiaryAccount, form: DiaryAccountForm | DiaryImportForm) -> dict[str, object]:
	username = account.letterboxd_username.strip()
	return {
		"form": form,
		"import_form": DiaryImportForm(),
		"letterboxd_username": username,
		"connection_status": "Connected" if username else "Not connected",
		"last_sync_label": _format_last_sync_label(account),
		"has_letterboxd_account": bool(username),
		"rss_feed_url": f"https://letterboxd.com/{username}/rss/" if username else "",
		"diary_account": account,
	}


@login_required
def diary(request: HttpRequest) -> HttpResponse:
	account = _get_diary_account(request.user)
	form = DiaryAccountForm(initial={"letterboxd_username": account.letterboxd_username})
	import_form = DiaryImportForm()
	context = _diary_import_context(account, form)
	context["import_form"] = import_form
	return render(request, "catalog/diary.html", context)


@login_required
def diary_settings(request: HttpRequest) -> HttpResponse:
	if request.method != "POST":
		return redirect("diary")

	account = _get_diary_account(request.user)
	form = DiaryAccountForm(request.POST)
	import_form = DiaryImportForm()
	if not form.is_valid():
		messages.error(request, "Check the Letterboxd username and try again.")
		context = _diary_import_context(account, form)
		context["import_form"] = import_form
		return render(request, "catalog/diary.html", context, status=400)

	new_username = form.cleaned_data["letterboxd_username"]
	if new_username != account.letterboxd_username:
		account.letterboxd_username = new_username
		account.save(update_fields=["letterboxd_username", "updated_at"])
		messages.success(request, "Letterboxd username saved.")
	else:
		messages.info(request, "Letterboxd username is unchanged.")
	return redirect("diary")


@login_required
def diary_import_start(request: HttpRequest) -> HttpResponse:
	if request.method != "POST":
		return JsonResponse({"ok": False, "error": "POST required."}, status=405)

	form = DiaryImportForm(request.POST, request.FILES)
	if not form.is_valid():
		return JsonResponse({"ok": False, "error": "Select a CSV or ZIP export."}, status=400)

	upload = form.cleaned_data["import_file"]
	file_name = getattr(upload, "name", "letterboxd-export")
	upload_size = getattr(upload, "size", 0) or 0
	if upload_size and int(upload_size) > DIARY_IMPORT_MAX_BYTES:
		return JsonResponse({"ok": False, "error": "File is too large."}, status=400)

	suffix = os.path.splitext(file_name)[1].lower()
	if suffix not in {".csv", ".zip"}:
		return JsonResponse({"ok": False, "error": "Upload a CSV or ZIP file."}, status=400)

	active = cache.get(_diary_import_active_key(request.user.id))
	try:
		active_uuid = UUID(str(active)) if active else None
	except Exception:
		active_uuid = None
	if active_uuid is not None:
		existing = _diary_import_get(active_uuid)
		if existing and existing.get("user_id") == request.user.id and existing.get("status") == "running":
			return JsonResponse(
				{
					"ok": True,
					"status": "running",
					"job_id": str(active_uuid),
					"progress_url": _diary_import_progress_url(active_uuid),
				}
			)

	job_id = uuid4()
	cache.set(_diary_import_active_key(request.user.id), str(job_id), timeout=DIARY_IMPORT_JOB_TTL_SECONDS)

	with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
		upload.seek(0)
		shutil.copyfileobj(upload, tmp)
		temp_path = tmp.name

	_diary_import_set(
		job_id,
		{
			"job_id": str(job_id),
			"user_id": request.user.id,
			"status": "running",
			"started_at": None,
			"finished_at": None,
			"source_name": file_name,
			"total_rows": 0,
			"processed_rows": 0,
			"created_entries": 0,
			"updated_entries": 0,
			"skipped_rows": 0,
			"require_review": 0,
			"current_label": "Queued...",
			"current_title": "",
			"newest_processed_guid": "",
			"progress_url": _diary_import_progress_url(job_id),
		},
	)

	thread = threading.Thread(
		target=_run_diary_import_job,
		kwargs={"job_id": job_id, "user_id": request.user.id, "temp_path": temp_path, "source_name": file_name},
		daemon=True,
	)
	thread.start()

	return JsonResponse(
		{
			"ok": True,
			"status": "running",
			"job_id": str(job_id),
			"progress_url": _diary_import_progress_url(job_id),
		}
	)


@login_required
def diary_import_progress(request: HttpRequest, job_id: str) -> HttpResponse:
	try:
		jid = UUID(str(job_id))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid job id."}, status=400)

	data = _diary_import_get(jid)
	if not data:
		return JsonResponse({"ok": False, "error": "Job not found."}, status=404)
	if int(data.get("user_id") or 0) != int(request.user.id):
		return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

	return JsonResponse({"ok": True, **data})
