from __future__ import annotations

from urllib.parse import quote

import requests

from django.conf import settings
from django.http import HttpRequest, JsonResponse


def _query_params_with_api_key(request: HttpRequest, *, api_key: str) -> dict[str, object]:
	params: dict[str, object] = {}
	for key, values in request.GET.lists():
		if key == "api_key":
			continue
		if len(values) == 1:
			params[key] = values[0]
		else:
			params[key] = values

	# Optional defaults (won't override explicit user-provided query params)
	language = getattr(settings, "TMDB_LANGUAGE", "")
	if language and "language" not in params:
		params["language"] = language
	region = getattr(settings, "TMDB_REGION", "")
	if region and "region" not in params:
		params["region"] = region

	params["api_key"] = api_key
	return params


def _apply_cors_proxy(proxy_base: str, target_url: str) -> str:
	proxy_base = (proxy_base or "").strip()
	if not proxy_base:
		return target_url

	needs_encoding = "raw?url=" in proxy_base or proxy_base.endswith("?") or proxy_base.endswith("=")
	if needs_encoding:
		return f"{proxy_base}{quote(target_url, safe='')}"
	return f"{proxy_base}{target_url}"


def _json_or_error_text(resp: requests.Response) -> tuple[object | None, str | None]:
	try:
		return resp.json(), None
	except ValueError:
		return None, (resp.text or "")


def tmdb_proxy(request: HttpRequest, endpoint: str) -> JsonResponse:
	"""Proxy TMDb API responses through Django.

	Route: /tmdb/<path:endpoint>/
	Forwards GET requests to https://api.themoviedb.org/3/<endpoint>
	Appends TMDb API key and preserves query parameters.
	"""
	if request.method != "GET":
		return JsonResponse({"error": "Method not allowed"}, status=405)

	api_key = getattr(settings, "TMDB_API_KEY", "")
	if not api_key:
		return JsonResponse(
			{
				"error": "TMDb API key missing. Set TMDB_API_KEY in your environment (.env).",
			},
			status=500,
		)

	clean_endpoint = (endpoint or "").lstrip("/")
	if not clean_endpoint:
		return JsonResponse({"error": "Invalid endpoint"}, status=400)
	if "://" in clean_endpoint or clean_endpoint.startswith("//"):
		return JsonResponse({"error": "Invalid endpoint"}, status=400)
	if ".." in clean_endpoint.split("/"):
		return JsonResponse({"error": "Invalid endpoint"}, status=400)

	base_url = "https://api.themoviedb.org/3"
	target_url = f"{base_url}/{clean_endpoint}"
	params = _query_params_with_api_key(request, api_key=api_key)

	def should_try_proxies(status_code: int) -> bool:
		# Don't hide auth/validation errors.
		if status_code in {400, 401, 404}:
			return False
		# Common cases when blocked or upstream is failing.
		return status_code in {403, 429, 451} or status_code >= 500

	# 1) Direct TMDb request
	try:
		resp = requests.get(
			target_url,
			params=params,
			headers={"Accept": "application/json"},
			timeout=15,
		)
		data, text = _json_or_error_text(resp)
		if resp.status_code < 400 and data is not None:
			return JsonResponse(data, safe=isinstance(data, dict))
		if resp.status_code < 400 and data is None:
			# Some blocks return HTML with 200. If proxies are configured, try them.
			if not getattr(settings, "CORS_PROXIES", None):
				return JsonResponse(
					{"error": "Upstream response was not JSON", "tmdb_status": resp.status_code},
					status=502,
				)
		if resp.status_code >= 400 and not (
			getattr(settings, "CORS_PROXIES", None) and should_try_proxies(resp.status_code)
		):
			error_message = None
			if isinstance(data, dict):
				error_message = data.get("status_message") or data.get("message")
			if not error_message:
				error_message = (text or "TMDb error")[:500]
			return JsonResponse(
				{"error": error_message, "tmdb_status": resp.status_code},
				status=resp.status_code,
			)
	except requests.RequestException:
		resp = None

	# 2) Fallback through CORS-style proxies (if configured)
	cors_proxies = list(getattr(settings, "CORS_PROXIES", []) or [])
	if not cors_proxies:
		return JsonResponse(
			{"error": "TMDb request failed (no proxy fallback configured)."},
			status=502,
		)

	# Build full URL with query string once, then proxy the full URL.
	prepared = requests.Request("GET", target_url, params=params).prepare()
	full_url = str(prepared.url)

	last_error: str | None = None
	for proxy_base in cors_proxies:
		proxied_url = _apply_cors_proxy(proxy_base, full_url)
		try:
			proxied_resp = requests.get(
				proxied_url,
				headers={"Accept": "application/json"},
				timeout=20,
			)
			proxied_data, proxied_text = _json_or_error_text(proxied_resp)
			if proxied_resp.status_code >= 400:
				msg = None
				if isinstance(proxied_data, dict):
					msg = proxied_data.get("status_message") or proxied_data.get("message")
				if not msg:
					msg = (proxied_text or "Proxy error")[:500]
				last_error = f"proxy returned {proxied_resp.status_code}: {msg}"
				continue
			if proxied_data is None:
				last_error = "proxy returned non-JSON response"
				continue
			return JsonResponse(proxied_data, safe=isinstance(proxied_data, dict))
		except requests.RequestException as exc:
			last_error = str(exc)
			continue

	return JsonResponse(
		{
			"error": "TMDb request failed via all proxies.",
			"details": (last_error or "unknown error")[:500],
		},
		status=502,
	)
