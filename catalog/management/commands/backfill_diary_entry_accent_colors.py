from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import models

from catalog.movie_accent import DEFAULT_MOVIE_ACCENT_COLOR, build_movie_accent_color
from catalog.models import DiaryEntry


class Command(BaseCommand):
	help = "Backfill DiaryEntry.accent_color from poster images for rows that still lack an accent."

	def add_arguments(self, parser):
		parser.add_argument(
			"--force",
			action="store_true",
			help="Recompute accent colors for all poster-backed diary entries, not just empty rows.",
		)

	def handle(self, *args, **options):
		force = bool(options.get("force"))
		queryset = DiaryEntry.objects.filter(poster_path__isnull=False).exclude(poster_path="")
		if not force:
			queryset = queryset.filter(models.Q(accent_color__isnull=True) | models.Q(accent_color=""))

		updated = 0
		failed = 0

		for entry in queryset.only("id", "poster_path", "accent_color").iterator():
			try:
				accent = build_movie_accent_color(entry.poster_path, fallback=entry.accent_color or DEFAULT_MOVIE_ACCENT_COLOR)
			except Exception:
				failed += 1
				continue

			accent = str(accent or "").strip() or DEFAULT_MOVIE_ACCENT_COLOR
			if accent == entry.accent_color:
				continue

			DiaryEntry.objects.filter(pk=entry.pk).update(accent_color=accent)
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Updated {updated} diary entry accent colors. Failed: {failed}."))
