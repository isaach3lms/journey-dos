"""Model primitives shared by every table in the system.

Two things are enforced here rather than left to each model author:

`UTCDateTime`
    SQLite has no timezone type and hands back naive datetimes. Postgres with
    `timestamptz` hands back aware ones. Comparing the two raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`, and it
    raises it in production only, because local development is SQLite. This
    decorator normalizes both ends so the comparison never happens.

`TenantScoped`
    Every table carries `church_id`. Adding a church is a row, not a
    migration. A model that inherits this cannot be written without a tenant.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator

from app.extensions import db


class UTCDateTime(TypeDecorator):
    """Store aware UTC, return aware UTC, on both SQLite and Postgres."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"Expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            # A naive datetime reaching the database is a bug upstream, but
            # assuming UTC is safer than writing an ambiguous value.
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    """Aware UTC now. Use this everywhere instead of `datetime.utcnow()`."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class TenantScoped:
    """Mixin that puts `church_id` on a table and indexes it.

    Inherit this on every model except `Church` itself and the two global
    Bible reference tables documented in spec v3 section E.4.
    """

    @declared_attr
    def church_id(cls) -> Mapped[int]:
        return mapped_column(
            ForeignKey("church.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def church(cls):
        return db.relationship("Church")


def render_migration_item(type_, obj, autogen_context):
    """Render `UTCDateTime` as plain SQLAlchemy in generated migrations.

    Without this, Alembic writes `app.models.base.UTCDateTime(...)` into the
    migration file and the migration fails at run time with a NameError,
    because migrations do not import application code.

    Rendering the underlying type is correct as well as convenient. The
    decorator changes how Python reads and writes values, not the DDL, so
    `sa.DateTime(timezone=True)` produces exactly the same column. It also
    keeps migrations runnable years from now if this module is refactored.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime(timezone=True)"
    return False


__all__ = [
    "UTCDateTime",
    "utcnow",
    "TimestampMixin",
    "TenantScoped",
    "render_migration_item",
    "sa",
]
