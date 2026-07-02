from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import Company
from catalog.services import compact_company_filmography_pages
from catalog.tmdb import TMDbClient


class Command(BaseCommand):
	help = "Normalize stored company filmography pages to the current compact shape."

	def add_arguments(self, parser) -> None:
		parser.add_argument(
			"--company",
			type=int,
			default=None,
			help="Optional TMDb company id to compact. If omitted, all companies are processed.",
		)

	def handle(self, *args: Any, **options: Any) -> None:
		company_tmdb_id = options.get("company")
		queryset = Company.objects.all().order_by("id")
		if company_tmdb_id is not None:
			queryset = queryset.filter(tmdb_id=int(company_tmdb_id))

		client = TMDbClient.from_settings()
		updated = 0
		for company in queryset.iterator():
			if not isinstance(company.tmdb_raw, dict):
				continue
			raw = company.tmdb_raw
			pages = raw.get("discover_movies_pages")
			if not isinstance(pages, dict) or not pages:
				continue

			compact_pages = compact_company_filmography_pages(pages)
			# Legacy rows may only have `year`; promote that to a real release_date
			# so the current views can read the current DB shape consistently.
			for payload in compact_pages.values():
				if not isinstance(payload, dict):
					continue
				results = payload.get("results") or []
				if not isinstance(results, list):
					continue
				for item in results:
					if not isinstance(item, dict):
						continue
					if str(item.get("release_date") or "").strip():
						pass
					else:
						year = item.get("year")
						if isinstance(year, int) and year > 0:
							item["release_date"] = f"{year:04d}-01-01"
						elif isinstance(year, str):
							year_s = year.strip()
							if len(year_s) == 4 and year_s.isdigit():
								item["release_date"] = f"{int(year_s):04d}-01-01"
			# Backfill poster_path only for the first page. Later pages are hydrated
			# on demand from TMDb at render time, so we keep them compact in DB.
			page1 = compact_pages.get("1")
			if isinstance(page1, dict):
				page1_results = page1.get("results") or []
				if isinstance(page1_results, list):
					for item in page1_results:
						if not isinstance(item, dict):
							continue
						if str(item.get("poster_path") or "").strip():
							continue
						mid = item.get("id")
						if not isinstance(mid, int):
							continue
						try:
							movie = client.get_movie(mid)
						except Exception:
							movie = {}
						if isinstance(movie, dict):
							poster_path = str(movie.get("poster_path") or "").strip()
							if poster_path:
								item["poster_path"] = poster_path
			if compact_pages == pages:
				continue

			company.tmdb_raw = {**raw, "discover_movies_pages": compact_pages}
			company.save(update_fields=["tmdb_raw", "updated_at"])
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Compacted {updated} company record(s)."))
