from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import NewsletterIssue
from catalog.newsletter import parse_issue, publish_issue


class Command(BaseCommand):
	help = "Publish due The Dailies newsletter issues (Mon/Wed/Fri scheduler target)."

	def add_arguments(self, parser) -> None:
		parser.add_argument(
			"--provider",
			default="The Dailies",
			help="Newsletter provider name to publish (default: The Dailies).",
		)

	def handle(self, *args: Any, **options: Any) -> None:
		provider = str(options.get("provider") or "The Dailies").strip() or "The Dailies"
		today = timezone.localdate()

		due_qs = NewsletterIssue.objects.filter(
			provider_name=provider,
			published_at__isnull=True,
			issue_date__lte=today,
		).order_by("issue_date", "id")

		total = due_qs.count()
		parsed_count = 0
		published_count = 0
		for issue in due_qs:
			if issue.status == NewsletterIssue.STATUS_DRAFT:
				parse_issue(issue)
				parsed_count += 1
			if publish_issue(issue):
				published_count += 1

		self.stdout.write(
			self.style.SUCCESS(
				f"Provider={provider}; due={total}; parsed={parsed_count}; published={published_count}."
			)
		)
