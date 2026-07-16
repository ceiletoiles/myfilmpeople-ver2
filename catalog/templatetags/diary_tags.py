"""Template tags for diary display formatting."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


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
	"""Render a 5-star rating with half-star support."""
	rating = _coerce_rating(value)
	if rating is None:
		return ""

	full_stars = int(rating)
	has_half = (rating - Decimal(full_stars)) >= Decimal("0.5")
	empty_stars = max(0, 5 - full_stars - (1 if has_half else 0))
	aria_label = f"{rating.normalize()} out of 5 stars"
	stars = '<span class="diary-star is-filled" aria-hidden="true">★</span>' * full_stars
	if has_half:
		stars += '<span class="diary-star is-half" aria-hidden="true">★</span>'
	stars += '<span class="diary-star is-empty" aria-hidden="true">☆</span>' * empty_stars
	return mark_safe(f'<span class="diary-rating-stars" aria-label="{aria_label}">{stars}</span>')
