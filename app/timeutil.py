"""Wall-clock time.

Everything in this system is stored as aware UTC, which is correct and, until
now, sufficient: a stage change or a logged call only ever needs to be
compared, and a duration is the same length in any zone.

A meeting is the first thing that has to be *read*. "Wednesday 7:00pm" is not a
moment in time, it is a moment in a place, and a group in Jackson that reads
6:00pm because the server is in UTC will have people turning up an hour late.

So: still stored as UTC, converted only at the edge where it is displayed, per
church. Nothing in the application logic ever handles a naive datetime, and the
conversion happens in exactly one place.

Choosing the fallback matters. `America/Chicago` is Journey's zone and a
sensible default for a US church, but a wrong guess here is silent, so
`church.timezone` is a real column that onboarding sets rather than something
inferred.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Chicago"

# Offered in the onboarding UI. Any IANA name is accepted; these are the ones a
# US church picks 99 percent of the time.
COMMON_TIMEZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
)


def is_valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def zone_for(church) -> ZoneInfo:
    """The church's zone, falling back rather than raising.

    A bad value in this column must not take a page down. It shows the wrong
    hour, which is visible and fixable; an exception is neither.
    """
    name = getattr(church, "timezone", None) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_local(value: datetime | None, church) -> datetime | None:
    """UTC to the church's wall clock. The only place this conversion happens."""
    if value is None:
        return None
    if value.tzinfo is None:
        # Should be impossible: UTCDateTime returns aware values. Assume UTC
        # rather than guess a local zone, which would shift the time silently.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone_for(church))


def from_local(value: datetime | None, church) -> datetime | None:
    """A wall-clock time a staff member typed, back to UTC for storage."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=zone_for(church)).astimezone(timezone.utc)


def format_local(value: datetime | None, church, fmt: str = "%A %-I:%M%p") -> str:
    local = to_local(value, church)
    if local is None:
        return ""
    return local.strftime(fmt).replace("AM", "am").replace("PM", "pm")


def now_local(church) -> datetime:
    return datetime.now(timezone.utc).astimezone(zone_for(church))
