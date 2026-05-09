"""Template tags for new movie arrivals."""
from __future__ import annotations

from django import template
from django.contrib.auth.models import User

register = template.Library()


@register.filter
def unseen_new_arrivals_count(user: User) -> int:
	"""Get count of unseen new movie arrivals for a user."""
	if not user or not user.is_authenticated:
		return 0
	
	from catalog.models import NewMovieArrival
	
	return NewMovieArrival.objects.filter(user=user, is_seen=False).count()
