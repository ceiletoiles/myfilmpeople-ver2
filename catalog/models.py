import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone


class Person(models.Model):
	tmdb_id = models.PositiveIntegerField(unique=True)
	name = models.CharField(max_length=255)
	profile_path = models.CharField(max_length=255, blank=True)

	tmdb_raw = models.JSONField(default=dict, blank=True)
	tmdb_credits_raw = models.JSONField(default=dict, blank=True)
	tmdb_last_sync_at = models.DateTimeField(null=True, blank=True)
	# How the last TMDb snapshot was refreshed: 'sync' (explicit) or 'ttl' (stale refresh).
	tmdb_last_sync_source = models.CharField(max_length=20, blank=True, default="")

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self) -> str:
		return f"{self.name} ({self.tmdb_id})"


class Company(models.Model):
	tmdb_id = models.PositiveIntegerField(unique=True)
	name = models.CharField(max_length=255)
	logo_path = models.CharField(max_length=255, blank=True)

	tmdb_raw = models.JSONField(default=dict, blank=True)
	tmdb_last_sync_at = models.DateTimeField(null=True, blank=True)
	# How the last TMDb snapshot was refreshed: 'sync' (explicit) or 'ttl' (stale refresh).
	tmdb_last_sync_source = models.CharField(max_length=20, blank=True, default="")

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self) -> str:
		return f"{self.name} ({self.tmdb_id})"


class Movie(models.Model):
	tmdb_id = models.PositiveIntegerField(unique=True)
	title = models.CharField(max_length=255, db_index=True)
	release_date = models.DateField(null=True, blank=True, db_index=True)
	poster_path = models.CharField(max_length=255, blank=True)
	backdrop_path = models.CharField(max_length=255, blank=True)

	tmdb_raw = models.JSONField(default=dict, blank=True)
	tmdb_credits_raw = models.JSONField(default=dict, blank=True)
	tmdb_last_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self) -> str:
		return f"{self.title} ({self.tmdb_id})"

	class Meta:
		indexes = [
			models.Index(fields=["title", "tmdb_id"]),
			models.Index(fields=["release_date", "tmdb_id"]),
			models.Index(fields=["tmdb_last_sync_at", "tmdb_id"]),
		]


class PersonFollow(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	person = models.ForeignKey(Person, on_delete=models.CASCADE)
	# Denormalized snapshot for easier querying without joins.
	name = models.CharField(max_length=255, blank=True)
	# Free-text role (e.g. "Director", "Actor", "Producer", "Writer").
	role = models.CharField(max_length=100)
	notes = models.TextField(blank=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "person", "role"],
				name="uniq_follow_user_person_role",
			)
		]

	def __str__(self) -> str:
		return f"{self.user_id} follows {self.person_id} as {self.role}"


class CompanyFollow(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	company = models.ForeignKey(Company, on_delete=models.CASCADE)
	# Denormalized snapshot for easier querying without joins.
	name = models.CharField(max_length=255, blank=True)
	notes = models.TextField(blank=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "company"],
				name="uniq_follow_user_company",
			)
		]

	def __str__(self) -> str:
		return f"{self.user_id} follows company {self.company_id}"


class NewMovieArrival(models.Model):
	"""Track newly discovered movies from synced data."""
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
	# Event type: "new" for newly discovered IDs, "update" for metadata changes (e.g. release_date updated).
	event_type = models.CharField(max_length=20, default="new")
	# Optional event metadata (e.g., old/new release_date).
	event_meta = models.JSONField(default=dict, blank=True)
	# Source: 'person' or 'company'
	source_type = models.CharField(max_length=20)
	# Related person or company ID
	source_id = models.PositiveIntegerField()
	# Person/Company name for display
	source_name = models.CharField(max_length=255, blank=True)
	# What role/relationship ('actor', 'director', 'crew', 'studio')
	role = models.CharField(max_length=100, blank=True)
	# Mark as seen/dismissed
	is_seen = models.BooleanField(default=False)
	# When the user first saw this arrival in New Arrivals.
	seen_at = models.DateTimeField(null=True, blank=True)

	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "movie", "event_type", "source_type", "source_id"],
				name="uniq_new_movie_arrival",
			)
		]
		ordering = ["-created_at"]

	def __str__(self) -> str:
		return f"New: {self.movie.title} ({self.source_name})"


class NewsletterIssue(models.Model):
	"""Raw newsletter payload for a single provider issue/date."""
	STATUS_DRAFT = "draft"
	STATUS_PARSED = "parsed"
	STATUS_PUBLISHED = "published"
	STATUS_CHOICES = [
		(STATUS_DRAFT, "Draft"),
		(STATUS_PARSED, "Parsed"),
		(STATUS_PUBLISHED, "Published"),
	]

	provider_name = models.CharField(max_length=120, default="The Dailies")
	issue_date = models.DateField()
	subject = models.CharField(max_length=255, blank=True)
	raw_text = models.TextField(blank=True)
	# Content hash used for idempotent ingestion of the same source payload.
	source_hash = models.CharField(max_length=64, unique=True)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
	parsed_at = models.DateTimeField(null=True, blank=True)
	published_at = models.DateTimeField(null=True, blank=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-issue_date", "-created_at"]

	def __str__(self) -> str:
		subj = f" - {self.subject}" if self.subject else ""
		return f"{self.provider_name} {self.issue_date.isoformat()}{subj}"

	def save(self, *args, **kwargs):
		update_fields = kwargs.get("update_fields")
		update_fields_set = set(update_fields) if update_fields is not None else None

		if not (self.source_hash or "").strip():
			raw = (self.raw_text or "").replace("\r\n", "\n").strip()
			self.source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
			if update_fields_set is not None:
				update_fields_set.add("source_hash")
		if self.status == self.STATUS_PUBLISHED and self.published_at is None:
			self.published_at = timezone.now()
			if update_fields_set is not None:
				update_fields_set.add("published_at")
		elif self.published_at is not None and self.status != self.STATUS_PUBLISHED:
			self.status = self.STATUS_PUBLISHED
			if update_fields_set is not None:
				update_fields_set.add("status")

		if update_fields_set is not None:
			kwargs["update_fields"] = update_fields_set
		super().save(*args, **kwargs)


class NewsletterItem(models.Model):
	"""Parsed text item from a newsletter issue."""
	issue = models.ForeignKey(NewsletterIssue, on_delete=models.CASCADE, related_name="items")
	position = models.PositiveIntegerField(default=1)
	text = models.TextField()
	# sha256 hash of normalized text used for de-duplication within an issue.
	normalized_text = models.CharField(max_length=64)
	# Placeholder for future entity extraction/enrichment.
	entity_hints = models.JSONField(default=dict, blank=True)

	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["issue", "normalized_text"],
				name="uniq_newsletter_item_text_per_issue",
			),
			models.UniqueConstraint(
				fields=["issue", "position"],
				name="uniq_newsletter_item_position_per_issue",
			),
		]
		ordering = ["issue", "position", "id"]

	def __str__(self) -> str:
		return f"{self.issue_id}#{self.position}: {self.text[:60]}"


class NewsletterItemSeen(models.Model):
	"""Per-user seen state for published newsletter items."""
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	item = models.ForeignKey(NewsletterItem, on_delete=models.CASCADE, related_name="seen_by")
	seen_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "item"],
				name="uniq_newsletter_item_seen",
			),
		]
		ordering = ["-seen_at"]

	def __str__(self) -> str:
		return f"{self.user_id} seen {self.item_id}"
