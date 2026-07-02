from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .context_processors import new_arrivals_context
from .models import Company, CompanyFollow, Movie, NewMovieArrival, NewsletterIssue, NewsletterItem, NewsletterItemSeen, Person, PersonFollow
from .related_links import build_person_related_links
from .newsletter import parse_issue, publish_issue, split_newsletter_items, upsert_issue_from_raw_text
from .new_movie_helpers import (
	build_person_comeback_event_meta,
	get_person_active_info,
	get_person_comeback_info,
	get_person_first_release_date,
	get_person_last_release_date,
	filter_movie_ids_by_release_date,
	record_new_movie_arrivals,
)
from .services import get_or_sync_company, get_or_sync_person, prefetch_company_filmography
from .views.movie import _build_country_name_lookup, _build_crew_groups, _build_release_groups
from types import SimpleNamespace
from unittest.mock import Mock


class NewMovieHelpersTests(TestCase):
	def setUp(self) -> None:
		self.User = get_user_model()
		self.user = self.User.objects.create_user(username="nmh-user", password="testpass123")

	def _create_movie(self, mid: int) -> Movie:
		return Movie.objects.create(tmdb_id=mid, title=f"Test Movie {mid}")

	def test_record_new_movie_arrivals_allows_when_tmdb_empty(self) -> None:
		mid = 88888888
		movie = self._create_movie(mid)
		NewMovieArrival.objects.filter(user=self.user, movie=movie).delete()

		# TMDb returns empty changes payload
		stub = SimpleNamespace(get_movie_changes=lambda m: {})
		with patch('catalog.tmdb.TMDbClient.from_settings', return_value=stub):
			cnt = record_new_movie_arrivals(
				user=self.user,
				source_type='person',
				source_id=1,
				source_name='Test Person',
				old_movie_ids=set(),
				new_movie_ids={mid},
				role='actor',
				source_last_sync_at=None,
			)

		self.assertEqual(cnt, 1)
		self.assertEqual(NewMovieArrival.objects.filter(user=self.user, movie=movie).count(), 1)

	def test_record_new_movie_arrivals_allows_when_tmdb_raises(self) -> None:
		mid = 77777777
		movie = self._create_movie(mid)
		NewMovieArrival.objects.filter(user=self.user, movie=movie).delete()

		# TMDb client raises an exception
		bad = Mock()
		bad.get_movie_changes.side_effect = Exception("boom")
		with patch('catalog.tmdb.TMDbClient.from_settings', return_value=bad):
			cnt = record_new_movie_arrivals(
				user=self.user,
				source_type='person',
				source_id=1,
				source_name='Test Person',
				old_movie_ids=set(),
				new_movie_ids={mid},
				role='actor',
				source_last_sync_at=None,
			)

		self.assertEqual(cnt, 1)
		self.assertEqual(NewMovieArrival.objects.filter(user=self.user, movie=movie).count(), 1)

	def test_filter_movie_ids_by_release_date_keeps_recent_and_undated(self) -> None:
		movie_ids = {1, 2, 3}
		release_dates = {1: "2023-01-01", 2: "2026-05-01", 3: ""}

		filtered = filter_movie_ids_by_release_date(movie_ids, release_dates, not_before=date(2025, 1, 1))

		self.assertEqual(filtered, {2, 3})


class ConnectPageTests(TestCase):
	def setUp(self) -> None:
		self.User = get_user_model()
		self.user = self.User.objects.create_user(username="connect-user", password="testpass123")
		self.client.force_login(self.user)

	def _make_person(self, *, tmdb_id: int, name: str, roles: list[str], external_ids: dict[str, str]) -> Person:
		return Person.objects.create(
			tmdb_id=tmdb_id,
			name=name,
			profile_path="",
			tmdb_raw={"credited_roles": roles, "external_ids": external_ids},
			tmdb_credits_raw={},
		)

	def _follow_person(self, person: Person, role: str) -> None:
		PersonFollow.objects.create(user=self.user, person=person, name=person.name, role=role)

	def test_connect_page_filters_by_role_and_external_id(self) -> None:
		director = self._make_person(
			tmdb_id=1,
			name="Direct Her",
			roles=["Director", "Writer"],
			external_ids={"instagram_id": "directher", "youtube_id": "@JosephKosinski"},
		)
		actor = self._make_person(
			tmdb_id=2,
			name="Act Her",
			roles=["Actor", "Producer"],
			external_ids={"twitter_id": "acther"},
		)
		crew = self._make_person(
			tmdb_id=3,
			name="Crew Her",
			roles=["Writer"],
			external_ids={"instagram_id": "crewher"},
		)
		wiki_crew = self._make_person(
			tmdb_id=4,
			name="Wiki Crew",
			roles=["Writer", "Producer"],
			external_ids={"wikidata_id": "Q123"},
		)
		composer = self._make_person(
			tmdb_id=5,
			name="Hans Zimmer",
			roles=["Actor", "Original Music Composer"],
			external_ids={"instagram_id": "hanszimmer"},
		)
		homepage_person = Person.objects.create(
			tmdb_id=6,
			name="Homepage Person",
			profile_path="",
			tmdb_raw={"credited_roles": ["Director"], "homepage": "https://example.com"},
			tmdb_credits_raw={},
		)
		studio_company = Company.objects.create(
			tmdb_id=7,
			name="Studio People",
			logo_path="",
			tmdb_raw={"homepage": "https://studio.example"},
		)

		self._follow_person(director, "Director")
		self._follow_person(actor, "Actor")
		self._follow_person(crew, "Writer")
		self._follow_person(wiki_crew, "Producer")
		self._follow_person(composer, "Original Music Composer")
		self._follow_person(homepage_person, "Director")
		CompanyFollow.objects.create(user=self.user, company=studio_company, name=studio_company.name)

		response = self.client.get(reverse("connect"), {"role": "director", "external": "instagram"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Direct Her")
		self.assertNotContains(response, "Act Her")
		self.assertNotContains(response, "Crew Her")
		self.assertNotContains(response, "Wiki Crew")
		self.assertNotContains(response, 'class="connect-person-role"')
		self.assertContains(response, "Instagram")
		self.assertContains(response, "@directher")
		self.assertContains(response, "Director")
		self.assertNotContains(response, "Instagram: @")
		self.assertNotContains(response, "Twitter")

		response = self.client.get(reverse("connect"), {"role": "actor", "external": "twitter"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'href="https://x.com/acther"')
		self.assertContains(response, "X")
		self.assertNotContains(response, "Twitter")

		response = self.client.get(reverse("connect"), {"role": "crew", "external": "wikidata"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Wiki Crew")
		self.assertNotContains(response, "Direct Her")
		self.assertNotContains(response, "Act Her")

		response = self.client.get(reverse("connect"), {"role": "crew", "external": "instagram"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Hans Zimmer")
		self.assertContains(response, "Original Music Composer")
		self.assertContains(response, 'class="connect-person-role"')

		response = self.client.get(reverse("connect"), {"role": "director", "external": "homepage"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Homepage Person")
		self.assertContains(response, 'href="https://example.com"')
		self.assertNotContains(response, "Homepage: https://example.com")

		response = self.client.get(reverse("connect"), {"role": "director", "external": "youtube"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'href="https://www.youtube.com/@JosephKosinski"')
		self.assertContains(response, "YouTube")
		self.assertContains(response, "@JosephKosinski")
		self.assertNotContains(response, "YouTube: @")

		response = self.client.get(reverse("connect"), {"role": "studio", "external": "instagram"})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Studio People")
		self.assertContains(response, 'class="connect-studio-media"')
		self.assertContains(response, 'href="https://studio.example"')
		self.assertContains(response, "Homepage")
		self.assertContains(response, "https://studio.example")
		self.assertNotContains(response, 'width="300" height="450"')
		self.assertNotContains(response, "Homepage: ")
		self.assertNotContains(response, "Instagram")
		self.assertNotContains(response, "Facebook")
		self.assertNotContains(response, "YouTube")
		self.assertNotContains(response, "Wikidata")

		partial_response = self.client.get(reverse("connect"), {"role": "director", "external": "homepage", "partial": "1"})
		self.assertEqual(partial_response.status_code, 200)
		self.assertEqual(partial_response.json()["ok"], True)
		self.assertIn("connect-shell", partial_response.json()["html"])

	def test_home_menu_links_to_connect(self) -> None:
		response = self.client.get(reverse("home"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse("connect"))


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


class MovieCrewGroupingTests(TestCase):
	def test_build_crew_groups_places_music_jobs_after_cinematography(self) -> None:
		groups = _build_crew_groups(
			{
				"crew": [
					{"id": 1, "name": "Writer One", "job": "Writer"},
					{"id": 2, "name": "DoP One", "job": "Director of Photography"},
					{"id": 3, "name": "Composer One", "job": "Original Music Composer"},
					{"id": 4, "name": "Theme One", "job": "Theme Music Composer"},
					{"id": 5, "name": "Score One", "job": "Music"},
					{"id": 6, "name": "Song One", "job": "Songs"},
					{"id": 7, "name": "Playback One", "job": "Playback Singer"},
					{"id": 8, "name": "Voice One", "job": "Vocals"},
				],
			}
		)

		self.assertEqual([group["job"] for group in groups], ["WRITER", "CINEMATOGRAPHY", "ORIGINAL MUSIC COMPOSER"])
		self.assertEqual(groups[2]["people"][0]["name"], "Composer One")
		self.assertEqual(
			[person["name"] for person in groups[2]["people"]],
			["Composer One", "Theme One", "Score One", "Song One", "Playback One", "Voice One"],
		)


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
		user = get_user_model().objects.create_user(username="newsletter-user", password="pw")
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


class MovieReleaseCountryLookupTests(TestCase):
	def test_release_groups_use_tmdb_country_lookup(self) -> None:
		country_lookup = _build_country_name_lookup(
			[
				{"iso_3166_1": "AX", "english_name": "Aland Islands"},
			]
		)
		release_groups = _build_release_groups(
			{
				"release_dates": {
					"results": [
						{
							"iso_3166_1": "AX",
							"release_dates": [
								{
									"release_date": "2024-01-01T00:00:00.000Z",
									"type": 3,
									"certification": "PG",
									"note": "Festival premiere",
								},
							],
						}
					]
				}
			},
			country_lookup,
		)

		self.assertEqual(release_groups[0]["releases"][0]["country_name"], "Aland Islands")


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

	@patch("catalog.views.person.get_person_status_label", return_value="Upcoming")
	@patch("catalog.views.person.get_or_sync_person")
	def test_person_detail_shows_status_beside_followed_role(self, mock_get_person, _mock_status_label) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="status-user", password="pw")
		person = Person.objects.create(
			tmdb_id=100,
			name="Status Person",
			profile_path="/profile.jpg",
			tmdb_raw={"name": "Status Person"},
			tmdb_credits_raw={"cast": []},
			tmdb_last_sync_at=timezone.now(),
		)
		PersonFollow.objects.create(user=user, person=person, name=person.name, role="Actor")
		mock_get_person.return_value = person

		client = self.client
		client.force_login(user)
		response = client.get(reverse("person_detail", args=[person.tmdb_id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			'<strong>Actor</strong> <span class="person-role-status muted">| upcoming</span>',
			html=True,
		)

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
	def test_get_or_sync_company_caches_alternative_names(self, mock_from_settings) -> None:
		client = mock_from_settings.return_value
		client.get_company.return_value = {
			"id": 77,
			"name": "Example Studio",
			"logo_path": "/logo.png",
			"homepage": "https://example.com",
		}
		client.get_company_alternative_names.return_value = {
			"results": [
				{"name": "Example Studios"},
				{"name": "Example Motion Pictures"},
			],
		}

		company = get_or_sync_company(77, force=True)
		raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}

		self.assertIn("alternative_names", raw)
		self.assertEqual(len((raw["alternative_names"].get("results") or [])), 2)

	def test_company_detail_exposes_alternative_names(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="company-related-user", password="pw")
		company = Company.objects.create(
			tmdb_id=77,
			name="Example Studio",
			logo_path="/logo.png",
			tmdb_raw={
				"name": "Example Studio",
				"alternative_names": {
					"results": [{"name": "Example Studios"}, {"name": "Example Motion Pictures"}],
				},
			},
			tmdb_last_sync_at=timezone.now(),
		)
		CompanyFollow.objects.create(user=user, company=company, name=company.name)

		with patch("catalog.views.company.get_or_sync_company", return_value=company):
			client = self.client
			client.force_login(user)
			response = client.get(reverse("company_detail", args=[company.tmdb_id]))

		self.assertEqual(response.status_code, 200)
		self.assertIn("alternative_names", response.context)
		self.assertEqual(response.context["alternative_names"], ["Example Studios", "Example Motion Pictures"])

	@patch("catalog.services.TMDbClient.from_settings")
	def test_company_filmography_is_stored_compactly(self, mock_from_settings) -> None:
		client = mock_from_settings.return_value
		client.get_company.return_value = {
			"id": 194232,
			"name": "Apple Studios",
			"logo_path": "/oE7H93u8sy5vvW5EH3fpCp68vvB.png",
		}
		client.get_company_alternative_names.return_value = {}
		client.discover_movies_by_company.return_value = {
			"page": 1,
			"results": [
				{
					"id": 1280115,
					"title": "Way of the Warrior Kid",
					"release_date": "2026-11-19",
					"poster_path": "/poster.jpg",
					"overview": "Full payload that should not be stored",
				},
			],
			"total_pages": 1,
			"total_results": 1,
		}

		company = get_or_sync_company(194232, force=True)
		prefetch_company_filmography(company, force=True, max_pages=1)
		company.refresh_from_db(fields=["tmdb_raw"])
		raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		pages = raw.get("discover_movies_pages")

		self.assertEqual(raw.get("name"), "Apple Studios")
		self.assertIsInstance(pages, dict)
		self.assertEqual(
			pages["1"]["results"][0],
			{"id": 1280115, "title": "Way of the Warrior Kid", "year": 2026},
		)

	@patch("catalog.views.company.get_or_sync_company")
	def test_company_detail_shows_status_beside_homepage(self, mock_get_company) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="company-status-user", password="pw")
		company = Company.objects.create(
			tmdb_id=88,
			name="Status Studio",
			logo_path="/logo.png",
			tmdb_raw={
				"name": "Status Studio",
				"homepage": "https://status.example",
				"discover_movies_pages": {
					"1": {
						"results": [
							{"id": 1, "title": "Future Project", "release_date": "2099-01-01"}
						]
					}
				},
				"tba_movies": [],
				"tba_scan_meta": {"scan_page": 1, "discover_total_pages": 1},
			},
			tmdb_last_sync_at=timezone.now(),
		)
		CompanyFollow.objects.create(user=user, company=company, name=company.name)
		mock_get_company.return_value = company

		client = self.client
		client.force_login(user)
		response = client.get(reverse("company_detail", args=[company.tmdb_id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'href="https://status.example"', html=False)
		self.assertContains(response, 'company-status muted', html=False)

	@patch("catalog.views.company.get_or_sync_company")
	def test_company_detail_shows_status_without_homepage(self, mock_get_company) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="company-no-homepage-user", password="pw")
		company = Company.objects.create(
			tmdb_id=89,
			name="No Homepage Studio",
			logo_path="/logo.png",
			tmdb_raw={
				"name": "No Homepage Studio",
				"discover_movies_pages": {
					"1": {
						"results": [
							{"id": 1, "title": "Future Project", "release_date": "2099-01-01"}
						]
					}
				},
				"tba_movies": [],
				"tba_scan_meta": {"scan_page": 1, "discover_total_pages": 1},
			},
			tmdb_last_sync_at=timezone.now(),
		)
		CompanyFollow.objects.create(user=user, company=company, name=company.name)
		mock_get_company.return_value = company

		client = self.client
		client.force_login(user)
		response = client.get(reverse("company_detail", args=[company.tmdb_id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'company-status muted', html=False)

	@patch("catalog.views.company.TMDbClient.from_settings")
	def test_company_detail_does_not_expose_status_for_non_followed_company(self, mock_from_settings) -> None:
		client = mock_from_settings.return_value
		client.get_company.return_value = {
			"id": 90,
			"name": "Public Studio",
			"logo_path": "/logo.png",
			"homepage": "https://public.example",
		}
		client.discover_movies_by_company.return_value = {"results": [], "total_pages": 1, "total_results": 0}

		user = get_user_model().objects.create_user(username="public-company-user", password="pw")
		self.client.force_login(user)

		response = self.client.get(reverse("company_detail", args=[90]))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["company_status_label"], "")

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

	def test_compact_company_filmography_command_compacts_existing_rows(self) -> None:
		company = Company.objects.create(
			tmdb_id=41077,
			name="A24",
			logo_path="/logo.png",
			tmdb_raw={
				"name": "A24",
				"discover_movies_pages": {
					"1": {
						"page": 1,
						"results": [
							{
								"id": 123,
								"title": "Example Film",
								"release_date": "2026-01-02",
								"poster_path": "/poster.jpg",
								"overview": "large payload",
							},
						]
					}
				},
			},
		)

		call_command("compact_company_filmography", "--company", str(company.tmdb_id))
		company.refresh_from_db(fields=["tmdb_raw"])
		raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		pages = raw.get("discover_movies_pages") or {}

		self.assertEqual(pages["1"]["results"][0], {"id": 123, "title": "Example Film", "year": 2026})

	def test_compact_person_credits_command_compacts_existing_rows(self) -> None:
		person = Person.objects.create(
			tmdb_id=1,
			name="Example Person",
			profile_path="/profile.png",
			tmdb_raw={"name": "Example Person"},
			tmdb_credits_raw={
				"cast": [
					{
						"id": 10,
						"title": "Example Movie",
						"character": "Hero",
						"release_date": "2025-01-01",
						"popularity": 12.3,
						"media_type": "movie",
						"poster_path": "/poster.jpg",
						"backdrop_path": "/backdrop.jpg",
						"vote_count": 999,
						"vote_average": 8.1,
					},
				],
				"crew": [],
			},
		)

		call_command("compact_person_credits", "--person", str(person.tmdb_id))
		person.refresh_from_db(fields=["tmdb_credits_raw"])
		credits = person.tmdb_credits_raw if isinstance(person.tmdb_credits_raw, dict) else {}

		self.assertEqual(
			credits["cast"][0],
			{
				"id": 10,
				"title": "Example Movie",
				"release_date": "2025-01-01",
				"popularity": 12.3,
				"media_type": "movie",
				"poster_path": "/poster.jpg",
				"backdrop_path": "/backdrop.jpg",
				"character": "Hero",
			},
		)
