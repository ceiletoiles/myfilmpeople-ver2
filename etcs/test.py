"""Demo runner for New Arrivals "update" notifications.

This is intended for quick demos (e.g., in front of an invigilator) without
waiting for TMDb to naturally update metadata.

It runs the Django management command `demo_new_arrivals_update`, which:
- temporarily wipes a cached `release_date` for one movie in the followed entity payload
- forces a TMDb sync so the date is restored
- records a New Arrivals `event_type="update"` notification

Usage (person):
  python etcs/test.py --username diksha6950 --person 525

Usage (company):
  python etcs/test.py --username diksha6950 --company 33 --max-pages 1
"""


from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--username", required=True)
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--person", type=int)
	group.add_argument("--company", type=int)
	parser.add_argument("--movie-id", type=int, default=None)
	parser.add_argument("--max-pages", type=int, default=1)
	args = parser.parse_args()

	project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
	sys.path.insert(0, project_root)
	os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

	import django
	django.setup()

	from django.core.management import call_command

	kwargs: dict[str, object] = {
		"username": args.username,
		"movie_id": args.movie_id,
		"max_pages": args.max_pages,
	}
	if args.person is not None:
		kwargs["person"] = args.person
	else:
		kwargs["company"] = args.company

	call_command("demo_new_arrivals_update", **kwargs)


if __name__ == "__main__":
	main()