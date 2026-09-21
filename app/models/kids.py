"""Kids check-in.

**A check-in system with no check-out record is a headcount, not a safety
system.** The demo omitted the check-out write path; this does not. Every
`Checkin` row can answer three questions after the fact: who was here, who
collected them, and when. A room that cannot answer the second one has no
account of where a child went.

The two codes are described in `app/pickup.py`. In short: the household PIN
identifies a family at the kiosk, and the pickup code, generated per household
per session, authorizes collection. They are never interchangeable.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow


class CheckinSession(TenantScoped, TimestampMixin, db.Model):
    """One occasion children are checked in for. A Sunday service, usually."""

    __tablename__ = "checkin_session"
    __table_args__ = (
        Index("ix_checkin_session_church_time", "church_id", "starts_at"),
        Index("ix_checkin_session_open", "church_id", "is_open"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False, default="Sunday")
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    # Only an open session accepts check-ins. Closing it does not check anyone
    # out: a child still in a room at the end of a service is exactly the thing
    # staff need to see, so it stays visible rather than being tidied away.
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    checkins: Mapped[list["Checkin"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Checkin.checked_in_at",
    )

    def __repr__(self) -> str:
        return f"<CheckinSession {self.name!r} at={self.starts_at}>"

    @property
    def present(self) -> list["Checkin"]:
        return [c for c in self.checkins if c.checked_out_at is None]

    @property
    def present_count(self) -> int:
        return len(self.present)

    @property
    def collected_count(self) -> int:
        return sum(1 for c in self.checkins if c.checked_out_at is not None)

    def close(self) -> None:
        self.is_open = False
        self.closed_at = utcnow()

    def reopen(self) -> None:
        self.is_open = True
        self.closed_at = None

    def code_for_household(self, household_id: int) -> str | None:
        """Siblings share one code, so a parent carries one, not three."""
        for checkin in self.checkins:
            if checkin.household_id == household_id:
                return checkin.pickup_code
        return None

    def issue_pickup_code(self, household_id: int) -> str:
        """The household's code for this session, generated once."""
        from app.pickup import generate_pickup_code

        existing = self.code_for_household(household_id)
        if existing:
            return existing

        def is_taken(code: str) -> bool:
            return db.session.scalar(
                db.select(Checkin.id).where(
                    Checkin.session_id == self.id, Checkin.pickup_code == code
                )
            ) is not None

        return generate_pickup_code(is_taken)

    @classmethod
    def get_for_church(cls, church_id: int, session_id: int) -> "CheckinSession | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == session_id, cls.church_id == church_id)
        )

    @classmethod
    def open_session(cls, church_id: int) -> "CheckinSession | None":
        """The session a kiosk is working against. Most recent open one."""
        return db.session.scalar(
            db.select(cls)
            .where(cls.church_id == church_id, cls.is_open.is_(True))
            .order_by(cls.starts_at.desc())
            .limit(1)
        )

    @classmethod
    def recent(cls, church_id: int, limit: int = 10):
        return (
            db.select(cls)
            .where(cls.church_id == church_id)
            .order_by(cls.starts_at.desc())
            .limit(limit)
        )


class Checkin(TenantScoped, TimestampMixin, db.Model):
    """One child, one session. Append-only in spirit: rows are not deleted."""

    __tablename__ = "checkin"
    __table_args__ = (
        # A child cannot be checked into the same session twice. Without this
        # a double-tapped kiosk button produces two rows, two labels, and two
        # children to account for at pickup.
        UniqueConstraint("session_id", "person_id", name="uq_checkin_person_id"),
        Index("ix_checkin_session_code", "session_id", "pickup_code"),
        Index("ix_checkin_church_present", "church_id", "session_id", "checked_out_at"),
        Index("ix_checkin_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("checkin_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session: Mapped["CheckinSession"] = relationship(back_populates="checkins")

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship(foreign_keys=[person_id])  # noqa: F821

    # Copied, not derived. A child moved between households after a Sunday must
    # not change who was recorded as collecting them that Sunday.
    household_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL")
    )
    household_name: Mapped[Optional[str]] = mapped_column(String(160))

    pickup_code: Mapped[str] = mapped_column(String(8), nullable=False)
    room: Mapped[Optional[str]] = mapped_column(String(80))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    checked_in_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    checked_in_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    # The half the demo omitted. Nullable because a child currently in a room
    # has not been collected; that is a state, not missing data.
    checked_out_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    checked_out_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    # Who physically took the child. Free text on purpose: it is often a
    # grandparent who is not on the roster, and a name written down beats a
    # dropdown that cannot express the truth.
    collected_by: Mapped[Optional[str]] = mapped_column(String(160))

    def __repr__(self) -> str:
        state = "out" if self.checked_out_at else "in"
        return f"<Checkin person={self.person_id} {state}>"

    @property
    def is_present(self) -> bool:
        return self.checked_out_at is None

    def check_out(self, collected_by: str | None = None, user=None) -> None:
        """Record the collection. Never silently overwrite an earlier one."""
        if self.checked_out_at is not None:
            return
        self.checked_out_at = utcnow()
        self.collected_by = (collected_by or "").strip()[:160] or None
        self.checked_out_by_user_id = getattr(user, "id", None)

    @classmethod
    def get_for_church(cls, church_id: int, checkin_id: int) -> "Checkin | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == checkin_id, cls.church_id == church_id)
        )

    @classmethod
    def by_pickup_code(cls, church_id: int, session_id: int, code: str) -> list["Checkin"]:
        """Everyone a code collects. Siblings share one, so this is a list."""
        from app.pickup import normalize

        code = normalize(code)
        if not code:
            return []
        return list(
            db.session.scalars(
                db.select(cls)
                .where(
                    cls.church_id == church_id,
                    cls.session_id == session_id,
                    cls.pickup_code == code,
                )
                .order_by(cls.id)
            )
        )

    @classmethod
    def history_for_person(cls, church_id: int, person_id: int, limit: int = 20):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.checked_in_at.desc())
            .limit(limit)
        )

    @classmethod
    def still_present(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id))
            .join(CheckinSession, CheckinSession.id == cls.session_id)
            .where(
                cls.church_id == church_id,
                cls.checked_out_at.is_(None),
                CheckinSession.is_open.is_(True),
            )
        ) or 0
