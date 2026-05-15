from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from catalog.models import CompanyFollow, PersonFollow
from catalog.services import get_person_status_label

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


@login_required
def profile(request: HttpRequest) -> HttpResponse:
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

	# Attach a computed status label to each follow for UI display
	for f in directors + actors + crew:
		try:
			f.status = get_person_status_label(f.person, followed_role=f.role)
		except Exception:
			f.status = ""

	# Attach a computed status label to each follow for UI display
	for f in directors + actors + crew:
		try:
			f.status = get_person_status_label(f.person, followed_role=f.role)
		except Exception:
			f.status = ""

	return render(
		request,
		"accounts/profile.html",
		{
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

	return render(
		request,
		"accounts/user_following.html",
		{
			"target_user": target_user,
			"is_self": target_user.pk == request.user.pk,
			"directors": directors,
			"actors": actors,
			"crew": crew,
			"companies": company_follows,
			"follow_count": len(person_follows) + len(company_follows),
		},
	)
