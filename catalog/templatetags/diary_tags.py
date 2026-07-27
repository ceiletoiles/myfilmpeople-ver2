"""Template tags for diary display formatting."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_STAR_PATH = "M12 2.25 14.92 8.17 21.45 9.12 16.73 13.72 17.84 20.22 12 17.16 6.16 20.22 7.27 13.72 2.55 9.12 9.08 8.17 12 2.25Z"


def _coerce_rating(value) -> Decimal | None:
	if value in (None, ""):
		return None
	try:
		rating = Decimal(str(value))
	except (InvalidOperation, ValueError, TypeError):
		return None
	if rating < 0:
		return Decimal("0")
	if rating > 5:
		return Decimal("5")
	return rating


@register.simple_tag
def diary_rating_stars(value):
	"""Render a compact SVG rating with half-star support."""
	rating = _coerce_rating(value)
	if rating is None:
		return ""

	full_stars = int(rating)
	has_half = (rating - Decimal(full_stars)) >= Decimal("0.5")
	display_rating = str(int(rating)) if rating == rating.to_integral() else str(rating.normalize())
	aria_label = f"{display_rating} out of 5 stars"
	stars = ""
	for _ in range(full_stars):
		stars += (
			f'<svg class="diary-star is-filled" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
			f'<path class="diary-star-base" d="{_STAR_PATH}"></path>'
			f'<path class="diary-star-fill" d="{_STAR_PATH}"></path>'
			f'</svg>'
		)
	if has_half:
		stars += (
			f'<svg class="diary-star is-half" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
			f'<path class="diary-star-base" d="{_STAR_PATH}"></path>'
			f'<path class="diary-star-fill" d="{_STAR_PATH}"></path>'
			f'</svg>'
		)
	return mark_safe(f'<span class="diary-rating-stars" aria-label="{aria_label}">{stars}</span>')
