from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.movie_accent import DEFAULT_MOVIE_ACCENT_COLOR, build_movie_accent_color
from catalog.models import Movie


class Command(BaseCommand):
	help = "Backfill Movie.accent_color from poster images for rows that still use the default accent."

	def add_arguments(self, parser):
		parser.add_argument(
			"--force",
			action="store_true",
			help="Recompute accent colors for all poster-backed movies, not just default-colored rows.",
		)

	def handle(self, *args, **options):
		force = bool(options.get("force"))
		queryset = Movie.objects.filter(poster_path__isnull=False).exclude(poster_path="")
		if not force:
			queryset = queryset.filter(accent_color=DEFAULT_MOVIE_ACCENT_COLOR)

		updated = 0
		failed = 0

		for movie in queryset.only("id", "poster_path", "accent_color").iterator():
			try:
				accent = build_movie_accent_color(movie.poster_path, fallback=movie.accent_color or DEFAULT_MOVIE_ACCENT_COLOR)
			except Exception:
				failed += 1
				continue

			accent = str(accent or "").strip() or DEFAULT_MOVIE_ACCENT_COLOR
			if accent == movie.accent_color:
				continue

			Movie.objects.filter(pk=movie.pk).update(accent_color=accent)
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Updated {updated} movie accent colors. Failed: {failed}."))
