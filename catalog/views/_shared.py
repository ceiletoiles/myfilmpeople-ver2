from __future__ import annotations

from datetime import date


SESSION_KEY_HIDE_SELF_APPEARANCES = "catalog_hide_self_appearances"


def _get_session_bool(session: object, key: str, default: bool) -> bool:
	"""Best-effort bool loader for session-like dicts."""
	try:
		value = getattr(session, "get")(key, default)  # type: ignore[attr-defined]
	except Exception:
		return default
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return bool(value)
	if isinstance(value, str):
		v = value.strip().lower()
		if v in {"1", "true", "t", "yes", "y", "on"}:
			return True
		if v in {"0", "false", "f", "no", "n", "off"}:
			return False
	return default


def _add_years_safe(d: date, years: int) -> date:
	try:
		return d.replace(year=d.year + years)
	except ValueError:
		# Handles Feb 29 -> Feb 28 on non-leap years.
		return d.replace(year=d.year + years, day=28)


def _add_months_safe(d: date, months: int) -> date:
	# months can be > 12
	month0 = (d.month - 1) + months
	new_year = d.year + (month0 // 12)
	new_month = (month0 % 12) + 1
	# Clamp day to end-of-month
	for day in (d.day, 28, 27, 26, 25):
		try:
			return date(new_year, new_month, day)
		except ValueError:
			continue
	return date(new_year, new_month, 1)


def _countdown_text(*, today: date, release_dt: date) -> str:
	if release_dt < today:
		return ""
	if release_dt == today:
		return "Today"
	days_left = (release_dt - today).days
	if days_left == 1:
		return "Tomorrow"
	if days_left <= 31:
		return f"{days_left} Day" if days_left == 1 else f"{days_left} Days"

	# Calendar-ish breakdown: Years, Months, Days.
	years = release_dt.year - today.year
	anchor = _add_years_safe(today, years)
	if anchor > release_dt:
		years -= 1
		anchor = _add_years_safe(today, years)

	months = (release_dt.year - anchor.year) * 12 + (release_dt.month - anchor.month)
	anchor2 = _add_months_safe(anchor, months)
	if anchor2 > release_dt:
		months -= 1
		anchor2 = _add_months_safe(anchor, months)

	days = max((release_dt - anchor2).days, 0)

	parts: list[str] = []
	if years > 0:
		parts.append(f"{years} Year" if years == 1 else f"{years} Years")
	if months > 0:
		parts.append(f"{months} Month")
	if days > 0:
		parts.append(f"{days} Days")
	return ", ".join(parts)


def _normalize_role(value: str) -> str:
	return (value or "").strip().lower()


def _role_category(role: str) -> str:
	role_n = _normalize_role(role)
	if role_n == "director":
		return "director"
	if role_n == "actor":
		return "actor"
	return "crew"


def _person_role_options_from_credits(credits_raw: dict) -> list[str]:
	roles: set[str] = set()
	cast_items = credits_raw.get("cast", []) or []
	crew_items = credits_raw.get("crew", []) or []

	if any(True for _ in cast_items):
		roles.add("Actor")

	for item in crew_items:
		job = (item.get("job") or "").strip()
		if job:
			roles.add(job)

	preferred = {"director": 0, "actor": 1}
	return sorted(
		roles,
		key=lambda r: (
			preferred.get(_normalize_role(r), 99),
			r.lower(),
		),
	)


def _parse_iso_date(value: str | None) -> date | None:
	if not value:
		return None
	try:
		return date.fromisoformat(value)
	except ValueError:
		return None


def _credit_role_for_movie(*, credits_raw: dict, movie_id: int) -> str:
	for c in credits_raw.get("crew", []) or []:
		if c.get("id") == movie_id and c.get("media_type") in (None, "movie"):
			if c.get("job"):
				return str(c.get("job"))
			return "Crew"
	for c in credits_raw.get("cast", []) or []:
		if c.get("id") == movie_id and c.get("media_type") in (None, "movie"):
			if c.get("character"):
				return f"Actor ({c.get('character')})"
			return "Actor"
	return ""
