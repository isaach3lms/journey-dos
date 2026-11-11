"""The outbox.

Nothing in this system sends email inside a web request. A request that calls
a third party API is as slow and as reliable as that API, and if the call fails
after the database has committed, the message is simply gone with nobody aware
of it. Queuing means the request commits a row, returns immediately, and a
worker does the sending where a failure is visible, retryable, and recorded.

Three properties matter more than the schema:

**Claiming is atomic.** A worker takes rows with a conditional UPDATE that
writes a claim token, then reads back only what it claimed. Two workers running
at once cannot claim the same row, because the second UPDATE matches nothing.
This works identically on SQLite and Postgres, unlike `FOR UPDATE SKIP LOCKED`.

**Suppression is checked at send time, not queue time.** Someone can
unsubscribe in the hour between a message being queued and being sent, and the
answer that matters is the one at the moment of sending.

**A dedupe key makes queuing idempotent.** "The welcome email for person 5"
can be queued twice by a retried request or a double-clicked button, and the
second attempt is a no-op rather than a second email.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.categories import CATEGORY_CODES
from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SUPPRESSED = "suppressed"
STATUS_CANCELLED = "cancelled"

OUTBOX_STATUSES = (
    STATUS_QUEUED,
    STATUS_SENT,
    STATUS_FAILED,
    STATUS_SUPPRESSED,
    STATUS_CANCELLED,
)

STATUS_LABELS = {
    STATUS_QUEUED: "Queued",
    STATUS_SENT: "Sent",
    STATUS_FAILED: "Failed",
    STATUS_SUPPRESSED: "Not sent, opted out",
    STATUS_CANCELLED: "Cancelled",
}

# After this many attempts a message stops being retried. It stays in the
# table as `failed` with its last error, because silently dropping mail is how
# a church finds out in March that nobody got the February newsletter.
MAX_ATTEMPTS = 5

_STATUS_LIST = ", ".join(f"'{s}'" for s in OUTBOX_STATUSES)
_CATEGORY_LIST = ", ".join(f"'{c}'" for c in CATEGORY_CODES)


class OutboxMessage(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "outbox_message"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_outbox_message_status"),
        CheckConstraint(
            f"category IN ({_CATEGORY_LIST})", name="ck_outbox_message_category"
        ),
        # Idempotent queuing. Two attempts to queue the same logical message
        # collapse into one row.
        UniqueConstraint("church_id", "dedupe_key", name="uq_outbox_message_dedupe_key"),
        # The worker's only query: what is queued, oldest first.
        Index("ix_outbox_status_queued", "status", "queued_at"),
        Index("ix_outbox_church_status", "church_id", "status", "queued_at"),
        Index("ix_outbox_church_person", "church_id", "person_id", "created_at"),
        Index("ix_outbox_claim", "claim_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Nullable: not every message is to someone on the roster.
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    person: Mapped[Optional["Person"]] = relationship()  # noqa: F821

    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    to_name: Mapped[Optional[str]] = mapped_column(String(120))

    category: Mapped[str] = mapped_column(String(40), nullable=False)

    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_QUEUED
    )

    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200))

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(200))

    queued_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    claim_token: Mapped[Optional[str]] = mapped_column(String(64))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    def __repr__(self) -> str:
        return f"<OutboxMessage {self.status} {self.to_email} {self.category}>"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def is_retryable(self) -> bool:
        return self.status == STATUS_FAILED and self.attempts < MAX_ATTEMPTS

    def mark_sent(self, provider_message_id: str | None = None) -> None:
        self.status = STATUS_SENT
        self.sent_at = utcnow()
        self.provider_message_id = provider_message_id
        self.last_error = None
        self.claim_token = None

    def mark_failed(self, error: str) -> None:
        self.attempts += 1
        self.last_error = (error or "")[:2000]
        self.claim_token = None
        # Back to the queue unless it has run out of attempts.
        self.status = STATUS_QUEUED if self.attempts < MAX_ATTEMPTS else STATUS_FAILED

    def mark_suppressed(self, reason: str) -> None:
        self.status = STATUS_SUPPRESSED
        self.last_error = reason[:2000]
        self.claim_token = None

    @classmethod
    def for_person(cls, church_id: int, person_id: int, limit: int = 25):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.created_at.desc(), cls.id.desc())
            .limit(limit)
        )

    @classmethod
    def queued_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == STATUS_QUEUED
            )
        ) or 0


class NotificationPreference(TenantScoped, TimestampMixin, db.Model):
    """One row per person per category, written only when they change it.

    Absence means the category default applies. Storing a row for every person
    and every category would mean a migration every time a category is added,
    which is exactly what keeping categories in Python is meant to avoid.
    """

    __tablename__ = "notification_preference"
    __table_args__ = (
        CheckConstraint(
            f"category IN ({_CATEGORY_LIST})",
            name="ck_notification_preference_category",
        ),
        UniqueConstraint(
            "church_id", "person_id", "category", name="uq_notification_preference_person_id"
        ),
        Index("ix_pref_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship(  # noqa: F821
        back_populates="notification_preferences"
    )

    category: Mapped[str] = mapped_column(String(40), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        state = "on" if self.allowed else "off"
        return f"<NotificationPreference {self.category}={state} person={self.person_id}>"
