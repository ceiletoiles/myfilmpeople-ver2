from __future__ import annotations

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
			("youtube_id", "YouTube", "https://www.youtube.com/channel/"),
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