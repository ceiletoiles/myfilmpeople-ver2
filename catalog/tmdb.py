from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache


TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"


class TMDbError(RuntimeError):
    pass


@dataclass(frozen=True)
class TMDbClient:
    api_key: str
    read_access_token: str = ""
    language: str = "en-US"
    region: str = ""
    cors_proxies: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls) -> "TMDbClient":
        return cls(
            api_key=getattr(settings, "TMDB_API_KEY", ""),
            read_access_token=getattr(settings, "TMDB_API_READ_ACCESS_TOKEN", ""),
            language=getattr(settings, "TMDB_LANGUAGE", "en-US"),
            region=getattr(settings, "TMDB_REGION", ""),
            cors_proxies=tuple(getattr(settings, "CORS_PROXIES", []) or []),
        )

    def _headers(self) -> dict[str, str]:
        if self.read_access_token:
            return {"Authorization": f"Bearer {self.read_access_token}"}
        return {}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key and not self.read_access_token:
            raise TMDbError(
                "TMDb API key missing. Set TMDB_API_KEY (or TMDB_API_READ_ACCESS_TOKEN) in .env."
            )

        merged_params: dict[str, Any] = {
            "language": self.language,
        }
        if params:
            merged_params.update(params)
        if self.region:
            merged_params.setdefault("region", self.region)
        if self.api_key:
            merged_params.setdefault("api_key", self.api_key)

        url = f"{TMDB_API_BASE_URL}{path}"
        headers = self._headers()

        direct_timeout_seconds = float(getattr(settings, "TMDB_TIMEOUT_SECONDS", 15) or 15)
        proxy_timeout_seconds = float(getattr(settings, "TMDB_PROXY_TIMEOUT_SECONDS", 20) or 20)

        # Cache successful TMDb JSON responses in Django cache (Redis).
        # This avoids repeated external API calls (and rate limits) across users.
        cache_key: str | None = None
        cache_ttl_seconds = int(getattr(settings, "TMDB_CACHE_TTL_HOURS", 168) or 168) * 60 * 60
        try:
            cache_input = {
                "path": path,
                "params": merged_params,
                # Include auth *type* so cache is isolated if you switch modes.
                "auth": "bearer" if bool(self.read_access_token) else "api_key",
                # Include a hashed fingerprint of the credential without storing it.
                "auth_fp": hashlib.sha256(
                    ((self.read_access_token or self.api_key) or "").encode("utf-8")
                ).hexdigest(),
            }
            cache_key = "tmdb:http:v1:" + hashlib.sha256(
                json.dumps(cache_input, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
            ).hexdigest()
            cached = cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        except Exception:
            # If cache is unavailable/misconfigured, continue without caching.
            cache_key = None

        def should_try_proxies(status_code: int) -> bool:
            # Don't mask auth/validation errors.
            if status_code in {400, 401, 404}:
                return False
            # Common cases when blocked or upstream is failing.
            return status_code in {403, 429, 451} or status_code >= 500

        def build_full_url() -> str:
            req = requests.Request("GET", url, params=merged_params).prepare()
            return str(req.url)

        def apply_proxy(proxy_base: str, target_url: str) -> str:
            # Heuristics based on common proxy styles:
            # - allorigins: ...raw?url=<ENCODED_URL>
            # - corsproxy.io: .../?<ENCODED_URL>
            # - cors-anywhere: .../<RAW_URL>
            proxy_base = (proxy_base or "").strip()
            if not proxy_base:
                return target_url

            needs_encoding = (
                "raw?url=" in proxy_base
                or proxy_base.endswith("?")
                or proxy_base.endswith("=")
            )
            if needs_encoding:
                return f"{proxy_base}{quote(target_url, safe='')}"
            return f"{proxy_base}{target_url}"

        # 1) Try direct TMDb first
        try:
            resp = requests.get(
                url,
                params=merged_params,
                headers=headers,
                timeout=direct_timeout_seconds,
            )
            if resp.status_code < 400:
                try:
                    payload = resp.json()
                    if cache_key and isinstance(payload, dict):
                        try:
                            cache.set(cache_key, payload, timeout=cache_ttl_seconds)
                        except Exception:
                            pass
                    return payload
                except ValueError as exc:
                    # Some network blocks return HTML with HTTP 200.
                    if not self.cors_proxies:
                        raise TMDbError(
                            "TMDb response was not JSON (possibly blocked). "
                            "Configure CORS_PROXIES or an alternate network route."
                        ) from exc
                    # Fall through to proxy attempts.
            if not (self.cors_proxies and should_try_proxies(resp.status_code)):
                raise TMDbError(f"TMDb error {resp.status_code}: {resp.text}")
        except requests.RequestException as exc:
            if not self.cors_proxies:
                raise TMDbError(f"TMDb request failed: {exc}") from exc

        # 2) Fallback through proxies (works reliably only when using api_key, not bearer token)
        if not self.api_key and self.read_access_token:
            raise TMDbError(
                "TMDb proxy fallback requires TMDB_API_KEY (query param). "
                "Bearer token auth (TMDB_API_READ_ACCESS_TOKEN) cannot be forwarded by these proxies."
            )

        full_url = build_full_url()
        last_error: Exception | None = None
        for proxy in self.cors_proxies:
            proxied_url = apply_proxy(proxy, full_url)
            try:
                proxied_resp = requests.get(proxied_url, timeout=proxy_timeout_seconds)
                if proxied_resp.status_code >= 400:
                    last_error = TMDbError(
                        f"Proxy {proxy} returned {proxied_resp.status_code}: {proxied_resp.text}"
                    )
                    continue
                try:
                    payload = proxied_resp.json()
                    if cache_key and isinstance(payload, dict):
                        try:
                            cache.set(cache_key, payload, timeout=cache_ttl_seconds)
                        except Exception:
                            pass
                    return payload
                except ValueError as exc:
                    last_error = exc
                    continue
            except requests.RequestException as exc:
                last_error = exc
                continue

        raise TMDbError(f"TMDb request failed via all proxies. Last error: {last_error}")

    def cache_key_for(self, path: str, params: dict[str, Any] | None = None) -> str:
        """Compute the Django cache key for a given TMDb path and params.

        This replicates the cache key construction used in `_get` so callers
        can invalidate cached TMDb HTTP responses.
        """
        merged_params: dict[str, Any] = {"language": self.language}
        if params:
            merged_params.update(params)
        if self.region:
            merged_params.setdefault("region", self.region)
        if self.api_key:
            merged_params.setdefault("api_key", self.api_key)

        cache_input = {
            "path": path,
            "params": merged_params,
            "auth": "bearer" if bool(self.read_access_token) else "api_key",
            "auth_fp": hashlib.sha256(((self.read_access_token or self.api_key) or "").encode("utf-8")).hexdigest(),
        }
        return "tmdb:http:v1:" + hashlib.sha256(
            json.dumps(cache_input, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()

    # Search
    def search_people(self, query: str, *, page: int = 1) -> dict[str, Any]:
        return self._get("/search/person", params={"query": query, "page": page, "include_adult": False})

    def search_companies(self, query: str, *, page: int = 1) -> dict[str, Any]:
        return self._get("/search/company", params={"query": query, "page": page})

    def search_movies(self, query: str, *, page: int = 1) -> dict[str, Any]:
        return self._get("/search/movie", params={"query": query, "page": page, "include_adult": False})

    # Details
    def get_person(self, person_id: int) -> dict[str, Any]:
        return self._get(f"/person/{person_id}")

    def get_person_credits(self, person_id: int) -> dict[str, Any]:
        return self._get(f"/person/{person_id}/combined_credits")

    def get_person_images(self, person_id: int) -> dict[str, Any]:
        return self._get(f"/person/{person_id}/images")

    def get_company(self, company_id: int) -> dict[str, Any]:
        return self._get(f"/company/{company_id}")

    def get_company_movies(self, company_id: int, *, page: int = 1) -> dict[str, Any]:
        return self._get(f"/company/{company_id}/movies", params={"page": page})

    def get_collection(self, collection_id: int) -> dict[str, Any]:
        return self._get(f"/collection/{collection_id}")

    def get_movie(self, movie_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}")

    def get_movie_credits(self, movie_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/credits")

    def get_movie_release_dates(self, movie_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/release_dates")

    def get_movie_images(self, movie_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/images")

    def get_movie_watch_providers(self, movie_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/watch/providers")

    def get_movie_recommendations(self, movie_id: int, *, page: int = 1) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/recommendations", params={"page": page})

    def get_movie_similar(self, movie_id: int, *, page: int = 1) -> dict[str, Any]:
        return self._get(f"/movie/{movie_id}/similar", params={"page": page})

    # Discovery
    def discover_movies_by_company(
        self,
        company_id: int,
        *,
        page: int = 1,
        sort_by: str = "primary_release_date.desc",
        include_adult: bool = False,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "with_companies": str(company_id),
            "page": page,
            "sort_by": sort_by,
            "include_adult": include_adult,
        }
        if extra_params:
            params.update(extra_params)
        return self._get("/discover/movie", params=params)


def tmdb_image_url(path: str, *, size: str) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"
