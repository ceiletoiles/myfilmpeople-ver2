from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.models import Company, CompanyFollow

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

		self.assertEqual(follow.status_key, "tba")
		self.assertEqual(follow.status, "TBA")

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
