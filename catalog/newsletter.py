from __future__ import annotations

import hashlib
import re

from django.db import transaction
from django.utils import timezone

from .models import NewsletterIssue, NewsletterItem

_MORE_SUFFIX_RE = re.compile(r"\s*\(\s*more\s*\)\s*\.?\s*$", flags=re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def _collapse_spaces(value: str) -> str:
	return _SPACE_RE.sub(" ", value).strip()


def normalize_item_text(value: str) -> str:
	cleaned = _MORE_SUFFIX_RE.sub("", value or "")
	cleaned = _collapse_spaces(cleaned)
	return hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()


def clean_item_text(value: str) -> str:
	cleaned = _MORE_SUFFIX_RE.sub("", value or "")
	return _collapse_spaces(cleaned)


def text_source_hash(raw_text: str) -> str:
	normalized = (raw_text or "").replace("\r\n", "\n").strip()
	return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def split_newsletter_items(raw_text: str) -> list[str]:
	text = (raw_text or "").replace("\r\n", "\n").strip()
	if not text:
		return []

	blocks = [b.strip() for b in re.split(r"\n\s*\n+", text) if b.strip()]
	if not blocks:
		return []

	# Ignore short headline-style first block such as "Film Development".
	if len(blocks) > 1:
		first = blocks[0]
		first_words = [w for w in re.split(r"\s+", first) if w]
		if len(first_words) <= 6 and not re.search(r"[.!?][\'\"”’)]?$", first):
			blocks = blocks[1:]

	items: list[str] = []
	seen_norm: set[str] = set()
	for block in blocks:
		cleaned = clean_item_text(block)
		if not cleaned:
			continue
		norm = normalize_item_text(cleaned)
		if not norm or norm in seen_norm:
			continue
		seen_norm.add(norm)
		items.append(cleaned)

	# If we ended up with only one large paragraph, try line-based fallback.
	if len(items) <= 1 and "\n" in text:
		items = []
		seen_norm.clear()
		for line in text.split("\n"):
			line = line.strip()
			if not line:
				continue
			cleaned = clean_item_text(line)
			if not cleaned:
				continue
			norm = normalize_item_text(cleaned)
			if not norm or norm in seen_norm:
				continue
			seen_norm.add(norm)
			items.append(cleaned)

	return items


def parse_issue(issue: NewsletterIssue) -> int:
	"""Parse raw_text into NewsletterItem rows for the given issue."""
	items = split_newsletter_items(issue.raw_text)
	rows: list[NewsletterItem] = []
	for idx, text in enumerate(items, start=1):
		rows.append(
			NewsletterItem(
				issue=issue,
				position=idx,
				text=text,
				normalized_text=normalize_item_text(text),
			)
		)

	with transaction.atomic():
		NewsletterItem.objects.filter(issue=issue).delete()
		if rows:
			NewsletterItem.objects.bulk_create(rows)
		issue.parsed_at = timezone.now()
		if issue.status != NewsletterIssue.STATUS_PUBLISHED:
			issue.status = NewsletterIssue.STATUS_PARSED
		issue.save(update_fields=["parsed_at", "status", "updated_at"])

	return len(rows)


def upsert_issue_from_raw_text(
	*,
	provider_name: str,
	issue_date,
	raw_text: str,
	subject: str = "",
) -> tuple[NewsletterIssue, bool]:
	"""Create or update issue by content hash (idempotent ingestion key)."""
	hash_value = text_source_hash(raw_text)
	defaults = {
		"provider_name": (provider_name or "The Dailies").strip() or "The Dailies",
		"issue_date": issue_date,
		"subject": (subject or "").strip(),
		"raw_text": (raw_text or "").strip(),
	}
	issue, created = NewsletterIssue.objects.get_or_create(source_hash=hash_value, defaults=defaults)
	if not created:
		updated = False
		for field, value in defaults.items():
			if getattr(issue, field) != value:
				setattr(issue, field, value)
				updated = True
		if updated:
			issue.save(update_fields=["provider_name", "issue_date", "subject", "raw_text", "updated_at"])
	return issue, created


def publish_issue(issue: NewsletterIssue) -> bool:
	"""Publish an issue once; returns True only when publication happened now."""
	if issue.published_at is not None:
		return False
	if issue.status == NewsletterIssue.STATUS_DRAFT:
		parse_issue(issue)
	issue.published_at = timezone.now()
	issue.status = NewsletterIssue.STATUS_PUBLISHED
	issue.save(update_fields=["published_at", "status", "updated_at"])
	return True
