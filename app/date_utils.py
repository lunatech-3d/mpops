"""Shared date storage and user-interface formatting helpers."""

from datetime import date, datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_date_value(value: Any) -> datetime | None:
    """Return a datetime for an ISO-compatible stored value, or ``None``."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_display_date(value: Any, empty: str = "") -> str:
    """Format an ISO stored date as ``MM-DD-YYYY`` for display only."""
    parsed = _parse_date_value(value)
    return parsed.strftime("%m-%d-%Y") if parsed else (empty if value in (None, "") else str(value))


def format_display_datetime(value: Any, empty: str = "") -> str:
    """Format an ISO stored timestamp as ``MM-DD-YYYY h:mm AM/PM``."""
    parsed = _parse_date_value(value)
    if not parsed:
        return empty if value in (None, "") else str(value)
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed.strftime('%m-%d-%Y')} {hour}:{parsed.strftime('%M %p')}"


def display_date_to_iso(value: Any) -> str | None:
    """Convert a UI date (US display format or ISO) to ISO storage format."""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Date must use MM-DD-YYYY or MM/DD/YYYY format.")


def display_datetime_to_iso(value: Any) -> str | None:
    """Convert a UI timestamp (US display format or ISO) to ISO storage format."""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%m-%d-%Y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(text, pattern).isoformat(timespec="minutes")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat(timespec="minutes")
    except ValueError as exc:
        raise ValueError("Date and time must use MM-DD-YYYY h:mm AM/PM format.") from exc
