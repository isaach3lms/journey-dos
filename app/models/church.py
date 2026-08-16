"""The tenant, and the hosts that resolve to it.

Amendment to C.2 of the v1 spec: Journey uses a custom domain
(app.thejourneychurchsemo.com), not a subdomain of the platform. A single
``slug`` column cannot express that, and during DNS cutover both hosts must
resolve at once. Hosts therefore live in their own table.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.types import UTCDateTime, UUIDType

CHURCH_STATUSES = ("active", "suspended")


class Church(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """One row per client. Adding a church is a row, never a migration."""

    __tablename__ = "church"
    __table_args__ = (UniqueConstraint("slug"),)

    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2))

    # Every local-time statement in the product depends on this. "Sends at day
    # 21" and "contacted within 48 hours" are meaningless without it.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/Chicago"
    )

    # The only tenant-overridable brand token in v1. See app/brand.py.
    accent_hex: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#2563FF"
    )
    logo_path: Mapped[str | None] = mapped_column(String(255))

    from_name: Mapped[str | None] = mapped_column(String(120))
    from_email: Mapped[str | None] = mapped_column(String(255))
    reply_to_email: Mapped[str | None] = mapped_column(String(255))

    connect_card_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    connect_card_intro: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    domains: Mapped[list["ChurchDomain"]] = relationship(
        back_populates="church",
        cascade="all, delete-orphan",
        order_by="ChurchDomain.is_primary.desc()",
    )

    @property
    def primary_host(self) -> str | None:
        for domain in self.domains:
            if domain.is_primary:
                return domain.host
        return self.domains[0].host if self.domains else None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Church {self.slug}>"


class ChurchDomain(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """A host that resolves to a tenant.

    Journey gets two rows: the custom domain the pastor gives out, and the
    platform host that keeps working during a DNS change or a certificate
    problem. Both point at the same tenant.
    """

    __tablename__ = "church_domain"
    __table_args__ = (
        UniqueConstraint("church_id", "id"),
        UniqueConstraint("host"),
        db.Index("ix_church_domain_church_id_is_primary", "church_id", "is_primary"),
    )

    church_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("church.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stored lowercase, no port, no leading www. Normalization happens in
    # app.tenancy.normalize_host and nowhere else.
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    church: Mapped[Church] = relationship(back_populates="domains")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChurchDomain {self.host}>"
