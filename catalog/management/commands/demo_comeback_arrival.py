from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.models import Movie, NewMovieArrival


class Command(BaseCommand):
	help = (
		"Seed fake comeback arrivals so you can verify the UI quickly. "
		"Creates unseen NewMovieArrival rows with event_meta.kind='comeback'."
	)

	def add_arguments(self, parser) -> None:
		parser.add_argument("--username", default="", help="Target username. Optional when only one user exists.")
		parser.add_argument("--count", type=int, default=1, help="How many fake comeback arrivals to create.")
		parser.add_argument("--role", default="director", help="Role label for the arrival (director/actor/crew).")
		parser.add_argument("--person-name", default="Demo Comeback Person", help="Source person display name.")
		parser.add_argument("--source-id", type=int, default=987654, help="Source person TMDb id for the event.")
		parser.add_argument("--start-movie-id", type=int, default=970000, help="Starting TMDb id for fake movies.")
		parser.add_argument("--gap-years", type=int, default=7, help="Gap years to embed in comeback metadata.")

	def _resolve_user(self, username: str):
		User = get_user_model()
		if username:
			try:
				return User.objects.get(username=username)
			except User.DoesNotExist as exc:
				raise CommandError(f"No user found with username={username!r}") from exc

		users = list(User.objects.all().order_by("id")[:2])
		if len(users) == 1:
			return users[0]
		if len(users) == 0:
			demo_user = User.objects.create_user(username="demo", password="demo12345")
			self.stdout.write(self.style.WARNING("No users found. Created demo user: demo / demo12345"))
			return demo_user
		raise CommandError("Multiple users found. Pass --username.")

	def handle(self, *args: Any, **options: Any) -> None:
		user = self._resolve_user(str(options.get("username") or "").strip())
		count = max(int(options.get("count") or 1), 1)
		role = str(options.get("role") or "director").strip() or "director"
		source_name = str(options.get("person_name") or "Demo Comeback Person").strip() or "Demo Comeback Person"
		source_id = int(options.get("source_id") or 987654)
		start_movie_id = int(options.get("start_movie_id") or 970000)
		gap_years = max(int(options.get("gap_years") or 7), 1)

		created = 0
		today = timezone.now().date()
		for idx in range(count):
			movie_tmdb_id = start_movie_id + idx
			release_date = today - timedelta(days=idx * 31)
			last_release_date = release_date - timedelta(days=365 * gap_years)
			movie, _ = Movie.objects.get_or_create(
				tmdb_id=movie_tmdb_id,
				defaults={
					"title": f"Demo Comeback Movie {idx + 1}",
					"release_date": release_date,
					"poster_path": "",
					"tmdb_raw": {},
					"tmdb_credits_raw": {},
				},
			)
			if movie.release_date != release_date:
				movie.release_date = release_date
				movie.save(update_fields=["release_date", "updated_at"])

			arrival, was_created = NewMovieArrival.objects.get_or_create(
				user=user,
				movie=movie,
				event_type="new",
				source_type="person",
				source_id=source_id,
				defaults={
					"source_name": source_name,
					"role": role,
					"is_seen": False,
					"event_meta": {
						"kind": "comeback",
						"last_release_date": last_release_date.isoformat(),
						"new_release_date": release_date.isoformat(),
						"gap_days": (release_date - last_release_date).days,
						"gap_label": f"{gap_years} years",
						"threshold_days": 365 * 3,
					},
				},
			)
			if not was_created:
				arrival.source_name = source_name
				arrival.role = role
				arrival.is_seen = False
				arrival.event_meta = {
					"kind": "comeback",
					"last_release_date": last_release_date.isoformat(),
					"new_release_date": release_date.isoformat(),
					"gap_days": (release_date - last_release_date).days,
					"gap_label": f"{gap_years} years",
					"threshold_days": 365 * 3,
				}
				arrival.save(update_fields=["source_name", "role", "is_seen", "event_meta"])
			created += 1

		unseen = NewMovieArrival.objects.filter(user=user, is_seen=False).count()
		self.stdout.write(self.style.SUCCESS(f"Seeded comeback arrivals: {created}"))
		self.stdout.write(self.style.SUCCESS(f"User: {user.username} | Unseen now: {unseen}"))
		self.stdout.write(self.style.SUCCESS("Open New Arrivals page to verify 'Back after ...' notes."))
