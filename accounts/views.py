from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from urllib.parse import urlencode

from catalog.models import CompanyFollow, PersonFollow
from catalog.services import (
	get_or_sync_company_tba_movies_page,
	get_person_status_key,
	get_person_status_label,
)
from django.utils import timezone
from datetime import date
from catalog.views._shared import _parse_iso_date, _add_years_safe

from .forms import SignupForm


def signup(request: HttpRequest) -> HttpResponse:
	if request.user.is_authenticated:
		return redirect("home")

	next_url = (request.POST.get("next") or request.GET.get("next") or "").strip() or None

	if request.method == "POST":
		form = SignupForm(request.POST)
		if form.is_valid():
			user = form.save()
			login(request, user)
			messages.success(request, "Account created.")
			if next_url and url_has_allowed_host_and_scheme(
				next_url,
				allowed_hosts={request.get_host()},
				require_https=request.is_secure(),
			):
				return redirect(next_url)
			return redirect(settings.LOGIN_REDIRECT_URL)
	else:
		form = SignupForm()

	return render(request, "accounts/signup.html", {"form": form, "next": next_url})


def _role_category(role: str) -> str:
	role_n = (role or "").strip().lower()
	if role_n == "director":
		return "director"
	if role_n == "actor":
		return "actor"
	return "crew"


def _build_status_filters(base_path: str, selected_status: str) -> list[dict[str, object]]:
	options = [
		("all", "Status"),
		("inactive", "Inactive"),
		("deceased", "Deceased"),
		("tba", "TBA"),
		("upcoming", "Upcoming"),
		("idle", "Idle"),
	]
	status_filters: list[dict[str, object]] = []
	for key, label in options:
		query = {} if key == "all" else {"status": key}
		url = base_path if not query else f"{base_path}?{urlencode(query)}"
		status_filters.append(
			{
				"key": key,
				"label": label,
				"url": url,
				"active": key == selected_status,
			}
		)
	return status_filters


def _normalize_status_key(value: str | None) -> str:
	status = (value or "").strip().lower()
	return status if status in {"inactive", "deceased", "tba", "upcoming", "idle"} else "all"


def _annotate_status(follow) -> None:
	try:
		follow.status = get_person_status_label(follow.person, followed_role=follow.role)
		follow.status_key = get_person_status_key(follow.person, followed_role=follow.role)
	except Exception:
		follow.status = ""
		follow.status_key = ""


def _annotate_company_status(follow) -> None:
	"""Annotate a CompanyFollow with `status` and `status_key`.
	Keys: upcoming, tba, inactive, idle
	"""
	try:
		company = follow.company
		today = timezone.now().date()
		ten_years_ago = _add_years_safe(today, -10)
		tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		pages = tmdb_raw.get("discover_movies_pages") or {}

		upcoming_with_date = 0
		upcoming_no_date = 0
		latest_past_release: date | None = None

		for payload in (pages.values() or []):
			if not isinstance(payload, dict):
				continue
			for m in (payload.get("results") or []):
				if not isinstance(m, dict):
					continue
				release_date_str = (m.get("release_date") or "").strip()
				release_dt = _parse_iso_date(release_date_str)
				if release_dt is not None and release_dt > today:
					upcoming_with_date += 1
				elif not release_date_str:
					upcoming_no_date += 1
				elif release_dt is not None and release_dt <= today:
					if latest_past_release is None or release_dt > latest_past_release:
						latest_past_release = release_dt

		if upcoming_with_date > 0:
			follow.status_key = "upcoming"
			follow.status = "Upcoming"
		elif upcoming_no_date > 0:
			follow.status_key = "tba"
			follow.status = "TBA"
		else:
			tba_items: list[dict] = []
			try:
				tba_items, _, _ = get_or_sync_company_tba_movies_page(
					company,
					page=1,
					page_size=1,
				)
			except Exception:
				tba_items = []

			if tba_items:
				follow.status_key = "tba"
				follow.status = "TBA"
			elif latest_past_release is not None and latest_past_release < ten_years_ago:
				follow.status_key = "inactive"
				follow.status = "Inactive"
			else:
				follow.status_key = "idle"
				follow.status = "Idle"
	except Exception:
		follow.status = ""
		follow.status_key = ""


@login_required
def profile(request: HttpRequest) -> HttpResponse:
	selected_status = _normalize_status_key(request.GET.get("status"))
	person_follows = (
		PersonFollow.objects.select_related("person")
		.filter(user=request.user)
		.order_by("role", "person__name")
	)
	company_follows = (
		CompanyFollow.objects.select_related("company")
		.filter(user=request.user)
		.order_by("company__name")
	)
	total_company_count = len(company_follows)

	directors = [f for f in person_follows if _role_category(f.role) == "director"]
	actors = [f for f in person_follows if _role_category(f.role) == "actor"]
	crew = [f for f in person_follows if _role_category(f.role) == "crew"]

	for f in directors + actors + crew:
		_annotate_status(f)

	# Annotate company follows with status
	for c in company_follows:
		_annotate_company_status(c)

	if selected_status != "all":
		directors = [f for f in directors if f.status_key == selected_status]
		actors = [f for f in actors if f.status_key == selected_status]
		crew = [f for f in crew if f.status_key == selected_status]
		company_follows = [c for c in company_follows if getattr(c, "status_key", "") == selected_status]

	tab_counts = {
		"director": len(directors),
		"actor": len(actors),
		"crew": len(crew),
		"studio": len(company_follows),
	}

	status_filters = _build_status_filters(request.path, selected_status)
	selected_status_label = next((f["label"] for f in status_filters if f["key"] == selected_status), "Status")

	return render(
		request,
		"accounts/profile.html",
		{
			"status_filters": status_filters,
			"selected_status": selected_status,
			"selected_status_label": selected_status_label,
			"directors": directors,
			"actors": actors,
			"crew": crew,
			"companies": company_follows,
			"tab_counts": tab_counts,
			"follow_count": len(person_follows) + total_company_count,
		},
	)


@login_required
def user_following(request: HttpRequest, username: str) -> HttpResponse:
	User = get_user_model()
	target_user = get_object_or_404(User, username__iexact=(username or "").strip())
	selected_status = _normalize_status_key(request.GET.get("status"))

	person_follows = (
		PersonFollow.objects.select_related("person")
		.filter(user=target_user)
		.order_by("role", "person__name")
	)
	company_follows = (
		CompanyFollow.objects.select_related("company")
		.filter(user=target_user)
		.order_by("company__name")
	)

	directors = [f for f in person_follows if _role_category(f.role) == "director"]
	actors = [f for f in person_follows if _role_category(f.role) == "actor"]
	crew = [f for f in person_follows if _role_category(f.role) == "crew"]

	for f in directors + actors + crew:
		_annotate_status(f)

	# Annotate company follows with status
	for c in company_follows:
		_annotate_company_status(c)

	if selected_status != "all":
		directors = [f for f in directors if f.status_key == selected_status]
		actors = [f for f in actors if f.status_key == selected_status]
		crew = [f for f in crew if f.status_key == selected_status]
		company_follows = [c for c in company_follows if getattr(c, "status_key", "") == selected_status]

	tab_counts = {
		"director": len(directors),
		"actor": len(actors),
		"crew": len(crew),
		"studio": len(company_follows),
	}

	status_filters = _build_status_filters(request.path, selected_status)
	selected_status_label = next((f["label"] for f in status_filters if f["key"] == selected_status), "Status")

	return render(
		request,
		"accounts/user_following.html",
		{
			"target_user": target_user,
			"is_self": target_user.pk == request.user.pk,
			"status_filters": status_filters,
			"selected_status": selected_status,
			"selected_status_label": selected_status_label,
			"directors": directors,
			"actors": actors,
			"crew": crew,
			"companies": company_follows,
			"tab_counts": tab_counts,
			"follow_count": len(person_follows) + len(company_follows),
		},
	)
