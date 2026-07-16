from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse

from ..models import CompanyFollow, Person, PersonFollow
from ..services import get_company_homepage, get_person_deathday
from .diary import _diary_sync_start_background


ROLE_TABS = [
	{"key": "director", "label": "Director"},
	{"key": "actor", "label": "Actor"},
	{"key": "crew", "label": "Crew"},
	{"key": "studio", "label": "Studio"},
]

EXTERNAL_ID_TABS = [
	{"key": "instagram", "label": "Instagram", "field": "instagram_id", "url_prefix": "https://www.instagram.com/"},
	{"key": "x", "label": "X", "field": "twitter_id", "url_prefix": "https://x.com/"},
	{"key": "facebook", "label": "Facebook", "field": "facebook_id", "url_prefix": "https://www.facebook.com/"},
	{"key": "youtube", "label": "YouTube", "field": "youtube_id", "url_prefix": "https://www.youtube.com/@"},
	{"key": "wikidata", "label": "Wikidata", "field": "wikidata_id", "url_prefix": "https://www.wikidata.org/wiki/"},
	{"key": "homepage", "label": "Homepage", "field": "homepage", "url_prefix": ""},
]

DEFAULT_ROLE_KEY = "director"
DEFAULT_EXTERNAL_KEY = "instagram"


def _normalize_key(value: str, *, allowed: set[str], default: str) -> str:
	key = (value or "").strip().lower()
	if key == "twitter" and "x" in allowed:
		return "x"
	return key if key in allowed else default


def _primary_role_category(role: str) -> str:
	role_name = (role or "").strip().lower()
	if role_name == "director":
		return "director"
	if role_name == "actor":
		return "actor"
	return "crew"


def _external_id_value(person: Person, field_name: str) -> str:
	raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}
	if field_name == "homepage":
		return str(raw.get("homepage") or "").strip()
	external_ids = raw.get("external_ids")
	if not isinstance(external_ids, dict):
		return ""
	value = str(external_ids.get(field_name) or "").strip()
	if field_name == "youtube_id":
		return value.lstrip("@")
	return value


def _external_display_value(tab: dict[str, str], value: str) -> str:
	if not value:
		return ""
	if tab["key"] in {"instagram", "x", "twitter"}:
		return f"@{value.lstrip('@')}"
	return value


def _external_url(tab: dict[str, str], value: str) -> str:
	if not value:
		return ""
	if tab["key"] == "homepage":
		return value
	return f"{tab['url_prefix']}{value}"


def _build_tab_url(role_key: str, external_key: str) -> str:
	query = urlencode({"role": role_key, "external": external_key})
	return f"{reverse('connect')}?{query}"


@login_required
def connect(request: HttpRequest) -> HttpResponse:
	_diary_sync_start_background(request.user)
	allowed_roles = {item["key"] for item in ROLE_TABS}
	role_key = _normalize_key(request.GET.get("role", DEFAULT_ROLE_KEY), allowed=allowed_roles, default=DEFAULT_ROLE_KEY)
	allowed_external = {item["key"] for item in EXTERNAL_ID_TABS}
	default_external = DEFAULT_EXTERNAL_KEY
	if role_key == "studio":
		allowed_external = {"homepage"}
		default_external = "homepage"
	external_key = _normalize_key(
		request.GET.get("external", default_external),
		allowed=allowed_external,
		default=default_external,
	)

	external_tab = next(item for item in EXTERNAL_ID_TABS if item["key"] == external_key)
	role_tab = next(item for item in ROLE_TABS if item["key"] == role_key)
	people: list[dict[str, object]] = []
	role_tabs: list[dict[str, object]] = []
	external_tabs: list[dict[str, object]] = []

	if role_key == "studio":
		follows_qs = (
			CompanyFollow.objects.select_related("company")
			.defer("company__tmdb_raw")
			.filter(user=request.user)
			.order_by("company__name")
		)
		studio_people: list[dict[str, object]] = []
		for follow in follows_qs:
			company = follow.company
			homepage = get_company_homepage(company)
			if not homepage:
				continue
			studio_people.append(
				{
					"entity_type": "company",
					"name": company.name,
					"image_path": company.logo_path,
					"is_deceased": False,
					"follow_role": "Studio",
					"external_value": homepage,
					"external_display": _external_display_value(external_tab, homepage),
					"external_url": _external_url(external_tab, homepage),
				}
			)
		people = sorted(studio_people, key=lambda item: str(item["name"]).lower())
		for tab in ROLE_TABS:
			role_tabs.append(
				{
					**tab,
					"active": tab["key"] == role_key,
					"url": _build_tab_url(tab["key"], external_key),
				}
			)
		external_tabs.append(
			{
				**external_tab,
				"active": True,
				"url": _build_tab_url(role_key, external_tab["key"]),
			}
		)
	else:
		follows_qs = (
			PersonFollow.objects.select_related("person")
			.filter(user=request.user)
			.order_by("person__name", "role")
		)
		people_with_roles: list[dict[str, object]] = []
		for follow in follows_qs:
			person = follow.person
			primary_role = _primary_role_category(follow.role)
			value = _external_id_value(person, external_tab["field"])
			people_with_roles.append(
				{
					"entity_type": "person",
					"person": person,
					"name": person.name,
					"image_path": person.profile_path,
					"is_deceased": bool(get_person_deathday(person)),
					"follow_role": follow.role,
					"primary_role": primary_role,
					"external_value": value,
					"external_display": _external_display_value(external_tab, value),
					"external_url": _external_url(external_tab, value),
				}
			)
		people = sorted(
			[item for item in people_with_roles if item["primary_role"] == role_key and item["external_value"]],
			key=lambda item: str(item["name"]).lower(),
		)

		for tab in ROLE_TABS:
			role_tabs.append(
				{
					**tab,
					"active": tab["key"] == role_key,
					"url": _build_tab_url(tab["key"], external_key),
				}
			)

		for tab in EXTERNAL_ID_TABS:
			external_tabs.append(
				{
					**tab,
					"active": tab["key"] == external_key,
					"url": _build_tab_url(role_key, tab["key"]),
				}
			)

	context = {
		"role_tabs": role_tabs,
		"external_tabs": external_tabs,
		"people": people,
		"people_count": len(people),
		"entity_label": "companies" if role_key == "studio" else "people",
		"active_role": role_tab,
		"active_external": external_tab,
	}

	if request.GET.get("partial") == "1":
		return JsonResponse({"ok": True, "html": render_to_string("catalog/_connect_section.html", context, request=request)})

	return render(
		request,
		"catalog/connect.html",
		context,
	)
