"""Contact and next steps.

Two tables and one denormalized column, and the column is the interesting part.

`Person.last_contact_at` duplicates `MAX(contact_log.occurred_at)`. That is a
deliberate denormalization, because the question the dashboard asks on every
load is "who has nobody talked to", and answering it with a join and a group by
means the database cannot use an index to skip anyone. With the column, the
whole question is a range scan on `(church_id, stage, stage_since)` plus a
comparison, and it stays fast at ten thousand people as easily as at fifty.

Denormalized data drifts. `flask recompute-contact` rebuilds the column from
the log, and a test asserts the two agree, so drift is detectable and fixable
rather than permanent.
"""

from __future__ import annotations

from typing import Optional

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# How the contact happened. A note is not contact: writing "should call Marcus"
# in the timeline must not clear a flag that says nobody has called Marcus.
CONTACT_METHODS = ("call", "text", "email", "in_person", "other")

METHOD_LABELS = {
    "call": "Phone call",
    "text": "Text message",
    "email": "Email",
    "in_person": "In person",
    "other": "Other",
}

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_DROPPED = "dropped"
NEXT_STEP_STATUSES = (STATUS_OPEN, STATUS_DONE, STATUS_DROPPED)

STATUS_LABELS = {
    STATUS_OPEN: "Open",
    STATUS_DONE: "Done",
    STATUS_DROPPED: "Dropped",
}

_METHOD_LIST = ", ".join(f"'{m}'" for m in CONTACT_METHODS)
_STATUS_LIST = ", ".join(f"'{s}'" for s in NEXT_STEP_STATUSES)


class ContactLog(TenantScoped, TimestampMixin, db.Model):
    """A record that a human actually talked to a person.

    This is the hard stop referenced in the architecture rules. An automated
    sequence stops when a human logs real contact, and "real" is what this
    table means: someone picked up a phone, sent a text, or sat down with them.
    """

    __tablename__ = "contact_log"
    __table_args__ = (
        CheckConstraint(f"method IN ({_METHOD_LIST})", name="ck_contact_log_method"),
        Index("ix_contact_church_person_time", "church_id", "person_id", "occurred_at"),
        Index("ix_contact_church_time", "church_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship(back_populates="contacts")  # noqa: F821

    method: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    logged_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    logged_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<ContactLog {self.method} person={self.person_id}>"

    @property
    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method.replace("_", " ").title())

    @classmethod
    def for_person(cls, church_id: int, person_id: int, limit: int = 25):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.occurred_at.desc(), cls.id.desc())
            .limit(limit)
        )


class NextStep(TenantScoped, TimestampMixin, db.Model):
    """One assigned action, owned by a named person.

    An unassigned next step is a wish. The owner is what turns "someone should
    call Marcus" into a thing that is either done or visibly not done.
    """

    __tablename__ = "next_step"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_next_step_status"),
        Index("ix_step_church_status_due", "church_id", "status", "due_on"),
        Index("ix_step_church_owner_status", "church_id", "owner_user_id", "status"),
        Index("ix_step_church_person_status", "church_id", "person_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship(back_populates="next_steps")  # noqa: F821

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    owner_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    owner_name: Mapped[Optional[str]] = mapped_column(String(120))

    due_on: Mapped[Optional[date]] = mapped_column(Date)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_OPEN)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    def __repr__(self) -> str:
        return f"<NextStep {self.title!r} {self.status} person={self.person_id}>"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    @property
    def is_overdue(self) -> bool:
        from datetime import date as _date

        return bool(self.is_open and self.due_on and self.due_on < _date.today())

    def close(self, status: str = STATUS_DONE) -> None:
        self.status = status
        self.completed_at = utcnow()

    @classmethod
    def open_for_person(cls, church_id: int, person_id: int):
        return (
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.status == STATUS_OPEN,
            )
            .order_by(cls.due_on.is_(None), cls.due_on, cls.id)
        )

    @classmethod
    def all_for_person(cls, church_id: int, person_id: int, limit: int = 25):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.created_at.desc(), cls.id.desc())
            .limit(limit)
        )
