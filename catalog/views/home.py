from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import CompanyFollow, PersonFollow
from ..new_movie_helpers import get_person_comeback_info
from ._shared import _role_category


def home(request: HttpRequest) -> HttpResponse:
	if request.user.is_authenticated:
		person_follows = (
			PersonFollow.objects.select_related("person")
			.filter(user=request.user)
			.order_by("role", "person__name")
		)
		for follow in person_follows:
			follow.comeback_info = get_person_comeback_info(
				follow.person.tmdb_credits_raw or {},
				followed_role=follow.role,
				deathday=(follow.person.tmdb_raw or {}).get("deathday"),
			)
		company_follows = (
			CompanyFollow.objects.select_related("company")
			.filter(user=request.user)
			.order_by("company__name")
		)

		directors = [f for f in person_follows if _role_category(f.role) == "director"]
		actors = [f for f in person_follows if _role_category(f.role) == "actor"]
		crew = [f for f in person_follows if _role_category(f.role) == "crew"]
		companies = company_follows
	else:
		directors = []
		actors = []
		crew = []
		companies = []

	next_url = (request.GET.get("next") or "").strip() or None

	return render(
		request,
		"catalog/home.html",
		{
			"directors": directors,
			"actors": actors,
			"crew": crew,
			"companies": companies,
			"next_url": next_url,
		},
	)
