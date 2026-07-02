from __future__ import annotations

import time
from functools import wraps

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse


def _client_bucket(request: HttpRequest) -> str:
	user = getattr(request, "user", None)
	if user is not None and getattr(user, "is_authenticated", False):
		return f"u:{getattr(user, 'pk', 0) or 0}"
	return f"ip:{request.META.get('REMOTE_ADDR', '') or 'unknown'}"


def rate_limit(
	*,
	limit: int,
	window_seconds: int = 60,
	bucket_name: str,
	json_status: int = 429,
	html_status: int = 429,
):
	"""Lightweight cache-backed rate limiter.

	Uses a sliding bucket per user/IP and endpoint name. It is intentionally
	best-effort: cache failures are treated as allow, so it won't break the app.
	"""

	limit = max(1, int(limit))
	window_seconds = max(1, int(window_seconds))

	def decorator(view_func):
		@wraps(view_func)
		def wrapped(request: HttpRequest, *args, **kwargs):
			now = int(time.time())
			window_id = now // window_seconds
			cache_key = f"rl:{bucket_name}:{_client_bucket(request)}:{window_id}"
			count = 0
			try:
				added = cache.add(cache_key, 1, timeout=window_seconds + 5)
				if not added:
					count = cache.incr(cache_key)
				else:
					count = 1
			except Exception:
				count = 0

			if count and count > limit:
				if request.headers.get("Accept", "").find("application/json") >= 0 or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
					return JsonResponse({"error": "Too many requests. Please slow down."}, status=json_status)
				return HttpResponse("Too many requests. Please slow down.", status=html_status)
			return view_func(request, *args, **kwargs)

		return wrapped

	return decorator
