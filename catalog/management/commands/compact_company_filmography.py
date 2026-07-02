from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import Company
from catalog.services import compact_company_filmography_pages


class Command(BaseCommand):
	help = "Compact stored company filmography pages to id/title/year only."

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

		updated = 0
		for company in queryset.iterator():
			if not isinstance(company.tmdb_raw, dict):
				continue
			raw = company.tmdb_raw
			pages = raw.get("discover_movies_pages")
			if not isinstance(pages, dict) or not pages:
				continue

			compact_pages = compact_company_filmography_pages(pages)
			if compact_pages == pages:
				continue

			company.tmdb_raw = {**raw, "discover_movies_pages": compact_pages}
			company.save(update_fields=["tmdb_raw", "updated_at"])
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Compacted {updated} company record(s)."))