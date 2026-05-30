from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import Movie


class Command(BaseCommand):
    help = "Delete movies that have not been accessed recently."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "MOVIE_STALE_DELETE_DAYS", 5) or 5),
            help="Delete movies not accessed in this many days (default from MOVIE_STALE_DELETE_DAYS or 5).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many movies would be deleted without deleting them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days = int(options.get("days") or 5)
        if days < 1:
            days = 1

        cutoff = timezone.now() - timedelta(days=days)
        stale_qs = Movie.objects.filter(last_accessed_at__lt=cutoff)
        stale_count = stale_qs.count()

        if options.get("dry_run"):
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would delete {stale_count} movies not accessed since {cutoff.isoformat()}."
                )
            )
            return

        deleted_rows, _details = stale_qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {stale_count} stale movies (rows removed including cascades: {deleted_rows})."
            )
        )
