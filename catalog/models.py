import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from .movie_accent import DEFAULT_MOVIE_ACCENT_COLOR, build_movie_accent_color


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
	last_accessed_at = models.DateTimeField(default=timezone.now, db_index=True)

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
		models.Index(fields=["last_accessed_at", "tmdb_id"]),
		]


class DiaryAccount(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="diary_account",
	)
	letterboxd_username = models.CharField(max_length=80, blank=True, default="")
	last_successful_sync_at = models.DateTimeField(null=True, blank=True)
	newest_processed_guid = models.CharField(max_length=512, blank=True, default="")

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["user_id"]

	def __str__(self) -> str:
		username = self.letterboxd_username.strip()
		if username:
			return f"{self.user_id} @{username}"
		return f"{self.user_id} Diary"

	@property
	def is_connected(self) -> bool:
		return bool(self.letterboxd_username.strip())


class DiaryEntry(models.Model):
	class MatchSource(models.TextChoices):
		AUTO = "AUTO", "Auto"
		MANUAL = "MANUAL", "Manual"

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="diary_entries")
	original_title = models.CharField(max_length=255, db_index=True)
	original_release_year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
	watched_date = models.DateField(db_index=True)
	rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
	liked = models.BooleanField(default=False)
	rewatch = models.BooleanField(default=False)
	review = models.TextField(blank=True, default="")
	rss_guid = models.CharField(max_length=512, blank=True, default="", db_index=True)

	tmdb_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
	official_title = models.CharField(max_length=255, blank=True, default="")
	poster_path = models.CharField(max_length=255, blank=True, default="")
	accent_color = models.CharField(max_length=7, blank=True, null=True)
	release_date = models.DateField(null=True, blank=True)
	match_source = models.CharField(max_length=20, choices=MatchSource.choices, default=MatchSource.AUTO)
	manual_lock = models.BooleanField(default=False)
	match_candidates = models.JSONField(default=list, blank=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-watched_date", "-created_at", "-id"]
		constraints = [
			models.UniqueConstraint(
				fields=["user", "original_title", "original_release_year", "watched_date"],
				name="uniq_diary_entry_original_watch",
			),
		]
		indexes = [
			models.Index(fields=["user", "watched_date"], name="catalog_dia_user_watched_idx"),
			models.Index(fields=["user", "rss_guid"], name="catalog_dia_user_rss_guid_idx"),
			models.Index(fields=["user", "tmdb_id"], name="catalog_dia_user_tmdb_idx"),
		]

	def __str__(self) -> str:
		year = f" ({self.original_release_year})" if self.original_release_year else ""
		return f"{self.original_title}{year} - {self.watched_date.isoformat()}"

	def save(self, *args, **kwargs):
		poster_path = str(self.poster_path or "").strip()
		current_accent = str(self.accent_color or "").strip()
		update_fields = kwargs.get("update_fields")
		update_fields_set = set(update_fields) if update_fields is not None else None

		should_refresh_accent = bool(poster_path) and (
			self.pk is None or not current_accent or current_accent == DEFAULT_MOVIE_ACCENT_COLOR
		)

		if self.pk is not None and poster_path and not should_refresh_accent and (
			update_fields_set is None or "poster_path" in update_fields_set
		):
			original = DiaryEntry.objects.filter(pk=self.pk).values("poster_path", "accent_color").first() or {}
			original_poster = str(original.get("poster_path") or "").strip()
			original_accent = str(original.get("accent_color") or "").strip()
			if original_poster != poster_path or not original_accent or original_accent == DEFAULT_MOVIE_ACCENT_COLOR:
				should_refresh_accent = True

		if should_refresh_accent:
			self.accent_color = build_movie_accent_color(poster_path, fallback=DEFAULT_MOVIE_ACCENT_COLOR)
			if update_fields_set is not None:
				update_fields_set.add("accent_color")
				kwargs["update_fields"] = list(update_fields_set)
		elif not poster_path and self.accent_color is not None:
			self.accent_color = None
			if update_fields_set is not None:
				update_fields_set.add("accent_color")
				kwargs["update_fields"] = list(update_fields_set)

		return super().save(*args, **kwargs)

	@property
	def has_tmdb_match(self) -> bool:
		return self.tmdb_id is not None

	@property
	def is_manual_match(self) -> bool:
		return self.match_source == self.MatchSource.MANUAL


class PersonFollow(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	person = models.ForeignKey(Person, on_delete=models.CASCADE)
	# Denormalized snapshot for easier querying without joins.
	name = models.CharField(max_length=255, blank=True)
	# Cached status label for display / filtering when annotated.
	status = models.CharField(max_length=20, blank=True, default="")
	# Cached status key for display / filtering when annotated.
	status_key = models.CharField(max_length=20, blank=True, default="")
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
	# Cached status label for display / filtering when annotated.
	status = models.CharField(max_length=20, blank=True, default="")
	# Cached status key for filtering / state tracking when annotated.
	status_key = models.CharField(max_length=20, blank=True, default="")
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


class FollowActivity(models.Model):
	ACTION_FOLLOW = "follow"
	ACTION_UNFOLLOW = "unfollow"
	ACTION_CHOICES = [
		(ACTION_FOLLOW, "Followed"),
		(ACTION_UNFOLLOW, "Unfollowed"),
	]

	ENTITY_PERSON = "person"
	ENTITY_COMPANY = "company"
	ENTITY_CHOICES = [
		(ENTITY_PERSON, "Person"),
		(ENTITY_COMPANY, "Company"),
	]

	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
	entity_type = models.CharField(max_length=20, choices=ENTITY_CHOICES)
	action = models.CharField(max_length=20, choices=ACTION_CHOICES)
	person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_activities")
	company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_activities")
	entity_name = models.CharField(max_length=255)
	role = models.CharField(max_length=100, blank=True)
	image_path = models.CharField(max_length=255, blank=True)

	created_at = models.DateTimeField(default=timezone.now, db_index=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at", "-id"]
		indexes = [
			models.Index(fields=["user", "created_at"], name="catalog_foll_user_c3d0f8_idx"),
		]

	def __str__(self) -> str:
		label = self.get_action_display().lower()
		if self.is_person and self.role:
			return f"{self.user_id} {label} {self.entity_name} as {self.role}"
		return f"{self.user_id} {label} {self.entity_name}"

	@property
	def is_person(self) -> bool:
		return self.entity_type == self.ENTITY_PERSON

	@property
	def is_company(self) -> bool:
		return self.entity_type == self.ENTITY_COMPANY

	@property
	def summary(self) -> str:
		prefix = self.get_action_display()
		if self.is_person and self.role:
			return f"{prefix} {self.entity_name} as {self.role}"
		return f"{prefix} {self.entity_name}"


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
