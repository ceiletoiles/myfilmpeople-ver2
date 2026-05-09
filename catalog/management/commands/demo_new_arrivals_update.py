from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from catalog.models import CompanyFollow, PersonFollow
from catalog.models import NewMovieArrival
from catalog.new_movie_helpers import (
	extract_movie_ids_from_credits,
	extract_movie_ids_from_filmography,
	extract_movie_release_dates_from_credits,
	extract_movie_release_dates_from_filmography,
	record_new_movie_arrivals,
)
from catalog.services import get_or_sync_company, get_or_sync_person, prefetch_company_filmography


class Command(BaseCommand):
	help = (
		"Demo: create a New Arrivals notification for a metadata update (release_date). "
		"It temporarily wipes a cached release_date and then forces a TMDb sync so the "
		"code detects the change and records an update event."
	)

	def add_arguments(self, parser) -> None:
		parser.add_argument("--username", required=True)

		group = parser.add_mutually_exclusive_group(required=True)
		group.add_argument("--person", type=int, help="Followed person TMDb id")
		group.add_argument("--company", type=int, help="Followed company TMDb id")

		parser.add_argument(
			"--movie-id",
			type=int,
			default=None,
			help="Optional specific movie TMDb id to target (must be present in cached payload).",
		)
		parser.add_argument(
			"--max-pages",
			type=int,
			default=1,
			help="For company demo only: how many discover pages to prefetch (default 1).",
		)
		force_group = parser.add_mutually_exclusive_group()
		force_group.add_argument(
			"--force",
			dest="force",
			action="store_true",
			default=True,
			help=(
				"(Default) Force the demo to create an unseen update event by deleting any existing "
				"update event for the same (user, source, movie) before recording."
			),
		)
		force_group.add_argument(
			"--no-force",
			dest="force",
			action="store_false",
			help="Do not delete existing update events before recording (demo may record 0 if already seen).",
		)

	def handle(self, *args: Any, **options: Any) -> None:
		username = str(options["username"])
		person_id = options.get("person")
		company_id = options.get("company")
		movie_id_opt = options.get("movie_id")

		User = get_user_model()
		try:
			user = User.objects.get(username=username)
		except User.DoesNotExist as exc:
			raise CommandError(f"No user found with username={username!r}") from exc

		force = bool(options.get("force"))
		if person_id is not None:
			self._demo_person(user, int(person_id), movie_id_opt, force=force)
			return

		if company_id is not None:
			max_pages = int(options.get("max_pages") or 1)
			if max_pages <= 0:
				max_pages = 1
			self._demo_company(user, int(company_id), movie_id_opt, max_pages=max_pages, force=force)
			return

		raise CommandError("Must pass either --person or --company")

	def _pick_credit_movie(self, release_date_map: dict[int, str], *, movie_id_opt: int | None) -> tuple[int, str]:
		if movie_id_opt is not None:
			rd = release_date_map.get(int(movie_id_opt))
			if rd is None:
				raise CommandError("--movie-id not found in cached payload.")
			if not str(rd or "").strip():
				raise CommandError("--movie-id found but its cached release_date is already empty.")
			return int(movie_id_opt), str(rd)

		candidates = [(mid, rd) for mid, rd in release_date_map.items() if isinstance(mid, int) and str(rd or "").strip()]
		if not candidates:
			raise CommandError("No items with a non-empty cached release_date found.")
		mid, rd = candidates[0]
		return int(mid), str(rd)

	def _demo_person(self, user, tmdb_id: int, movie_id_opt: int | None, *, force: bool) -> None:
		follows = list(PersonFollow.objects.filter(user=user, person__tmdb_id=tmdb_id).select_related("person"))
		if not follows:
			raise CommandError("You are not following this person yet (no baseline).")

		p = get_or_sync_person(tmdb_id, force=False)
		p.refresh_from_db(fields=["tmdb_credits_raw", "name", "tmdb_last_sync_at"])
		credits = p.tmdb_credits_raw or {}

		old_rd_map = extract_movie_release_dates_from_credits(credits)
		mid, old_rd = self._pick_credit_movie(old_rd_map, movie_id_opt=movie_id_opt)

		self.stdout.write(self.style.SUCCESS(f"Person: {p.name} ({tmdb_id})"))
		self.stdout.write(f"Target movie: {mid} old release_date: {old_rd}")

		# Wipe cached release_date for that movie id.
		for key in ("cast", "crew"):
			arr = credits.get(key) or []
			if not isinstance(arr, list):
				continue
			for item in arr:
				if isinstance(item, dict) and item.get("id") == mid:
					item["release_date"] = ""

		p.tmdb_credits_raw = credits
		p.save(update_fields=["tmdb_credits_raw", "updated_at"])
		p.refresh_from_db(fields=["tmdb_credits_raw"])

		old_credits = p.tmdb_credits_raw or {}
		old_ids = extract_movie_ids_from_credits(old_credits)
		old_rds = extract_movie_release_dates_from_credits(old_credits)
		self.stdout.write(f"After wipe, cached release_date: {old_rds.get(mid)!r}")

		# Force TMDb sync.
		p = get_or_sync_person(tmdb_id, force=True)
		new_credits = p.tmdb_credits_raw or {}
		new_ids = extract_movie_ids_from_credits(new_credits)
		new_rds = extract_movie_release_dates_from_credits(new_credits)
		self.stdout.write(f"After sync, cached release_date: {new_rds.get(mid)!r}")

		if force:
			NewMovieArrival.objects.filter(
				user=user,
				event_type="update",
				source_type="person",
				source_id=tmdb_id,
				movie__tmdb_id=mid,
			).delete()

		created = 0
		for f in follows:
			created += record_new_movie_arrivals(
				user=user,
				source_type="person",
				source_id=tmdb_id,
				source_name=p.name,
				old_movie_ids=old_ids,
				new_movie_ids=new_ids,
				role=f.role or "",
				old_release_dates=old_rds,
				new_release_dates=new_rds,
			)

		self.stdout.write(self.style.SUCCESS(f"Recorded arrivals: {created}"))
		unseen = NewMovieArrival.objects.filter(user=user, is_seen=False).count()
		self.stdout.write(self.style.SUCCESS(f"Unseen arrivals now: {unseen}"))
		self.stdout.write(self.style.SUCCESS("Now refresh the site: the notification badge should increase."))

	def _demo_company(
		self,
		user,
		tmdb_id: int,
		movie_id_opt: int | None,
		*,
		max_pages: int,
		force: bool,
	) -> None:
		follows = list(CompanyFollow.objects.filter(user=user, company__tmdb_id=tmdb_id).select_related("company"))
		if not follows:
			raise CommandError("You are not following this company yet (no baseline).")

		company = get_or_sync_company(tmdb_id, force=False)
		# Ensure we have at least some cached pages.
		try:
			prefetch_company_filmography(company, force=False, max_pages=max_pages)
		except Exception:
			pass
		company.refresh_from_db(fields=["tmdb_raw", "name", "tmdb_last_sync_at"])
		raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}

		old_rd_map = extract_movie_release_dates_from_filmography(raw)
		mid, old_rd = self._pick_credit_movie(old_rd_map, movie_id_opt=movie_id_opt)

		self.stdout.write(self.style.SUCCESS(f"Company: {company.name} ({tmdb_id})"))
		self.stdout.write(f"Target movie: {mid} old release_date: {old_rd}")

		# Wipe cached release_date inside discover pages.
		pages = raw.get("discover_movies_pages")
		if not isinstance(pages, dict):
			raise CommandError("Company has no cached discover_movies_pages. Try syncing/prefetching first.")
		for payload in pages.values():
			if not isinstance(payload, dict):
				continue
			for item in payload.get("results", []) or []:
				if isinstance(item, dict) and item.get("id") == mid:
					item["release_date"] = ""

		company.tmdb_raw = raw
		company.save(update_fields=["tmdb_raw", "updated_at"])
		company.refresh_from_db(fields=["tmdb_raw"])

		old_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		old_ids = extract_movie_ids_from_filmography(old_raw)
		old_rds = extract_movie_release_dates_from_filmography(old_raw)
		self.stdout.write(f"After wipe, cached release_date: {old_rds.get(mid)!r}")

		# Force company sync + prefetch to restore
		company = get_or_sync_company(tmdb_id, force=True)
		try:
			prefetch_company_filmography(company, force=True, max_pages=max_pages)
		except Exception:
			pass

		company.refresh_from_db(fields=["tmdb_raw", "name"])
		new_raw = company.tmdb_raw if isinstance(company.tmdb_raw, dict) else {}
		new_ids = extract_movie_ids_from_filmography(new_raw)
		new_rds = extract_movie_release_dates_from_filmography(new_raw)
		self.stdout.write(f"After sync, cached release_date: {new_rds.get(mid)!r}")

		if force:
			NewMovieArrival.objects.filter(
				user=user,
				event_type="update",
				source_type="company",
				source_id=tmdb_id,
				movie__tmdb_id=mid,
			).delete()

		created = record_new_movie_arrivals(
			user=user,
			source_type="company",
			source_id=tmdb_id,
			source_name=company.name,
			old_movie_ids=old_ids,
			new_movie_ids=new_ids,
			role="studio",
			old_release_dates=old_rds,
			new_release_dates=new_rds,
		)

		self.stdout.write(self.style.SUCCESS(f"Recorded arrivals: {created}"))
		unseen = NewMovieArrival.objects.filter(user=user, is_seen=False).count()
		self.stdout.write(self.style.SUCCESS(f"Unseen arrivals now: {unseen}"))
		self.stdout.write(self.style.SUCCESS("Now refresh the site: the notification badge should increase."))
