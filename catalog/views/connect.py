from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from ..models import PersonFollow


ROLE_TABS = [
	{"key": "director", "label": "Director"},
	{"key": "actor", "label": "Actor"},
	{"key": "crew", "label": "Crew"},
]

EXTERNAL_ID_TABS = [
	{"key": "instagram", "label": "Instagram", "field": "instagram_id", "url_prefix": "https://www.instagram.com/"},
	{"key": "x", "label": "X", "field": "twitter_id", "url_prefix": "https://x.com/"},
	{"key": "facebook", "label": "Facebook", "field": "facebook_id", "url_prefix": "https://www.facebook.com/"},
	{"key": "youtube", "label": "Youtube", "field": "youtube_id", "url_prefix": "https://www.youtube.com/channel/"},
	{"key": "wikidata", "label": "Wikidata", "field": "wikidata_id", "url_prefix": "https://www.wikidata.org/wiki/"},
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
	external_ids = raw.get("external_ids")
	if not isinstance(external_ids, dict):
		return ""
	return str(external_ids.get(field_name) or "").strip()


def _external_display_value(tab: dict[str, str], value: str) -> str:
	if not value:
		return ""
	if tab["key"] in {"instagram", "x", "twitter"}:
		return f"@{value}"
	return value


def _external_url(tab: dict[str, str], value: str) -> str:
	if not value:
		return ""
	return f"{tab['url_prefix']}{value}"


def _build_tab_url(role_key: str, external_key: str) -> str:
	query = urlencode({"role": role_key, "external": external_key})
	return f"{reverse('connect')}?{query}"


@login_required
def connect(request: HttpRequest) -> HttpResponse:
	allowed_roles = {item["key"] for item in ROLE_TABS}
	allowed_external = {item["key"] for item in EXTERNAL_ID_TABS}
	role_key = _normalize_key(request.GET.get("role", DEFAULT_ROLE_KEY), allowed=allowed_roles, default=DEFAULT_ROLE_KEY)
	external_key = _normalize_key(
		request.GET.get("external", DEFAULT_EXTERNAL_KEY),
		allowed=allowed_external,
		default=DEFAULT_EXTERNAL_KEY,
	)

	external_tab = next(item for item in EXTERNAL_ID_TABS if item["key"] == external_key)
	role_tab = next(item for item in ROLE_TABS if item["key"] == role_key)

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
				"person": person,
				"follow_role": follow.role,
				"primary_role": primary_role,
				"external_value": value,
				"external_display": _external_display_value(external_tab, value),
				"external_url": _external_url(external_tab, value),
			}
		)
	all_people: list[dict[str, object]] = []
	for item in people_with_roles:
		if item["primary_role"] != role_key:
			continue
		if not item["external_value"]:
			continue
		all_people.append(
			item
		)

	people = sorted(all_people, key=lambda item: str(item["person"].name).lower())

	role_tabs: list[dict[str, object]] = []
	for tab in ROLE_TABS:
		count = sum(1 for item in people_with_roles if item["primary_role"] == tab["key"] and item["external_value"])
		role_tabs.append(
			{
				**tab,
				"count": count,
				"active": tab["key"] == role_key,
				"url": _build_tab_url(tab["key"], external_key),
			}
		)

	external_tabs: list[dict[str, object]] = []
	for tab in EXTERNAL_ID_TABS:
		count = sum(1 for item in people_with_roles if item["primary_role"] == role_key and _external_id_value(item["person"], tab["field"]))
		external_tabs.append(
			{
				**tab,
				"count": count,
				"active": tab["key"] == external_key,
				"url": _build_tab_url(role_key, tab["key"]),
			}
		)

	return render(
		request,
		"catalog/connect.html",
		{
			"role_tabs": role_tabs,
			"external_tabs": external_tabs,
			"people": people,
			"people_count": len(people),
			"active_role": role_tab,
			"active_external": external_tab,
		},
	)