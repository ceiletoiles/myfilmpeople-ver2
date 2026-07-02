from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import CompanyFollow, PersonFollow
from ..new_movie_helpers import get_person_comeback_info
from django.utils import timezone
from datetime import date
from ._shared import _role_category, _parse_iso_date, _add_years_safe


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
		# Compute studio status for each followed company
		today = timezone.now().date()
		ten_years_ago = _add_years_safe(today, -10)
		companies = []
		for f in company_follows:
			# Default
			f.studio_status = "idle"
			company = f.company
			tmdb_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
			pages = tmdb_raw.get("discover_movies_pages") or {}
			upcoming_with_date = 0
			upcoming_no_date = 0
			latest_past_release: date | None = None
			for payload in (pages.values() or []):
				if not isinstance(payload, dict):
					continue
				for m in [movie for movie in (payload.get("results") or []) if isinstance(movie, dict)]:
					if not isinstance(m, dict):
						continue
					release_date_str = str(m.get("release_date") or "").strip()
					release_dt = _parse_iso_date(release_date_str)
					if release_dt is not None and release_dt > today:
						upcoming_with_date += 1
					elif not release_date_str:
						# No announced date but present in company discover results
						# Treat as TBA/upcoming without date
						upcoming_no_date += 1
					elif release_dt is not None and release_dt <= today:
						if latest_past_release is None or release_dt > latest_past_release:
							latest_past_release = release_dt

			# Priority: announced upcoming > TBA (unannounced upcoming) > inactive (10y gap) > idle
			if upcoming_with_date > 0:
				f.studio_status = "upcoming"
			elif upcoming_no_date > 0:
				f.studio_status = "tba"
			elif latest_past_release is not None and latest_past_release < ten_years_ago:
				f.studio_status = "inactive"
			else:
				f.studio_status = "idle"
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
