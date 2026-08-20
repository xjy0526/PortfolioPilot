"""UTC persistence and configurable display-time helpers."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from config import settings


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persistence and trace fields."""
    return datetime.now(UTC)


def utc_now_iso(*, timespec: str = "seconds") -> str:
    """Return a stable ISO-8601 UTC timestamp."""
    return utc_now().isoformat(timespec=timespec).replace("+00:00", "Z")


def display_timezone() -> ZoneInfo:
    """Return the configured presentation timezone."""
    return ZoneInfo(settings.DISPLAY_TIMEZONE)


def display_now() -> datetime:
    """Return current time converted from UTC to the presentation timezone."""
    return utc_now().astimezone(display_timezone())
