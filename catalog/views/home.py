from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import CompanyFollow, PersonFollow
from ..new_movie_helpers import get_person_comeback_info
from ..services import get_company_status_snapshot, get_person_deathday
from .diary import _diary_sync_start_background
from ._shared import _role_category


def home(request: HttpRequest) -> HttpResponse:
	if request.user.is_authenticated:
		_diary_sync_start_background(request.user)
		person_follows = (
			PersonFollow.objects.select_related("person")
			.defer("person__tmdb_raw")
			.filter(user=request.user)
			.order_by("role", "person__name")
		)
		for follow in person_follows:
			follow.person_deathday = get_person_deathday(follow.person)
			follow.comeback_info = get_person_comeback_info(
				follow.person.tmdb_credits_raw or {},
				followed_role=follow.role,
				deathday=follow.person_deathday,
			)
		company_follows = (
			CompanyFollow.objects.select_related("company")
			.defer("company__tmdb_raw")
			.filter(user=request.user)
			.order_by("company__name")
		)

		directors = [f for f in person_follows if _role_category(f.role) == "director"]
		actors = [f for f in person_follows if _role_category(f.role) == "actor"]
		crew = [f for f in person_follows if _role_category(f.role) == "crew"]
		# Compute studio status for each followed company
		companies = []
		for f in company_follows:
			_, f.studio_status = get_company_status_snapshot(f.company)
			companies.append(f)
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
