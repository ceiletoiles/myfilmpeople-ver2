from __future__ import annotations

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.now_showing_sync import sync_now_showing


class Command(BaseCommand):
	help = "Import the latest movie-only BookYourShow snapshot."

	def add_arguments(self, parser):
		parser.add_argument("--city", default=getattr(settings, "BOOKYOURSHOW_CITY", "Bengaluru"))
		parser.add_argument("--source-url", default=getattr(settings, "BOOKYOURSHOW_SYNC_URL", ""))
		parser.add_argument("--sync-key", default=os.getenv("BOOKYOURSHOW_SYNC_KEY", ""))

	def handle(self, *args, **options):
		try:
			result = sync_now_showing(
				city=options["city"].strip(),
				source_url=options["source_url"].strip(),
				sync_key=options["sync_key"],
			)
		except (ValueError, RuntimeError) as exc:
			raise CommandError(str(exc)) from exc

		self.stdout.write(self.style.SUCCESS(
			f"Imported {result['imported']} movies for {options['city'].strip()}; "
			f"TMDb matched: {result['tmdb_matched']}; posters saved: {result['posters_saved']}"
		))
