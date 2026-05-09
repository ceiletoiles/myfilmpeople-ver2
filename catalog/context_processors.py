"""Context processors for the catalog app."""
from __future__ import annotations

from django.http import HttpRequest

from catalog.models import NewMovieArrival, NewsletterItem


def new_arrivals_context(request: HttpRequest) -> dict:
	"""Add new arrivals count to template context."""
	context = {
		'new_arrivals_count': 0,
		'movie_new_arrivals_count': 0,
		'newsletter_new_arrivals_count': 0,
	}
	
	if request.user.is_authenticated:
		movie_count = NewMovieArrival.objects.filter(
			user=request.user, is_seen=False
		).count()
		newsletter_count = NewsletterItem.objects.filter(
			issue__published_at__isnull=False,
		).exclude(
			seen_by__user=request.user,
		).count()
		context['movie_new_arrivals_count'] = movie_count
		context['newsletter_new_arrivals_count'] = newsletter_count
		context['new_arrivals_count'] = movie_count + newsletter_count
	
	return context
