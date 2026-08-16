"""Shared model plumbing.

The composite tenant key is the floor of this system's multi-tenancy. Every
tenant table carries UNIQUE (church_id, id) so that every child table can
declare a composite foreign key back to it:

    ForeignKeyConstraint(
        ["church_id", "person_id"], ["person.church_id", "person.id"]
    )

That makes a cross tenant reference structurally impossible instead of
conventionally avoided. Query-layer scoping added later is defense in depth,
not the primary control.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.types import UTCDateTime, UUIDType, utcnow


class TimestampMixin:
    """created_at and updated_at, always aware UTC."""

    created_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class UUIDPrimaryKeyMixin:
    """Opaque primary keys.

    Sequential integers leak roster size and let anyone walk the records by
    incrementing a number. This application puts identifiers in public URLs,
    on the connect card confirmation and on unsubscribe links, so the keys must
    not be guessable.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )


class TenantMixin(UUIDPrimaryKeyMixin):
    """Every tenant-scoped table inherits this. No exceptions in v1."""

    church_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("church.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def tenant_table_args(*extra):
    """Return __table_args__ with the composite tenant key always present."""
    return (UniqueConstraint("church_id", "id"), *extra)


__all__ = [
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "TenantMixin",
    "tenant_table_args",
]
