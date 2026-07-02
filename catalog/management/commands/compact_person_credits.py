from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from catalog.models import Person
from catalog.services import compact_person_credits_payload


class Command(BaseCommand):
	help = "Compact stored person credits to id/title/release_date/popularity/media_type/paths only."

	def add_arguments(self, parser) -> None:
		parser.add_argument(
			"--person",
			type=int,
			default=None,
			help="Optional TMDb person id to compact. If omitted, all people are processed.",
		)

	def handle(self, *args: Any, **options: Any) -> None:
		person_tmdb_id = options.get("person")
		queryset = Person.objects.all().order_by("id")
		if person_tmdb_id is not None:
			queryset = queryset.filter(tmdb_id=int(person_tmdb_id))

		updated = 0
		for person in queryset.iterator():
			if not isinstance(person.tmdb_credits_raw, dict):
				continue
			compact = compact_person_credits_payload(person.tmdb_credits_raw)
			if compact == person.tmdb_credits_raw:
				continue
			person.tmdb_credits_raw = compact
			person.save(update_fields=["tmdb_credits_raw", "updated_at"])
			updated += 1

		self.stdout.write(self.style.SUCCESS(f"Compacted {updated} person record(s)."))
