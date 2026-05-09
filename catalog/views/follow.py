from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
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
from ._shared import SESSION_KEY_HIDE_SELF_APPEARANCES, _get_session_bool, _person_role_options_from_credits


def _wants_json(request: HttpRequest) -> bool:
	accept = (request.headers.get("Accept") or "").lower()
	xrw = (request.headers.get("X-Requested-With") or "").lower()
	return ("application/json" in accept) or (xrw == "xmlhttprequest")


def _render_person_follow_controls(request: HttpRequest, *, tmdb_id: int) -> str:
	hide_self_appearances = _get_session_bool(
		request.session,
		SESSION_KEY_HIDE_SELF_APPEARANCES,
		default=True,
	)
	person = get_or_sync_person(tmdb_id)
	follows_qs = PersonFollow.objects.select_related("person").filter(
		user=request.user, person__tmdb_id=tmdb_id
	)
	follow_roles = sorted(set(follows_qs.values_list("role", flat=True))) if follows_qs.exists() else []
	follow_roles_set = set(follow_roles)
	note_text = (
		(follows_qs.order_by("-updated_at").values_list("notes", flat=True).first() or "")
		if follows_qs.exists()
		else ""
	)

	credits = person.tmdb_credits_raw or {}
	role_options = _person_role_options_from_credits(credits)
	role_options_remaining = [r for r in role_options if r not in follow_roles_set]

	return render_to_string(
		"catalog/_person_follow_controls.html",
		{
			"person": person,
			"follow_roles": follow_roles,
			"role_options": role_options,
			"role_options_remaining": role_options_remaining,
			"note_text": note_text,
			"hide_self_appearances": hide_self_appearances,
		},
		request=request,
	)


def _render_company_follow_controls(request: HttpRequest, *, tmdb_id: int) -> str:
	company = get_or_sync_company(tmdb_id)
	follow = CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).first()
	is_followed = bool(follow)
	note_text = follow.notes if follow else ""
	return render_to_string(
		"catalog/_company_follow_controls.html",
		{
			"company": company,
			"is_followed": is_followed,
			"note_text": note_text,
		},
		request=request,
	)


@login_required
def person_note(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("person_detail", tmdb_id=tmdb_id)

	wants_json = _wants_json(request)
	ajax_context = (request.POST.get("ajax_context") or "").strip().lower()
	notes = (request.POST.get("notes") or "").strip()

	qs = PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id)
	if not qs.exists():
		msg = "Follow this person to add notes."
		messages.error(request, msg)
		if wants_json:
			return JsonResponse({"ok": False, "error": msg}, status=400)
		return redirect("person_detail", tmdb_id=tmdb_id)

	# Shared per person: keep the note consistent across all followed roles.
	now = timezone.now()
	qs.update(notes=notes, updated_at=now)
	messages.success(request, "Notes saved.")

	if wants_json:
		payload: dict[str, object] = {
			"ok": True,
			"tmdb_id": tmdb_id,
			"message": "Notes saved.",
		}
		if ajax_context == "person_detail":
			payload["controls_target"] = "#person-follow-controls"
			payload["controls_html"] = _render_person_follow_controls(request, tmdb_id=tmdb_id)
		return JsonResponse(payload)

	return redirect("person_detail", tmdb_id=tmdb_id)


@login_required
def company_note(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("company_detail", tmdb_id=tmdb_id)

	wants_json = _wants_json(request)
	ajax_context = (request.POST.get("ajax_context") or "").strip().lower()
	notes = (request.POST.get("notes") or "").strip()

	follow = CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).first()
	if not follow:
		msg = "Follow this company to add notes."
		messages.error(request, msg)
		if wants_json:
			return JsonResponse({"ok": False, "error": msg}, status=400)
		return redirect("company_detail", tmdb_id=tmdb_id)

	follow.notes = notes
	follow.save(update_fields=["notes", "updated_at"])
	messages.success(request, "Notes saved.")

	if wants_json:
		payload: dict[str, object] = {
			"ok": True,
			"tmdb_id": tmdb_id,
			"message": "Notes saved.",
		}
		if ajax_context == "company_detail":
			payload["controls_target"] = "#company-follow-controls"
			payload["controls_html"] = _render_company_follow_controls(request, tmdb_id=tmdb_id)
		return JsonResponse(payload)

	return redirect("company_detail", tmdb_id=tmdb_id)


@login_required
def follow(request: HttpRequest) -> HttpResponse:
	if request.method != "POST":
		return redirect("search")

	wants_json = _wants_json(request)
	ajax_context = (request.POST.get("ajax_context") or "").strip().lower()

	entity_type = (request.POST.get("entity_type") or "").strip()
	tmdb_id_str = (request.POST.get("tmdb_id") or "").strip()
	role = (request.POST.get("role") or "").strip()

	try:
		tmdb_id = int(tmdb_id_str)
	except ValueError:
		messages.error(request, "Invalid TMDb id.")
		if wants_json:
			return JsonResponse({"ok": False, "error": "Invalid TMDb id."}, status=400)
		return redirect("search")

	if entity_type == "person":
		# Treat the follow moment as the baseline snapshot.
		person = get_or_sync_person(tmdb_id, force=True)
		credits = person.tmdb_credits_raw or {}
		valid_roles = set(_person_role_options_from_credits(credits))
		if role not in valid_roles:
			messages.error(request, "Select a valid role from this person's TMDb credits.")
			if wants_json:
				return JsonResponse(
					{
						"ok": False,
						"error": "Select a valid role from this person's TMDb credits.",
					},
					status=400,
				)
			return redirect("person_detail", tmdb_id=tmdb_id)

		pf, created = PersonFollow.objects.get_or_create(
			user=request.user,
			person=person,
			role=role,
			defaults={"name": person.name or ""},
		)
		if not created and (pf.name or "") != (person.name or ""):
			pf.name = person.name or ""
			pf.save(update_fields=["name", "updated_at"])
		messages.success(request, f"Now following {person.name} as {role}.")
		if wants_json:
			payload: dict[str, object] = {
				"ok": True,
				"entity_type": "person",
				"tmdb_id": tmdb_id,
				"role": role,
				"message": f"Now following {person.name} as {role}.",
			}
			if ajax_context == "person_detail":
				payload["controls_target"] = "#person-follow-controls"
				payload["controls_html"] = _render_person_follow_controls(request, tmdb_id=tmdb_id)
			return JsonResponse(payload)
		return redirect("home")

	if entity_type == "company":
		# Treat the follow moment as the baseline snapshot.
		company = get_or_sync_company(tmdb_id, force=True)
		# Pre-cache full company filmography so later syncs compare against the
		# state visible at follow time, not an older stale cache.
		max_pages = getattr(settings, "TMDB_COMPANY_FILMOGRAPHY_PREFETCH_MAX_PAGES", 0)
		try:
			max_pages_int = int(max_pages)
		except (TypeError, ValueError):
			max_pages_int = 0
		try:
			prefetch_company_filmography(
				company,
				force=True,
				max_pages=None if max_pages_int <= 0 else max_pages_int,
			)
		except Exception:
			# Non-fatal: following should still succeed even if TMDb is temporarily unavailable.
			pass
		cf, created = CompanyFollow.objects.get_or_create(
			user=request.user,
			company=company,
			defaults={"name": company.name or ""},
		)
		if not created and (cf.name or "") != (company.name or ""):
			cf.name = company.name or ""
			cf.save(update_fields=["name", "updated_at"])
		messages.success(request, f"Now following {company.name}.")
		if wants_json:
			payload = {
				"ok": True,
				"entity_type": "company",
				"tmdb_id": tmdb_id,
				"message": f"Now following {company.name}.",
			}
			if ajax_context == "company_detail":
				payload["controls_target"] = "#company-follow-controls"
				payload["controls_html"] = _render_company_follow_controls(request, tmdb_id=tmdb_id)
			return JsonResponse(payload)
		return redirect("home")

	messages.error(request, "Invalid entity type.")
	if wants_json:
		return JsonResponse({"ok": False, "error": "Invalid entity type."}, status=400)
	return redirect("search")


@login_required
def person_sync(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("person_detail", tmdb_id=tmdb_id)

	if not PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id).exists():
		messages.error(request, "Follow this person to sync and cache their data.")
		return redirect("person_detail", tmdb_id=tmdb_id)

	# Get old movie IDs before syncing
	person = get_or_sync_person(tmdb_id, force=False)
	# Avoid stale cache objects: snapshot old state from DB.
	try:
		person.refresh_from_db(fields=["tmdb_credits_raw", "tmdb_last_sync_at"])
	except Exception:
		pass
	old_credits = person.tmdb_credits_raw or {}
	old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(old_credits.get("crew"), list)

	# Sync new data
	person = get_or_sync_person(tmdb_id, force=True)
	PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id).update(name=person.name)

	# Get new movie IDs after syncing
	new_credits = person.tmdb_credits_raw or {}
	new_movie_ids = extract_movie_ids_from_credits(new_credits)

	notifications_created = 0

	# Record new arrivals (only if a baseline existed pre-sync).
	if old_baseline_present:
		follows = PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id)
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
				user=request.user,
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

	messages.success(
		request,
		(
			f"Synced person data from TMDb. Cached movies: {len(new_movie_ids)}. "
			f"Notifications: {notifications_created}."
		),
	)
	return redirect("person_detail", tmdb_id=tmdb_id)


@login_required
def company_sync(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("company_detail", tmdb_id=tmdb_id)

	if not CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).exists():
		messages.error(request, "Follow this company to sync and cache their data.")
		return redirect("company_detail", tmdb_id=tmdb_id)

	# Get old movie IDs before syncing
	company = get_or_sync_company(tmdb_id, force=False)
	# Avoid stale cache objects: snapshot old state from DB.
	try:
		company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
	except Exception:
		pass
	old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
	old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
	old_pages = old_tmdb_raw.get("discover_movies_pages")
	old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
	old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)

	# Sync new data
	company = get_or_sync_company(tmdb_id, force=True)
	# Refresh full filmography for followed companies.
	max_pages = getattr(settings, "TMDB_COMPANY_FILMOGRAPHY_PREFETCH_MAX_PAGES", 0)
	try:
		max_pages_int = int(max_pages)
	except (TypeError, ValueError):
		max_pages_int = 0
	fetched_pages = 0
	try:
		fetched_pages = prefetch_company_filmography(
			company,
			force=True,
			max_pages=None if max_pages_int <= 0 else max_pages_int,
		)
	except Exception:
		pass

	# Get new movie IDs after syncing
	new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
	new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw)
	new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw)

	# Progress info (best-effort): pages cached vs total_pages.
	pages_cached = 0
	total_pages = None
	try:
		pages = new_tmdb_raw.get("discover_movies_pages")
		meta = new_tmdb_raw.get("discover_movies_meta")
		if isinstance(pages, dict):
			pages_cached = len(pages)
		if isinstance(meta, dict):
			total_pages_val = meta.get("total_pages")
			if isinstance(total_pages_val, int) and total_pages_val > 0:
				total_pages = total_pages_val
	except Exception:
		pass

	notifications_created = 0

	# Record new arrivals (only if a baseline existed pre-sync).
	# This avoids treating the first successful filmography prefetch as "new".
	if old_baseline_present:
		notifications_created = record_new_movie_arrivals(
			user=request.user,
			source_type="company",
			source_id=tmdb_id,
			source_name=company.name,
			old_movie_ids=old_movie_ids,
			new_movie_ids=new_movie_ids,
			role="studio",
			old_release_dates=old_release_dates,
			new_release_dates=new_release_dates,
		)

	CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).update(name=company.name)
	if total_pages is not None:
		left_pages = max(0, int(total_pages) - int(pages_cached))
		pages_msg = f"Pages cached: {pages_cached}/{total_pages} (left {left_pages}, fetched {fetched_pages})."
	else:
		pages_msg = f"Pages cached: {pages_cached} (fetched {fetched_pages})."

	messages.success(
		request,
		(
			f"Synced company data from TMDb. Cached movies: {len(new_movie_ids)}. "
			f"{pages_msg} Notifications: {notifications_created}."
		),
	)
	return redirect("company_detail", tmdb_id=tmdb_id)


@login_required
def sync_all_followed(request: HttpRequest) -> HttpResponse:
	if request.method != "POST":
		return redirect("home")

	# Optional: redirect back to where the action was triggered.
	next_url = (request.POST.get("next") or "").strip()
	if not next_url:
		next_url = (request.META.get("HTTP_REFERER") or "").strip()
	if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
		next_url = ""
	redirect_to = next_url or "/"

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

	total_people = len(person_ids)
	total_companies = len(company_ids)

	if not person_ids and not company_ids:
		messages.info(request, "Nothing to sync yet - you are not following any people/companies.")
		return redirect(redirect_to)

	synced_people = 0
	synced_companies = 0
	fail_people = 0
	fail_companies = 0
	notifications_created = 0

	for pid in person_ids:
		try:
			# Get old movie IDs before syncing
			person = get_or_sync_person(pid, force=False)
			# Avoid stale cache objects: snapshot old state from DB.
			try:
				person.refresh_from_db(fields=["tmdb_credits_raw", "tmdb_last_sync_at"])
			except Exception:
				pass
			old_credits = person.tmdb_credits_raw or {}
			old_movie_ids = extract_movie_ids_from_credits(old_credits)
			old_release_dates = extract_movie_release_dates_from_credits(old_credits)
			old_baseline_present = isinstance(old_credits.get("cast"), list) or isinstance(old_credits.get("crew"), list)

			# Sync new data
			person = get_or_sync_person(pid, force=True)
			PersonFollow.objects.filter(user=request.user, person__tmdb_id=pid).update(name=person.name)

			# Get new movie IDs after syncing
			new_credits = person.tmdb_credits_raw or {}
			new_movie_ids = extract_movie_ids_from_credits(new_credits)
			new_release_dates = extract_movie_release_dates_from_credits(new_credits)

			# Record new arrivals (only if a baseline existed pre-sync).
			if old_baseline_present:
				follows = PersonFollow.objects.filter(user=request.user, person__tmdb_id=pid)
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
						user=request.user,
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

	for cid in company_ids:
		try:
			# Get old movie IDs before syncing
			company = get_or_sync_company(cid, force=False)
			# Avoid stale cache objects: snapshot old state from DB.
			try:
				company.refresh_from_db(fields=["tmdb_raw", "tmdb_last_sync_at"])
			except Exception:
				pass
			old_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			old_movie_ids = extract_movie_ids_from_filmography(old_tmdb_raw)
			old_pages = old_tmdb_raw.get("discover_movies_pages")
			old_baseline_present = isinstance(old_pages, dict) and len(old_pages) > 0
			old_release_dates = extract_movie_release_dates_from_filmography(old_tmdb_raw)

			# Sync new data
			company = get_or_sync_company(cid, force=True)
			# Refresh full filmography for followed companies (best-effort, capped by settings).
			max_pages = getattr(settings, "TMDB_COMPANY_FILMOGRAPHY_PREFETCH_MAX_PAGES", 0)
			try:
				max_pages_int = int(max_pages)
			except (TypeError, ValueError):
				max_pages_int = 0
			try:
				prefetch_company_filmography(
					company,
					force=True,
					max_pages=None if max_pages_int <= 0 else max_pages_int,
				)
			except Exception:
				pass

			# Get new movie IDs after syncing
			new_tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			new_movie_ids = extract_movie_ids_from_filmography(new_tmdb_raw)
			new_release_dates = extract_movie_release_dates_from_filmography(new_tmdb_raw)

			# Record new arrivals (only if a baseline existed pre-sync).
			if old_baseline_present:
				notifications_created += record_new_movie_arrivals(
					user=request.user,
					source_type="company",
					source_id=cid,
					source_name=company.name,
					old_movie_ids=old_movie_ids,
					new_movie_ids=new_movie_ids,
					role="studio",
					old_release_dates=old_release_dates,
					new_release_dates=new_release_dates,
				)

			CompanyFollow.objects.filter(user=request.user, company__tmdb_id=cid).update(name=company.name)
			synced_companies += 1
		except Exception:
			fail_companies += 1

	fail_total = fail_people + fail_companies
	left_total = fail_total
	base_msg = (
		f"Sync complete: people {synced_people}/{total_people}, "
		f"companies {synced_companies}/{total_companies}. "
		f"Notifications: {notifications_created}. "
		f"Left: {left_total}."
	)
	if fail_total:
		messages.error(request, base_msg)
	else:
		messages.success(request, base_msg)

	return redirect(redirect_to)


@login_required
def person_unfollow(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("person_detail", tmdb_id=tmdb_id)

	wants_json = _wants_json(request)
	ajax_context = (request.POST.get("ajax_context") or "").strip().lower()

	role = (request.POST.get("role") or "").strip()
	qs = PersonFollow.objects.filter(user=request.user, person__tmdb_id=tmdb_id)

	if role:
		deleted_count, _ = qs.filter(role=role).delete()
		if deleted_count:
			messages.success(request, f"Unfollowed role: {role}.")
			if wants_json:
				payload: dict[str, object] = {
					"ok": True,
					"tmdb_id": tmdb_id,
					"role": role,
					"message": f"Unfollowed role: {role}.",
				}
				if ajax_context == "person_detail":
					payload["controls_target"] = "#person-follow-controls"
					payload["controls_html"] = _render_person_follow_controls(request, tmdb_id=tmdb_id)
				return JsonResponse(payload)
		else:
			messages.info(request, f"You were not following this person as {role}.")
			if wants_json:
				return JsonResponse(
					{
						"ok": False,
						"error": f"You were not following this person as {role}.",
					},
					status=400,
				)
		return redirect("person_detail", tmdb_id=tmdb_id)

	deleted_count, _ = qs.delete()
	if deleted_count:
		messages.success(request, "Unfollowed person (all roles).")
		if wants_json:
			payload = {
				"ok": True,
				"tmdb_id": tmdb_id,
				"message": "Unfollowed person (all roles).",
			}
			if ajax_context == "person_detail":
				payload["controls_target"] = "#person-follow-controls"
				payload["controls_html"] = _render_person_follow_controls(request, tmdb_id=tmdb_id)
			return JsonResponse(payload)
	else:
		messages.info(request, "You were not following this person.")
		if wants_json:
			return JsonResponse(
				{"ok": False, "error": "You were not following this person."},
				status=400,
			)
	return redirect("person_detail", tmdb_id=tmdb_id)


@login_required
def company_unfollow(request: HttpRequest, tmdb_id: int) -> HttpResponse:
	if request.method != "POST":
		return redirect("company_detail", tmdb_id=tmdb_id)

	wants_json = _wants_json(request)
	ajax_context = (request.POST.get("ajax_context") or "").strip().lower()

	deleted_count, _ = CompanyFollow.objects.filter(user=request.user, company__tmdb_id=tmdb_id).delete()
	if deleted_count:
		messages.success(request, "Unfollowed company.")
		if wants_json:
			payload = {
				"ok": True,
				"tmdb_id": tmdb_id,
				"message": "Unfollowed company.",
			}
			if ajax_context == "company_detail":
				payload["controls_target"] = "#company-follow-controls"
				payload["controls_html"] = _render_company_follow_controls(request, tmdb_id=tmdb_id)
			return JsonResponse(payload)
	else:
		messages.info(request, "You were not following this company.")
		if wants_json:
			return JsonResponse(
				{"ok": False, "error": "You were not following this company."},
				status=400,
			)
	return redirect("company_detail", tmdb_id=tmdb_id)
