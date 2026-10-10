"""The push queue.

The same rule the outbox was built on, finally applied to the other channel:
**nothing in this system calls a third party inside a web request.**

Push was the exception, and the exception cost a church a thread with the
same message in it six times. Posting to a room pushed every device belonging
to every person in it, one HTTPS call at a time, each with a ten second
timeout, all before the page came back. The send button sat there looking
untouched, so it got pressed again.

So push now queues a row and a worker sends it, exactly as email does. The
differences from the outbox are all consequences of what a notification is:

**A late notification is worse than none.** An email about Sunday's plan is
still useful on Monday. A phone buzzing at four o'clock about a message sent
at three is noise somebody turns notifications off to stop. Rows older than
`STALE_MINUTES` are dropped as expired rather than sent, and that is a
success, not a failure.

**Fewer retries.** Three, not five, for the same reason. By the fifth attempt
nobody wants it.

**No CHECK on the category.** The outbox has one, which means adding a
notification category is a migration that rebuilds a constraint on two tables,
and forgetting it is a bug that only appears in production on Postgres. A
third table on that ritual buys very little here: the category is validated in
Python before anything is queued, and these rows are drained and purged within
the hour rather than kept as a record. Retiring a category should not require
a migration to let the last few rows finish.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

PUSH_QUEUED = "queued"
PUSH_SENT = "sent"
PUSH_FAILED = "failed"
PUSH_SUPPRESSED = "suppressed"
PUSH_EXPIRED = "expired"

PUSH_STATUSES = (
    PUSH_QUEUED,
    PUSH_SENT,
    PUSH_FAILED,
    PUSH_SUPPRESSED,
    PUSH_EXPIRED,
)

PUSH_STATUS_LABELS = {
    PUSH_QUEUED: "Waiting",
    PUSH_SENT: "Sent",
    PUSH_FAILED: "Failed",
    PUSH_SUPPRESSED: "Not sent, opted out",
    PUSH_EXPIRED: "Too old to send",
}

# Three, not the outbox's five. See the module docstring.
PUSH_MAX_ATTEMPTS = 3

# Past this, a notification is noise rather than news.
STALE_MINUTES = 30

_PUSH_STATUS_LIST = ", ".join(f"'{s}'" for s in PUSH_STATUSES)


class PushQueueItem(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "push_queue"
    __table_args__ = (
        CheckConstraint(f"status IN ({_PUSH_STATUS_LIST})", name="ck_push_queue_status"),
        # Idempotent queuing, the same guarantee the outbox gives. A retried
        # request queues one notification, not two.
        UniqueConstraint("church_id", "dedupe_key", name="uq_push_queue_dedupe_key"),
        # The worker's only query: what is waiting, oldest first.
        Index("ix_push_queue_status_queued", "status", "queued_at"),
        Index("ix_push_queue_church_status", "church_id", "status", "queued_at"),
        Index("ix_push_queue_claim", "claim_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # CASCADE rather than SET NULL. A push is to a person; with no person
    # there is nobody to notify, and an orphan row would sit queued until the
    # worker threw it away anyway.
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    category: Mapped[str] = mapped_column(String(40), nullable=False)

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="/")
    # A tag replaces rather than stacks on the lock screen.
    tag: Mapped[Optional[str]] = mapped_column(String(80))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PUSH_QUEUED)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200))

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    # How many of this person's devices took it. Zero and sent is normal: a
    # subscription can expire between queuing and sending.
    devices: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    queued_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    claim_token: Mapped[Optional[str]] = mapped_column(String(64))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<PushQueueItem {self.status} person={self.person_id} {self.category}>"

    @property
    def status_label(self) -> str:
        return PUSH_STATUS_LABELS.get(self.status, self.status.title())

    def is_stale(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return (now - self.queued_at) > timedelta(minutes=STALE_MINUTES)

    def mark_sent(self, devices: int = 0) -> None:
        self.status = PUSH_SENT
        self.sent_at = utcnow()
        self.devices = devices
        self.last_error = None
        self.claim_token = None

    def mark_failed(self, error: str) -> None:
        self.attempts += 1
        self.last_error = (error or "")[:2000]
        self.claim_token = None
        self.status = PUSH_QUEUED if self.attempts < PUSH_MAX_ATTEMPTS else PUSH_FAILED

    def mark_suppressed(self, reason: str) -> None:
        self.status = PUSH_SUPPRESSED
        self.last_error = reason[:2000]
        self.claim_token = None

    def mark_expired(self) -> None:
        """Not a failure. The system decided this was too old to be worth
        somebody's attention, which is the system working."""
        self.status = PUSH_EXPIRED
        self.last_error = f"Older than {STALE_MINUTES} minutes when the worker reached it."
        self.claim_token = None

    @classmethod
    def queued_count(cls, church_id: int | None = None) -> int:
        statement = db.select(func.count(cls.id)).where(cls.status == PUSH_QUEUED)
        if church_id is not None:
            statement = statement.where(cls.church_id == church_id)
        return db.session.scalar(statement) or 0

    @classmethod
    def oldest_queued_at(cls, church_id: int | None = None):
        """The age of this tells you whether the worker is running.

        A queue depth on its own does not: ten waiting is healthy two seconds
        after a post and a dead worker ten minutes later.
        """
        statement = db.select(func.min(cls.queued_at)).where(cls.status == PUSH_QUEUED)
        if church_id is not None:
            statement = statement.where(cls.church_id == church_id)
        return db.session.scalar(statement)

    @classmethod
    def recent_for_church(cls, church_id: int, limit: int = 25):
        return (
            db.select(cls)
            .where(cls.church_id == church_id)
            .order_by(cls.created_at.desc(), cls.id.desc())
            .limit(limit)
        )
