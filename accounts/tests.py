from __future__ import annotations

import re
from unittest.mock import patch

from django.core import mail
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Company, CompanyFollow, FollowActivity, Person
from .models import EmailVerification

from .views import _annotate_company_status


class CompanyStatusTests(TestCase):
	def setUp(self) -> None:
		User = get_user_model()
		self.user = User.objects.create_user(username="tester", password="pass12345")

	def _make_follow(self, tmdb_raw: dict) -> CompanyFollow:
		company = Company.objects.create(tmdb_id=101, name="Studio 101", tmdb_raw=tmdb_raw)
		return CompanyFollow.objects.create(user=self.user, company=company, name=company.name)

	def test_company_status_falls_back_to_tba_scan(self) -> None:
		follow = self._make_follow(
			{
				"discover_movies_pages": {
					"1": {
						"results": [
							{"id": 1, "title": "Old Release", "release_date": "2000-01-01"}
						],
					}
				},
			}
		)

		with patch("accounts.views.get_or_sync_company_tba_movies_page", return_value=([
			{"id": 99, "title": "Mystery Project", "release_date": ""}
		], False, False)):
			_annotate_company_status(follow)

		self.assertEqual(follow.status_key, "announced")
		self.assertEqual(follow.status, "Announced")

	def test_company_status_stays_inactive_when_no_tba_titles_exist(self) -> None:
		follow = self._make_follow(
			{
				"discover_movies_pages": {
					"1": {
						"results": [
							{"id": 1, "title": "Old Release", "release_date": "2000-01-01"}
						],
					}
				},
			}
		)

		with patch("accounts.views.get_or_sync_company_tba_movies_page", return_value=([], False, False)):
			_annotate_company_status(follow)

		self.assertEqual(follow.status_key, "inactive")
		self.assertEqual(follow.status, "Inactive")


class ProfilePartialResponseTests(TestCase):
	def test_profile_status_filter_returns_partial_html(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="profile-user", password="pass12345")
		self.client.force_login(user)

		response = self.client.get(reverse("user_profile"), {"status": "all", "partial": "1"})

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()["ok"])
		self.assertIn("data-profile-shell", response.json()["html"])


class ProfileActivityTests(TestCase):
	def setUp(self) -> None:
		User = get_user_model()
		self.user = User.objects.create_user(username="activity-user", password="pass12345")
		self.client.force_login(self.user)

	def test_profile_page_shows_follow_activity_toggle_and_feed(self) -> None:
		person = Person.objects.create(tmdb_id=2001, name="Activity Person", profile_path="/person.jpg")
		company = Company.objects.create(tmdb_id=2002, name="Activity Studio", logo_path="/studio.png")
		FollowActivity.objects.create(
			user=self.user,
			entity_type=FollowActivity.ENTITY_PERSON,
			action=FollowActivity.ACTION_FOLLOW,
			person=person,
			entity_name=person.name,
			role="Actor",
			image_path=person.profile_path,
		)
		FollowActivity.objects.create(
			user=self.user,
			entity_type=FollowActivity.ENTITY_COMPANY,
			action=FollowActivity.ACTION_UNFOLLOW,
			company=company,
			entity_name=company.name,
			image_path=company.logo_path,
		)

		response = self.client.get(reverse("user_profile"), {"partial": "1"})
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertIn("Followed Activity Person as Actor", payload["html"])
		self.assertIn("Unfollowed Activity Studio", payload["html"])

		full_response = self.client.get(reverse("user_profile"))
		self.assertEqual(full_response.status_code, 200)
		self.assertContains(full_response, "data-profile-view-toggle")
		self.assertContains(full_response, "profile-activity")

	@patch("catalog.views.follow._person_role_options_from_credits", return_value=["Actor"])
	@patch("catalog.views.follow.get_or_sync_person")
	@patch("catalog.views.follow.get_or_sync_company")
	@patch("catalog.views.follow.prefetch_company_filmography", return_value=0)
	def test_follow_and_unfollow_record_activity(self, _mock_prefetch, mock_get_company, mock_get_person, _mock_roles) -> None:
		person = Person.objects.create(
			tmdb_id=3001,
			name="Logged Person",
			profile_path="/logged-person.jpg",
			tmdb_credits_raw={"cast": [{"id": 1}]},
		)
		company = Company.objects.create(tmdb_id=3002, name="Logged Studio", logo_path="/logged-studio.png")
		mock_get_person.return_value = person
		mock_get_company.return_value = company

		response = self.client.post(reverse("follow"), {"entity_type": "person", "tmdb_id": person.tmdb_id, "role": "Actor"})
		self.assertEqual(response.status_code, 302)
		response = self.client.post(reverse("person_unfollow", args=[person.tmdb_id]), {"role": "Actor"})
		self.assertEqual(response.status_code, 302)
		response = self.client.post(reverse("follow"), {"entity_type": "company", "tmdb_id": company.tmdb_id})
		self.assertEqual(response.status_code, 302)
		response = self.client.post(reverse("company_unfollow", args=[company.tmdb_id]))
		self.assertEqual(response.status_code, 302)

		activities = list(FollowActivity.objects.filter(user=self.user).order_by("created_at", "id"))
		self.assertEqual(
			[activity.action for activity in activities],
			[
				FollowActivity.ACTION_FOLLOW,
				FollowActivity.ACTION_UNFOLLOW,
				FollowActivity.ACTION_FOLLOW,
				FollowActivity.ACTION_UNFOLLOW,
			],
		)
		self.assertEqual(activities[0].summary, "Followed Logged Person as Actor")
		self.assertEqual(activities[2].summary, "Followed Logged Studio")


class SignupVerificationTests(TestCase):
	def test_signup_sends_verification_code_and_activates_after_submit(self) -> None:
		response = self.client.post(
			reverse("signup"),
			{
				"username": "newuser",
				"email": "newuser@example.com",
				"password1": "pass12345!",
				"password2": "pass12345!",
			},
		)

		self.assertRedirects(response, reverse("signup_verify"))
		User = get_user_model()
		user = User.objects.get(username="newuser")
		self.assertFalse(user.is_active)
		self.assertEqual(len(mail.outbox), 1)

		match = re.search(r"(\d{6})", mail.outbox[0].body)
		self.assertIsNotNone(match)
		otp_code = match.group(1)

		verify_response = self.client.post(reverse("signup_verify"), {"otp_code": otp_code})
		self.assertRedirects(verify_response, reverse("home"))
		user.refresh_from_db()
		self.assertTrue(user.is_active)
		self.assertTrue(self.client.session.get("_auth_user_id"))

	def test_profile_hides_verify_link_for_signup_verified_users(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="signup-user", email="signup@example.com", password="pass12345!")
		EmailVerification.objects.create(user=user, email_verified=True, verified_via_signup=True)
		self.client.force_login(user)

		response = self.client.get(reverse("user_profile"))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, "Verify email")
		self.assertNotContains(response, "Verified")

	def test_profile_shows_verify_link_for_legacy_unverified_users(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="legacy-user", email="legacy@example.com", password="pass12345!")
		EmailVerification.objects.create(user=user, email_verified=False, verified_via_signup=False)
		self.client.force_login(user)

		response = self.client.get(reverse("user_profile"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Verify email")

	def test_profile_verify_link_triggers_email_verification_flow(self) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="legacy-user-2", email="legacy2@example.com", password="pass12345!")
		EmailVerification.objects.create(user=user, email_verified=False, verified_via_signup=False)
		self.client.force_login(user)

		response = self.client.get(reverse("trigger_email_verification"))

		self.assertRedirects(response, reverse("signup_verify"))
		self.assertEqual(len(mail.outbox), 1)
