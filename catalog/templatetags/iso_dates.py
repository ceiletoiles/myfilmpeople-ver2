from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django import template

register = template.Library()


def _format_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


@register.filter(name='iso_date_long')
def iso_date_long(value: Any) -> str:
    """Format an ISO `YYYY-MM-DD` date string or a date/datetime as "Month D, YYYY".

    Returns 'TBA' for empty/falsey input, and leaves unknown formats unchanged.
    """
    if not value:
        return 'TBA'
    if isinstance(value, datetime):
        return _format_date(value.date())
    if isinstance(value, date):
        return _format_date(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 'TBA'
        # Try ISO date first (YYYY-MM-DD)
        try:
            d = date.fromisoformat(s)
            return _format_date(d)
        except Exception:
            pass
        # Try full datetime ISO
        try:
            dt = datetime.fromisoformat(s)
            return _format_date(dt.date())
        except Exception:
            return s
    return str(value)
