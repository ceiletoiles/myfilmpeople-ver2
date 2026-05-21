from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .context_processors import new_arrivals_context
from .models import Movie, NewMovieArrival, NewsletterIssue, NewsletterItem, NewsletterItemSeen, Person, PersonFollow
from .related_links import build_person_related_links
from .newsletter import parse_issue, publish_issue, split_newsletter_items, upsert_issue_from_raw_text
from .new_movie_helpers import (
	build_person_comeback_event_meta,
	get_person_active_info,
	get_person_comeback_info,
	get_person_first_release_date,
	get_person_last_release_date,
	record_new_movie_arrivals,
)
from .services import get_or_sync_person


class PersonComebackHelperTests(TestCase):
	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_last_release_date_ignores_future_titles(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2016-01-01", "media_type": "movie"},
				{"id": 2, "release_date": "2030-01-01", "media_type": "movie"},
			],
			"crew": [
				{"id": 3, "release_date": "2018-05-01", "media_type": "movie"},
			],
		}

		self.assertEqual(get_person_last_release_date(credits), date(2018, 5, 1))

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_first_release_date_ignores_future_titles(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2016-01-01", "media_type": "movie"},
				{"id": 2, "release_date": "2030-01-01", "media_type": "movie"},
			],
			"crew": [
				{"id": 3, "release_date": "2018-05-01", "media_type": "movie"},
			],
		}

		self.assertEqual(get_person_first_release_date(credits), date(2016, 1, 1))

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_comeback_info_flags_long_gap(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2010-01-01", "media_type": "movie"},
			],
		}

		info = get_person_comeback_info(credits)
		self.assertIsNotNone(info)
		assert info is not None
		self.assertEqual(info["last_release_date"], date(2010, 1, 1))
		self.assertGreaterEqual(int(info["gap_days"]), 365 * 3)
		self.assertIn("year", str(info["gap_label"]))

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_comeback_info_is_followed_role_specific(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2025-01-01", "media_type": "movie", "character": "Lead"},
			],
			"crew": [
				{"id": 2, "release_date": "2010-01-01", "media_type": "movie", "job": "Director"},
			],
		}

		actor_info = get_person_comeback_info(credits, followed_role="Actor")
		director_info = get_person_comeback_info(credits, followed_role="Director")

		self.assertIsNone(actor_info)
		self.assertIsNotNone(director_info)

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_comeback_info_excludes_passive_and_self_credits(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2012-01-01", "media_type": "movie", "character": "Hero"},
				{"id": 2, "release_date": "2025-01-01", "media_type": "movie", "character": "Self"},
			],
			"crew": [
				{"id": 3, "release_date": "2012-01-01", "media_type": "movie", "job": "Writer"},
				{"id": 4, "release_date": "2025-01-01", "media_type": "movie", "job": "Original Screenplay"},
			],
		}

		actor_info = get_person_comeback_info(credits, followed_role="Actor")
		writer_info = get_person_comeback_info(credits, followed_role="Writer")

		self.assertIsNotNone(actor_info)
		self.assertIsNotNone(writer_info)
		assert actor_info is not None
		assert writer_info is not None
		self.assertEqual(actor_info["last_release_date"], date(2012, 1, 1))
		self.assertEqual(writer_info["last_release_date"], date(2012, 1, 1))

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_get_person_active_info_is_followed_role_specific(self) -> None:
		credits = {
			"cast": [
				{"id": 1, "release_date": "2010-01-01", "media_type": "movie", "character": "Lead"},
			],
			"crew": [
				{"id": 2, "release_date": "2020-01-01", "media_type": "movie", "job": "Director"},
			],
		}

		actor_info = get_person_active_info(credits, followed_role="Actor")
		director_info = get_person_active_info(credits, followed_role="Director")

		self.assertIsNotNone(actor_info)
		self.assertIsNotNone(director_info)
		assert actor_info is not None
		assert director_info is not None
		self.assertEqual(actor_info["first_release_date"], date(2010, 1, 1))
		self.assertEqual(director_info["first_release_date"], date(2020, 1, 1))

	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_build_person_comeback_event_meta_returns_meta_for_returning_movie(self) -> None:
		meta = build_person_comeback_event_meta(
			old_release_dates={1: "2014-01-01"},
			new_release_dates={99: "2021-01-01"},
			new_movie_ids={99},
		)

		self.assertIn(99, meta)
		self.assertEqual(meta[99]["kind"], "comeback")
		self.assertEqual(meta[99]["last_release_date"], "2014-01-01")
		self.assertEqual(meta[99]["new_release_date"], "2021-01-01")


class NewMovieArrivalMetadataTests(TestCase):
	@override_settings(TMDB_PERSON_COMEBACK_GAP_YEARS=3)
	def test_record_new_movie_arrivals_persists_comeback_metadata(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="gap-user", password="pw")
		movie = Movie.objects.create(tmdb_id=12345, title="Comeback Film", release_date=date(2021, 1, 1))

		created = record_new_movie_arrivals(
			user=user,
			source_type="person",
			source_id=77,
			source_name="Example Person",
			old_movie_ids=set(),
			new_movie_ids={movie.tmdb_id},
			role="actor",
			old_release_dates={1: "2014-01-01"},
			new_release_dates={movie.tmdb_id: "2021-01-01"},
			new_event_meta_by_movie={
				movie.tmdb_id: {
					"kind": "comeback",
					"last_release_date": "2014-01-01",
					"new_release_date": "2021-01-01",
					"gap_days": 2557,
					"gap_label": "7 years",
					"threshold_days": 1095,
				},
			},
		)

		self.assertEqual(created, 1)
		arrival = NewMovieArrival.objects.get(user=user, movie=movie, event_type="new")
		self.assertEqual(arrival.event_meta.get("kind"), "comeback")
		self.assertEqual(arrival.event_meta.get("gap_label"), "7 years")


class NewsletterIngestionTests(TestCase):
	def test_split_newsletter_items_strips_header_and_more_suffix(self) -> None:
		raw_text = """
Film Development

Oscar Isaac is set to star in a new Netflix Las Vegas drama. (more)

Scarlett Johansson is starring in Ari Aster's next A24 film, Scapegoat. (more)
""".strip()

		items = split_newsletter_items(raw_text)

		self.assertEqual(len(items), 2)
		self.assertFalse(items[0].lower().endswith("(more)"))
		self.assertIn("Oscar Isaac", items[0])
		self.assertIn("Scarlett Johansson", items[1])

	def test_upsert_parse_publish_and_unread_seen_flow(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="newsletter-user", password="pw")
		rf = RequestFactory()

		raw_text = """
Film Development

Brendan Fraser is going to star in the Mars survival thriller Starman. (more)

Liam Hemsworth is set to star in They Like the Dark. (more)
""".strip()

		issue_date = date(2026, 5, 8)
		issue, created = upsert_issue_from_raw_text(
			provider_name="The Dailies",
			issue_date=issue_date,
			raw_text=raw_text,
			subject="Film Development",
		)
		self.assertTrue(created)

		items_count = parse_issue(issue)
		self.assertEqual(items_count, 2)
		self.assertTrue(publish_issue(issue))
		self.assertFalse(publish_issue(issue))

		issue2, created2 = upsert_issue_from_raw_text(
			provider_name="The Dailies",
			issue_date=issue_date,
			raw_text=raw_text,
			subject="Film Development",
		)
		self.assertFalse(created2)
		self.assertEqual(issue.id, issue2.id)

		request = rf.get("/")
		request.user = user
		context = new_arrivals_context(request)
		self.assertEqual(context["newsletter_new_arrivals_count"], 2)
		self.assertEqual(context["new_arrivals_count"], 2)

		first_item = NewsletterItem.objects.filter(issue=issue).order_by("position").first()
		assert first_item is not None
		NewsletterItemSeen.objects.create(user=user, item=first_item)

		context2 = new_arrivals_context(request)
		self.assertEqual(context2["newsletter_new_arrivals_count"], 1)
		self.assertEqual(context2["new_arrivals_count"], 1)
		self.assertEqual(NewsletterIssue.objects.count(), 1)


class NewArrivalsSeenHistoryTests(TestCase):
	def test_new_arrivals_visit_sets_seen_at_for_movie_and_newsletter(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="history-user", password="pw")
		movie = Movie.objects.create(tmdb_id=501, title="History Movie", release_date=date(2026, 5, 1))

		arrival = NewMovieArrival.objects.create(
			user=user,
			movie=movie,
			event_type="new",
			source_type="person",
			source_id=99,
			source_name="Example Person",
			role="director",
			is_seen=False,
		)

		issue = NewsletterIssue.objects.create(
			provider_name="The Dailies",
			issue_date=date(2026, 5, 8),
			subject="Issue",
			raw_text="Sample",
			source_hash="histtesthash01",
			status=NewsletterIssue.STATUS_PUBLISHED,
		)
		item = NewsletterItem.objects.create(
			issue=issue,
			position=1,
			text="Sample item",
			normalized_text="histnormhash01",
		)

		client = self.client
		client.force_login(user)
		response = client.get(reverse("new_arrivals"))
		self.assertEqual(response.status_code, 200)

		arrival.refresh_from_db()
		self.assertTrue(arrival.is_seen)
		self.assertIsNotNone(arrival.seen_at)

		seen = NewsletterItemSeen.objects.filter(user=user, item=item).first()
		self.assertIsNotNone(seen)
		assert seen is not None
		self.assertIsNotNone(seen.seen_at)


class SearchPrefixTests(TestCase):
	def setUp(self) -> None:
		self.User = get_user_model()
		self.viewer = self.User.objects.create_user(username="viewer", password="pw")
		self.target = self.User.objects.create_user(username="alice", password="pw")

	@patch("catalog.views.search.TMDbClient.from_settings")
	def test_user_prefixed_search_uses_local_users_only(self, mock_tmdb) -> None:
		mock_tmdb.side_effect = AssertionError("TMDb should not be used for user-prefixed search")

		client = self.client
		client.force_login(self.viewer)
		response = client.get(reverse("search"), {"q": "u:@alice"})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["user_results"], [{"username": "alice"}])
		self.assertEqual(response.context["people_results"], [])
		self.assertEqual(response.context["company_results"], [])
		self.assertEqual(response.context["movie_results"], [])
		mock_tmdb.assert_not_called()

	@patch("catalog.views.search.TMDbClient.from_settings")
	def test_user_prefixed_search_suggest_skips_tmdb(self, mock_tmdb) -> None:
		mock_tmdb.side_effect = AssertionError("TMDb should not be used for user-prefixed suggestions")

		client = self.client
		client.force_login(self.viewer)
		response = client.get(reverse("search_suggest"), {"q": "u:@alice"})

		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertEqual(data["people"], [])
		self.assertEqual(data["companies"], [])
		self.assertEqual(data["movies"], [])
		mock_tmdb.assert_not_called()


class RelatedLinksTests(TestCase):
	def test_person_detail_exposes_related_links(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="related-user", password="pw")
		person = Person.objects.create(
			tmdb_id=99,
			name="Example Person",
			profile_path="/profile.jpg",
			tmdb_raw={
				"name": "Example Person",
				"homepage": "https://example.com",
				"imdb_id": "nm1234567",
				"external_ids": {"instagram_id": "example"},
			},
			tmdb_credits_raw={"cast": []},
			tmdb_last_sync_at=timezone.now(),
		)
		PersonFollow.objects.create(user=user, person=person, name=person.name, role="Actor")

		with patch("catalog.views.person.get_or_sync_person", return_value=person):
			client = self.client
			client.force_login(user)
			response = client.get(reverse("person_detail", args=[person.tmdb_id]))

		self.assertEqual(response.status_code, 200)
		self.assertIn("related_links", response.context)
		self.assertTrue(any(link["label"] == "TMDb" for link in response.context["related_links"]))

	def test_build_person_related_links_includes_imdb_and_socials(self) -> None:
		raw = {
			"homepage": "https://example.com",
			"imdb_id": "nm1234567",
			"external_ids": {
				"instagram_id": "example",
				"twitter_id": "example",
			},
		}

		links = build_person_related_links(7, raw)
		self.assertTrue(any(link["label"] == "TMDb" for link in links))
		self.assertTrue(any(link["label"] == "IMDb" for link in links))
		self.assertTrue(any(link["label"] == "Instagram" for link in links))

	@patch("catalog.services.TMDbClient.from_settings")
	def test_get_or_sync_person_caches_external_ids(self, mock_from_settings) -> None:
		client = mock_from_settings.return_value
		client.get_person.return_value = {
			"id": 7,
			"name": "Example Person",
			"profile_path": "/profile.jpg",
			"homepage": "https://example.com",
			"imdb_id": "nm1234567",
		}
		client.get_person_credits.return_value = {"cast": []}
		client.get_person_external_ids.return_value = {
			"instagram_id": "example",
			"twitter_id": "example",
		}

		person = get_or_sync_person(7, force=True)
		raw = person.tmdb_raw if isinstance(person.tmdb_raw, dict) else {}

		self.assertIn("external_ids", raw)
		self.assertEqual(raw["external_ids"].get("instagram_id"), "example")

		links = build_person_related_links(7, raw)
		self.assertTrue(any(link["label"] == "TMDb" for link in links))
		self.assertTrue(any(link["label"] == "Instagram" for link in links))
