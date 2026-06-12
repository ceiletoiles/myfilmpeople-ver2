from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Company, CompanyFollow, FollowActivity, Person
from catalog.models import PersonFollow
from .models import EmailVerification, PasswordResetRequestLog, PasswordResetToken

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
		self.assertEqual(PersonFollow.objects.get(user=self.user, person=person, role="Actor").status_key, "")
		response = self.client.post(reverse("person_unfollow", args=[person.tmdb_id]), {"role": "Actor"})
		self.assertEqual(response.status_code, 302)
		response = self.client.post(reverse("follow"), {"entity_type": "company", "tmdb_id": company.tmdb_id})
		self.assertEqual(response.status_code, 302)
		self.assertEqual(CompanyFollow.objects.get(user=self.user, company=company).status_key, "")
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
	@patch("accounts.email_services.requests.post")
	def test_signup_uses_brevo_api_when_available(self, mock_post) -> None:
		mock_response = Mock()
		mock_response.raise_for_status.return_value = None
		mock_post.return_value = mock_response

		with self.settings(BREVO_API_KEY="brevo-test-key"):
			response = self.client.post(
				reverse("signup"),
				{
					"username": "brevousr",
					"email": "brevousr@example.com",
					"password1": "pass12345!",
					"password2": "pass12345!",
				},
			)

		self.assertRedirects(response, reverse("signup_verify"))
		mock_post.assert_called_once()
		args, kwargs = mock_post.call_args
		self.assertEqual(args[0], "https://api.brevo.com/v3/smtp/email")
		self.assertEqual(kwargs["headers"]["api-key"], "brevo-test-key")
		self.assertEqual(kwargs["timeout"], 10)

	@patch("accounts.email_services.requests.post", side_effect=TimeoutError("smtp timeout"))
	def test_signup_creates_user_even_if_email_send_fails(self, _mock_post) -> None:
		with self.settings(BREVO_API_KEY="brevo-test-key"):
			response = self.client.post(
				reverse("signup"),
				{
					"username": "timeoutuser",
					"email": "timeoutuser@example.com",
					"password1": "pass12345!",
					"password2": "pass12345!",
				},
			)

		self.assertRedirects(response, reverse("signup_verify"))
		User = get_user_model()
		user = User.objects.get(username="timeoutuser")
		self.assertFalse(user.is_active)
		self.assertTrue(self.client.session.get("pending_signup_verification"))

	@patch("accounts.email_services.requests.post")
	def test_signup_sends_verification_code_and_activates_after_submit(self, mock_post) -> None:
		mock_response = Mock()
		mock_response.raise_for_status.return_value = None
		mock_post.return_value = mock_response

		with self.settings(BREVO_API_KEY="brevo-test-key"):
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
		payload = json.loads(mock_post.call_args.kwargs["data"])
		match = re.search(r"(\d{6})", payload["textContent"])
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

		mock_response = Mock()
		mock_response.raise_for_status.return_value = None
		with self.settings(BREVO_API_KEY="brevo-test-key"), patch("accounts.email_services.requests.post", return_value=mock_response) as mock_post:
			response = self.client.get(reverse("trigger_email_verification"))

		self.assertRedirects(response, reverse("signup_verify"))
		mock_post.assert_called_once()


class PasswordResetTests(TestCase):
	@patch("accounts.email_services.requests.post")
	def test_password_reset_request_sends_brevo_email_and_stores_hashed_token(self, mock_post) -> None:
		User = get_user_model()
		user = User.objects.create_user(username="reset-user", email="reset@example.com", password="pass12345!")
		mock_response = Mock()
		mock_response.raise_for_status.return_value = None
		mock_post.return_value = mock_response

		with self.settings(BREVO_API_KEY="brevo-test-key"):
			response = self.client.post(reverse("password_reset_request"), {"email": "reset@example.com"})

		self.assertRedirects(response, reverse("password_reset_request"))
		self.assertEqual(PasswordResetRequestLog.objects.filter(action="request", user=user, success=True).count(), 1)
		token = PasswordResetToken.objects.get(user=user)
		self.assertEqual(len(token.token_hash), 64)
		payload = json.loads(mock_post.call_args.kwargs["data"])
		reset_url = re.search(r'href="([^"]+)"', payload["htmlContent"]).group(1)
		token_from_email = parse_qs(urlparse(reset_url).query)["token"][0]
		self.assertNotEqual(token.token_hash, token_from_email)

		second_token = PasswordResetToken.objects.create(
			user=user,
			token_hash="b" * 64,
			expires_at=timezone.now() + timedelta(minutes=15),
		)

		response = self.client.get(reverse("password_reset_confirm"), {"token": token_from_email})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Reset password")

		reset_response = self.client.post(
			reverse("password_reset_confirm"),
			{
				"token": token_from_email,
				"new_password1": "Newpass123!x",
				"new_password2": "Newpass123!x",
			},
		)
		self.assertRedirects(reset_response, reverse("login"))
		user.refresh_from_db()
		self.assertTrue(user.check_password("Newpass123!x"))
		token.refresh_from_db()
		self.assertIsNotNone(token.used_at)
		second_token.refresh_from_db()
		self.assertIsNotNone(second_token.used_at)

	def test_password_reset_request_is_generic_for_missing_email(self) -> None:
		response = self.client.post(reverse("password_reset_request"), {"email": "missing@example.com"})

		self.assertRedirects(response, reverse("password_reset_request"))
		self.assertEqual(PasswordResetRequestLog.objects.filter(action="request", success=False).count(), 1)
