"""Enrollment in a sequence.

The sequence itself is Python. This row is one person's position in it, plus
the reason it ended, which is the part a pastor actually reads: "stopped
because Dana called her" is a better sentence than "completed".

`step_index` rather than a copy of the step: a sequence edited between sends
should change what the next email says, because the newer wording is the one
the church decided on. If a sequence gets shorter, an enrollment past the end
completes rather than erroring.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime, timedelta

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
ENROLLMENT_STATUSES = (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_STOPPED)

# Why a sequence ended. These are shown to staff, so they read as sentences.
REASON_CONTACT = "contact"
REASON_TARGET_STAGE = "target_stage"
REASON_STAGE_LEFT = "stage_left"
REASON_FINISHED = "finished"
REASON_OPTED_OUT = "opted_out"
REASON_MANUAL = "manual"
REASON_NO_EMAIL = "no_email"

REASON_LABELS = {
    REASON_CONTACT: "Someone talked to them",
    REASON_TARGET_STAGE: "They got where it was pointing",
    REASON_STAGE_LEFT: "They moved on to another stage",
    REASON_FINISHED: "Ran to the end",
    REASON_OPTED_OUT: "They turned this kind of email off",
    REASON_MANUAL: "Stopped by staff",
    REASON_NO_EMAIL: "No email address on file",
}

_STATUS_LIST = ", ".join(f"'{s}'" for s in ENROLLMENT_STATUSES)


class SequenceEnrollment(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "sequence_enrollment"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_STATUS_LIST})", name="ck_sequence_enrollment_status"
        ),
        # One live enrollment per person per sequence. Without this, moving
        # back and forth across a stage boundary starts the welcome series
        # again every time.
        UniqueConstraint(
            "person_id", "sequence_code", name="uq_sequence_enrollment_sequence_code"
        ),
        Index("ix_enrollment_due", "church_id", "status", "next_due_at"),
        Index("ix_enrollment_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    sequence_code: Mapped[str] = mapped_column(String(60), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_ACTIVE
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    enrolled_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    next_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    steps_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    end_reason: Mapped[Optional[str]] = mapped_column(String(40))

    def __repr__(self) -> str:
        return f"<SequenceEnrollment {self.sequence_code} person={self.person_id} {self.status}>"

    @property
    def sequence(self):
        from app.sequences import get

        return get(self.sequence_code)

    @property
    def sequence_name(self) -> str:
        sequence = self.sequence
        return sequence.name if sequence else self.sequence_code

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    @property
    def end_reason_label(self) -> str | None:
        if not self.end_reason:
            return None
        return REASON_LABELS.get(self.end_reason, self.end_reason.replace("_", " "))

    def schedule_next(self) -> None:
        """Work out when the current step is due, or finish.

        Offsets are from enrollment, not from the last send, so a worker that
        was down for a day does not push the whole series a day later.
        """
        sequence = self.sequence
        step = sequence.step_at(self.step_index) if sequence else None
        if step is None:
            self.complete(REASON_FINISHED)
            return
        self.next_due_at = self.enrolled_at + timedelta(days=step.day)

    def advance(self) -> None:
        self.step_index += 1
        self.steps_sent += 1
        self.last_sent_at = utcnow()
        self.schedule_next()

    def complete(self, reason: str = REASON_FINISHED) -> None:
        self.status = STATUS_COMPLETED
        self.ended_at = utcnow()
        self.end_reason = reason
        self.next_due_at = None

    def stop(self, reason: str) -> None:
        """Ended early. The reason is what staff read, so it is recorded."""
        self.status = STATUS_STOPPED
        self.ended_at = utcnow()
        self.end_reason = reason
        self.next_due_at = None

    # -- lookups ------------------------------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, enrollment_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == enrollment_id, cls.church_id == church_id)
        )

    @classmethod
    def active_for_person(cls, church_id: int, person_id: int):
        return db.select(cls).where(
            cls.church_id == church_id,
            cls.person_id == person_id,
            cls.status == STATUS_ACTIVE,
        )

    @classmethod
    def for_person(cls, church_id: int, person_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.enrolled_at.desc())
        )

    @classmethod
    def due(cls, church_id: int | None = None, limit: int = 200):
        query = (
            db.select(cls)
            .where(
                cls.status == STATUS_ACTIVE,
                cls.next_due_at.is_not(None),
                cls.next_due_at <= utcnow(),
            )
            .order_by(cls.next_due_at)
            .limit(limit)
        )
        if church_id is not None:
            query = query.where(cls.church_id == church_id)
        return query

    @classmethod
    def active_count(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == STATUS_ACTIVE
            )
        ) or 0

    @classmethod
    def stopped_by_contact_count(cls, church_id: int) -> int:
        """The number worth showing a pastor.

        It is the count of times the system stepped back because a human
        stepped in, which is the behaviour the whole feature is for.
        """
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.end_reason == REASON_CONTACT
            )
        ) or 0

    @classmethod
    def sent_last_days(cls, church_id: int, days: int = 7) -> int:
        return db.session.scalar(
            db.select(func.coalesce(func.sum(cls.steps_sent), 0)).where(
                cls.church_id == church_id,
                cls.last_sent_at >= utcnow() - timedelta(days=days),
            )
        ) or 0
