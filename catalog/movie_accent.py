from __future__ import annotations

import colorsys
import hashlib
import os
import tempfile

import requests

from .tmdb import tmdb_image_url

try:
	from colorthief import ColorThief
except Exception:  # pragma: no cover - fallback when dependency is unavailable
	ColorThief = None


DEFAULT_MOVIE_ACCENT_COLOR = "#6B7280"
_MIN_LIGHTNESS = 0.30
_MAX_LIGHTNESS = 0.74
_MIN_SATURATION = 0.22
_PALETTE_SIZE = 6


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
	try:
		r, g, b = rgb
	except Exception:
		return DEFAULT_MOVIE_ACCENT_COLOR
	return f"#{int(r):02X}{int(g):02X}{int(b):02X}"


def _normalize_rgb(rgb: tuple[int, int, int]) -> str:
	try:
		r, g, b = rgb
		r_f = max(0.0, min(255.0, float(r))) / 255.0
		g_f = max(0.0, min(255.0, float(g))) / 255.0
		b_f = max(0.0, min(255.0, float(b))) / 255.0
	except Exception:
		return DEFAULT_MOVIE_ACCENT_COLOR

	hue, lightness, saturation = colorsys.rgb_to_hls(r_f, g_f, b_f)
	if saturation < _MIN_SATURATION:
		saturation = min(1.0, _MIN_SATURATION + (saturation * 0.45))
	if lightness < _MIN_LIGHTNESS:
		lightness = _MIN_LIGHTNESS
	elif lightness > _MAX_LIGHTNESS:
		lightness = _MAX_LIGHTNESS

	normalized = colorsys.hls_to_rgb(hue, lightness, saturation)
	return _rgb_to_hex(tuple(round(channel * 255) for channel in normalized))


def _score_rgb(rgb: tuple[int, int, int]) -> float:
	try:
		r, g, b = rgb
		r_f = max(0.0, min(255.0, float(r))) / 255.0
		g_f = max(0.0, min(255.0, float(g))) / 255.0
		b_f = max(0.0, min(255.0, float(b))) / 255.0
	except Exception:
		return 0.0

	_, lightness, saturation = colorsys.rgb_to_hls(r_f, g_f, b_f)
	# Favor vivid colors that are neither too close to black nor too washed out.
	sat_score = min(1.0, max(0.0, saturation))
	light_score = 1.0 - min(1.0, abs(lightness - 0.55) / 0.55)
	return (sat_score * 0.7) + (light_score * 0.3)


def fallback_movie_accent_color(seed: str) -> str:
	seed_value = (seed or "").strip()
	if not seed_value:
		return DEFAULT_MOVIE_ACCENT_COLOR

	digest = hashlib.sha1(seed_value.encode("utf-8", errors="ignore")).digest()
	hue = digest[0] / 255.0
	saturation = 0.58 + (digest[1] / 255.0) * 0.22
	lightness = 0.38 + (digest[2] / 255.0) * 0.18
	rgb = colorsys.hls_to_rgb(hue, min(_MAX_LIGHTNESS, lightness), min(1.0, max(_MIN_SATURATION, saturation)))
	return _rgb_to_hex(tuple(round(channel * 255) for channel in rgb))


def build_movie_accent_color(poster_path: str, *, fallback: str = DEFAULT_MOVIE_ACCENT_COLOR) -> str:
	path = (poster_path or "").strip()
	if not path or ColorThief is None:
		return fallback_movie_accent_color(path) if path else fallback

	image_url = tmdb_image_url(path, size="w500")
	if not image_url:
		return fallback

	temp_path: str | None = None
	try:
		response = requests.get(image_url, timeout=10)
		response.raise_for_status()
		with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(path)[1] or ".jpg") as handle:
			handle.write(response.content)
			temp_path = handle.name
		thief = ColorThief(temp_path)
		palette = thief.get_palette(color_count=_PALETTE_SIZE, quality=1) or []
		candidates = [color for color in palette if isinstance(color, tuple) and len(color) == 3]
		if not candidates:
			color = thief.get_color(quality=1)
			if isinstance(color, tuple) and len(color) == 3:
				candidates = [color]
		if not candidates:
			return fallback

		best = max(candidates, key=_score_rgb)
		return _normalize_rgb(best)
	except Exception:
		return fallback_movie_accent_color(path) if path else fallback
	finally:
		if temp_path:
			try:
				os.unlink(temp_path)
			except OSError:
				pass
