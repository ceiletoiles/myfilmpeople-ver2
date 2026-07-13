from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
	Company,
	CompanyFollow,
	DiaryAccount,
	DiaryEntry,
	FollowActivity,
	Movie,
	NewMovieArrival,
	NewsletterIssue,
	NewsletterItem,
	NewsletterItemSeen,
	Person,
	PersonFollow,
)
from .newsletter import parse_issue, publish_issue


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
	search_fields = ("name", "tmdb_id")
	list_display = ("tmdb_id", "name", "tmdb_last_sync_at", "tmdb_last_sync_source")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
	search_fields = ("name", "tmdb_id")
	list_display = ("tmdb_id", "name", "tmdb_last_sync_at", "tmdb_last_sync_source")


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
	search_fields = ("title", "tmdb_id")
	list_display = ("tmdb_id", "title", "release_date", "tmdb_last_sync_at")


@admin.register(DiaryAccount)
class DiaryAccountAdmin(admin.ModelAdmin):
	list_display = ("user", "letterboxd_username", "last_successful_sync_at", "newest_processed_guid")
	search_fields = ("user__username", "letterboxd_username", "newest_processed_guid")


@admin.register(DiaryEntry)
class DiaryEntryAdmin(admin.ModelAdmin):
	list_display = (
		"user",
		"watched_date",
		"original_title",
		"original_release_year",
		"match_source",
		"manual_lock",
		"tmdb_id",
	)
	list_filter = ("match_source", "manual_lock", "liked", "rewatch", "watched_date")
	search_fields = ("user__username", "original_title", "official_title", "rss_guid")
	raw_id_fields = ("user",)


@admin.register(PersonFollow)
class PersonFollowAdmin(admin.ModelAdmin):
	list_display = ("user", "person_link", "name", "role", "created_at")
	list_filter = ("role",)
	search_fields = ("user__username", "name", "person__name")

	def person_link(self, obj):
		person = getattr(obj, 'person', None)
		if not person:
			return '-'
		url = reverse('admin:catalog_person_change', args=[person.id])
		return format_html('<a href="{}">{}</a>', url, person.name)
	person_link.short_description = 'Person'


@admin.register(CompanyFollow)
class CompanyFollowAdmin(admin.ModelAdmin):
	list_display = ("user", "company_link", "name", "created_at")
	search_fields = ("user__username", "name", "company__name")

	def company_link(self, obj):
		company = getattr(obj, 'company', None)
		if not company:
			return '-'
		url = reverse('admin:catalog_company_change', args=[company.id])
		return format_html('<a href="{}">{}</a>', url, company.name)
	company_link.short_description = 'Company'


@admin.register(FollowActivity)
class FollowActivityAdmin(admin.ModelAdmin):
	list_display = ("user", "action", "entity_type", "entity_name", "role", "created_at")
	list_filter = ("action", "entity_type", "created_at")
	search_fields = ("user__username", "entity_name", "role")


@admin.register(NewMovieArrival)
class NewMovieArrivalAdmin(admin.ModelAdmin):
	list_display = (
		"user",
		"movie",
		"event_type",
		"source_type",
		"source_name",
		"role",
		"is_seen",
		"created_at",
		"seen_at",
	)
	list_filter = ("event_type", "source_type", "role", "is_seen")
	search_fields = ("user__username", "movie__title", "source_name", "role")
	raw_id_fields = ("user", "movie")


@admin.action(description="Parse selected newsletter issues")
def parse_selected_newsletter_issues(modeladmin, request, queryset):
	total_items = 0
	for issue in queryset:
		total_items += parse_issue(issue)
	modeladmin.message_user(
		request,
		f"Parsed {queryset.count()} issue(s); created {total_items} item(s).",
	)


@admin.action(description="Publish selected newsletter issues")
def publish_selected_newsletter_issues(modeladmin, request, queryset):
	published = 0
	for issue in queryset:
		if publish_issue(issue):
			published += 1
	modeladmin.message_user(
		request,
		f"Published {published} of {queryset.count()} selected issue(s).",
	)


class NewsletterItemInline(admin.TabularInline):
	model = NewsletterItem
	extra = 0
	fields = ("position", "text", "normalized_text", "created_at")
	readonly_fields = ("normalized_text", "created_at")
	ordering = ("position", "id")
	show_change_link = False


@admin.register(NewsletterIssue)
class NewsletterIssueAdmin(admin.ModelAdmin):
	list_display = (
		"provider_name",
		"issue_date",
		"subject",
		"status",
		"item_count",
		"parsed_at",
		"published_at",
	)
	list_filter = ("provider_name", "status", "issue_date")
	search_fields = ("provider_name", "subject", "raw_text", "source_hash")
	readonly_fields = ("source_hash", "parsed_at", "published_at", "created_at", "updated_at")
	actions = (parse_selected_newsletter_issues, publish_selected_newsletter_issues)
	inlines = (NewsletterItemInline,)

	def item_count(self, obj):
		return obj.items.count()


@admin.register(NewsletterItem)
class NewsletterItemAdmin(admin.ModelAdmin):
	list_display = ("issue", "position", "text_preview", "created_at")
	list_filter = ("issue__provider_name", "issue__issue_date")
	search_fields = ("text", "normalized_text", "issue__subject")
	readonly_fields = ("normalized_text", "created_at")

	def text_preview(self, obj):
		text = (obj.text or "").strip()
		return text if len(text) <= 100 else f"{text[:97]}..."


@admin.register(NewsletterItemSeen)
class NewsletterItemSeenAdmin(admin.ModelAdmin):
	list_display = ("user", "item", "seen_at")
	list_filter = ("seen_at", "item__issue__provider_name")
	search_fields = ("user__username", "item__text")
