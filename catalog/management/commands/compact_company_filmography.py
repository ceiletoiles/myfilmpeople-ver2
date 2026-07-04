from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import Company
from catalog.services import compact_company_filmography_payload


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

		updated = 0
		for company in queryset.iterator():
			if not isinstance(company.tmdb_raw, dict):
				continue
			raw = company.tmdb_raw
			changed = False
			merged = {**raw}
			for legacy_key in ("company_movies_pages", "company_movies_meta"):
				if legacy_key in merged:
					merged.pop(legacy_key, None)
					changed = True
			pages = raw.get("discover_movies_pages")
			if isinstance(pages, dict) and pages:
				page1 = pages.get("1")
				if isinstance(page1, dict):
					compact_page1 = compact_company_filmography_payload(page1, include_title=True)
					compact_pages = {"1": compact_page1} if compact_page1 else {}
					if compact_pages != pages:
						merged["discover_movies_pages"] = compact_pages
						changed = True
			if not changed:
				continue

			company.tmdb_raw = merged
			company.save(update_fields=["tmdb_raw", "updated_at"])
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Compacted {updated} company record(s)."))
