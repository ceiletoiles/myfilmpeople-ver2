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
from catalog.services import get_person_status_key, get_person_status_label

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

	directors = [f for f in person_follows if _role_category(f.role) == "director"]
	actors = [f for f in person_follows if _role_category(f.role) == "actor"]
	crew = [f for f in person_follows if _role_category(f.role) == "crew"]

	for f in directors + actors + crew:
		_annotate_status(f)

	if selected_status != "all":
		directors = [f for f in directors if f.status_key == selected_status]
		actors = [f for f in actors if f.status_key == selected_status]
		crew = [f for f in crew if f.status_key == selected_status]

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
			"follow_count": len(person_follows) + len(company_follows),
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

	if selected_status != "all":
		directors = [f for f in directors if f.status_key == selected_status]
		actors = [f for f in actors if f.status_key == selected_status]
		crew = [f for f in crew if f.status_key == selected_status]

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
			"follow_count": len(person_follows) + len(company_follows),
		},
	)
