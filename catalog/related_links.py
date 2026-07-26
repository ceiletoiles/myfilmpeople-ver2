from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
from typing import Any


def _clean_text(value: Any) -> str:
	return str(value or "").strip()


def _append_link(links: list[dict[str, str]], *, label: str, url: str) -> None:
	url = _clean_text(url)
	if not url:
		return
	if any(item.get("url") == url for item in links):
		return
	links.append({"label": label, "url": url})


def _letterboxd_search_url(title: str, release_year: str) -> str:
	title = _clean_text(title)
	release_year = _clean_text(release_year)
	if not title or not release_year:
		return ""
	return f"https://letterboxd.com/search/films/{quote(f'{title} {release_year}')}/"


def _year_from_release_date(release_date: str) -> str:
	release_date = _clean_text(release_date)
	if not release_date:
		return ""
	try:
		return str(datetime.strptime(release_date, "%Y-%m-%d").year)
	except ValueError:
		return release_date[:4] if len(release_date) >= 4 else ""


def _letterboxd_release_year(tmdb_raw: dict[str, Any]) -> str:
	release_dates = tmdb_raw.get("release_dates")
	if not isinstance(release_dates, dict):
		return _year_from_release_date(_clean_text(tmdb_raw.get("release_date")))

	results = release_dates.get("results")
	if not isinstance(results, list):
		return _year_from_release_date(_clean_text(tmdb_raw.get("release_date")))

	premiere_dates: list[str] = []
	all_dates: list[str] = []
	for country_release in results:
		if not isinstance(country_release, dict):
			continue
		entries = country_release.get("release_dates")
		if not isinstance(entries, list):
			continue
		for entry in entries:
			if not isinstance(entry, dict):
				continue
			try:
				release_type = int(entry.get("type") or 0)
			except (TypeError, ValueError):
				release_type = 0
			raw_release_date = _clean_text(entry.get("release_date"))
			if raw_release_date:
				release_date = raw_release_date.split("T", 1)[0]
				all_dates.append(release_date)
				if release_type == 1:
					premiere_dates.append(release_date)

	if premiere_dates:
		premiere_dates.sort()
		return _year_from_release_date(premiere_dates[0])

	if all_dates:
		all_dates.sort()
		return _year_from_release_date(all_dates[0])

	return _year_from_release_date(_clean_text(tmdb_raw.get("release_date")))


def build_person_related_links(tmdb_id: int, tmdb_raw: dict[str, Any] | None = None) -> list[dict[str, str]]:
	raw = tmdb_raw if isinstance(tmdb_raw, dict) else {}
	links: list[dict[str, str]] = []
	_append_link(links, label="TMDb", url=f"https://www.themoviedb.org/person/{int(tmdb_id)}")

	homepage = _clean_text(raw.get("homepage"))
	if homepage:
		_append_link(links, label="Homepage", url=homepage)

	imdb_id = _clean_text(raw.get("imdb_id"))
	if imdb_id:
		_append_link(links, label="IMDb", url=f"https://www.imdb.com/name/{imdb_id}/")

	external_ids = raw.get("external_ids")
	if isinstance(external_ids, dict):
		for key, label, prefix in (
			("facebook_id", "Facebook", "https://www.facebook.com/"),
			("instagram_id", "Instagram", "https://www.instagram.com/"),
			("twitter_id", "X", "https://x.com/"),
			("tiktok_id", "TikTok", "https://www.tiktok.com/@"),
			("youtube_id", "YouTube", "https://www.youtube.com/"),
		):
			value = _clean_text(external_ids.get(key))
			if value:
				_append_link(links, label=label, url=f"{prefix}{value}")

	return links


def build_company_related_links(tmdb_id: int, tmdb_raw: dict[str, Any] | None = None) -> list[dict[str, str]]:
	raw = tmdb_raw if isinstance(tmdb_raw, dict) else {}
	links: list[dict[str, str]] = []
	_append_link(links, label="TMDb", url=f"https://www.themoviedb.org/company/{int(tmdb_id)}")

	homepage = _clean_text(raw.get("homepage"))
	if homepage:
		_append_link(links, label="Homepage", url=homepage)

	imdb_id = _clean_text(raw.get("imdb_id"))
	if imdb_id:
		_append_link(links, label="IMDb", url=f"https://www.imdb.com/company/{imdb_id}/")

	return links


def build_movie_related_links(tmdb_id: int, tmdb_raw: dict[str, Any] | None = None) -> list[dict[str, str]]:
	raw = tmdb_raw if isinstance(tmdb_raw, dict) else {}
	links: list[dict[str, str]] = []
	_append_link(links, label="TMDb", url=f"https://www.themoviedb.org/movie/{int(tmdb_id)}")

	homepage = _clean_text(raw.get("homepage"))
	if homepage:
		_append_link(links, label="Homepage", url=homepage)

	imdb_id = _clean_text(raw.get("imdb_id"))
	if imdb_id:
		_append_link(links, label="IMDb", url=f"https://www.imdb.com/title/{imdb_id}/")

	letterboxd_year = _letterboxd_release_year(raw)
	if letterboxd_year:
		title = _clean_text(raw.get("title") or raw.get("original_title"))
		_append_link(links, label="Letterboxd", url=_letterboxd_search_url(title, letterboxd_year))

	# Include social/external ids if available
	external_ids = raw.get("external_ids")
	if isinstance(external_ids, dict):
		for key, label, prefix in (
			("facebook_id", "Facebook", "https://www.facebook.com/"),
			("instagram_id", "Instagram", "https://www.instagram.com/"),
			("twitter_id", "X", "https://x.com/"),
			("tiktok_id", "TikTok", "https://www.tiktok.com/@"),
			("youtube_id", "YouTube", "https://www.youtube.com/"),
		):
			value = _clean_text(external_ids.get(key))
			if value:
				_append_link(links, label=label, url=f"{prefix}{value}")

	return links
