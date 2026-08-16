"""Column types shared across every model.

UTCDateTime exists because SQLite returns timezone-naive datetimes and
Postgres returns aware ones. Comparing the two raises TypeError at runtime,
in production, inside the stuck evaluator, at 6am. Every timestamp column in
this application uses this type. There are no bare DateTime columns.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import DateTime, Uuid
from sqlalchemy.types import TypeDecorator

UTC = _dt.timezone.utc


class UTCDateTime(TypeDecorator):
    """Store aware UTC, return aware UTC, on every dialect.

    On bind: a naive value is rejected rather than assumed. Silently attaching
    UTC to a naive local timestamp is how an appointment lands six hours off.
    On result: SQLite hands back naive values, so UTC is attached on the way out.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, _dt.datetime):
            raise TypeError(f"UTCDateTime expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError(
                "Naive datetime passed to a UTCDateTime column. "
                "Use app.types.utcnow() or attach a timezone explicitly."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> _dt.datetime:
    """The only clock this application reads. Always aware, always UTC."""
    return _dt.datetime.now(tz=UTC)


# Native uuid on Postgres, CHAR(32) on SQLite, one Python type either way.
UUIDType = Uuid(as_uuid=True)
