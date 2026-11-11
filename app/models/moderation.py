"""Reporting messages and blocking people.

Required by App Store Guideline 1.2 for any app where people post content
other people read, and right regardless: a church chat room is a place where
somebody can be unkind to somebody else, and the person on the receiving end
needs a way to make it stop that does not depend on them finding a staff
member on a Sunday.

Two rules shape this.

**A block acts immediately and needs nobody's permission.** The person who
blocked stops seeing that author's messages the moment they tap it. Staff are
told, because blocking usually means something happened, but the protection
does not wait for them.

**A report is a request for a human, not an automatic takedown.** One report
does not delete a message, because in a room of twelve people one unhappy
reader could silence anybody. Staff see it, decide, and the decision is
audited.

Neither table stores the words of the message. A report points at the message,
and if staff remove it the words are cleared from the one place they lived.
Copying them into a report table would keep abusive text around after the
church decided to delete it.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
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

REPORT_OPEN = "open"
REPORT_REMOVED = "removed"
REPORT_DISMISSED = "dismissed"
REPORT_STATUSES = (REPORT_OPEN, REPORT_REMOVED, REPORT_DISMISSED)

REPORT_LABELS = {
    REPORT_OPEN: "Waiting for a decision",
    REPORT_REMOVED: "Message removed",
    REPORT_DISMISSED: "Kept, no action",
}

# Where the report came from. A block files one too, because blocking usually
# means something happened, and staff should know even when the person did not
# think to report it separately.
SOURCE_REPORT = "report"
SOURCE_BLOCK = "block"

_STATUS_LIST = ", ".join(f"'{s}'" for s in REPORT_STATUSES)


class MessageReport(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "message_report"
    __table_args__ = (
        # One report per person per message. Tapping it twice is not two
        # complaints, and counting it as two would let one person make a
        # message look more contested than it is.
        UniqueConstraint(
            "message_id", "reporter_person_id", name="uq_message_report_reporter"
        ),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_message_report_status"),
        Index("ix_report_church_status", "church_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    message_id: Mapped[int] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message: Mapped["Message"] = relationship()  # noqa: F821

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )

    reporter_person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL")
    )
    # Copied so the report still makes sense after an account is removed.
    reporter_name: Mapped[Optional[str]] = mapped_column(String(120))

    # Optional, and short. The person reporting should not have to write an
    # essay to be taken seriously.
    reason: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_REPORT)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=REPORT_OPEN)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    resolved_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    resolved_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<MessageReport message={self.message_id} {self.status}>"

    @property
    def is_open(self) -> bool:
        return self.status == REPORT_OPEN

    @property
    def status_label(self) -> str:
        return REPORT_LABELS.get(self.status, self.status)

    def resolve(self, status: str, user) -> None:
        self.status = status
        self.resolved_at = utcnow()
        self.resolved_by_user_id = getattr(user, "id", None)
        self.resolved_by_name = getattr(user, "name", None)

    # -- lookups ------------------------------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, report_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == report_id, cls.church_id == church_id)
        )

    @classmethod
    def open_for_church(cls, church_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.status == REPORT_OPEN)
            .order_by(cls.created_at)
        )

    @classmethod
    def recent_closed(cls, church_id: int, limit: int = 20):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.status != REPORT_OPEN)
            .order_by(cls.resolved_at.desc())
            .limit(limit)
        )

    @classmethod
    def open_count(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == REPORT_OPEN
            )
        ) or 0

    @classmethod
    def file(cls, message, reporter, reason: str | None = None,
             source: str = SOURCE_REPORT) -> tuple["MessageReport", bool]:
        """Record a report, or return the one this person already filed.

        Returns (report, created). Caller commits.
        """
        existing = db.session.scalar(
            db.select(cls).where(
                cls.message_id == message.id,
                cls.reporter_person_id == reporter.id,
            )
        )
        if existing is not None:
            # A second tap, or a block after a report. Reopen it if staff had
            # already dismissed it, because the person is telling us again.
            if reason and not existing.reason:
                existing.reason = reason[:1000]
            if not existing.is_open:
                existing.status = REPORT_OPEN
                existing.resolved_at = None
            return existing, False

        report = cls(
            church_id=message.church_id,
            message_id=message.id,
            conversation_id=message.conversation_id,
            reporter_person_id=reporter.id,
            reporter_name=reporter.full_name,
            reason=(reason or "").strip()[:1000] or None,
            source=source,
        )
        db.session.add(report)
        return report, True


class PersonBlock(TenantScoped, TimestampMixin, db.Model):
    """One person choosing not to see another person's messages."""

    __tablename__ = "person_block"
    __table_args__ = (
        UniqueConstraint("blocker_person_id", "blocked_person_id", name="uq_person_block_pair"),
        CheckConstraint(
            "blocker_person_id != blocked_person_id", name="ck_person_block_not_self"
        ),
        Index("ix_block_church_blocker", "church_id", "blocker_person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    blocker_person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    blocked_person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    blocked: Mapped["Person"] = relationship(foreign_keys=[blocked_person_id])  # noqa: F821

    # Copied so the list on the You screen still reads after the other person
    # is archived.
    blocked_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<PersonBlock {self.blocker_person_id} -> {self.blocked_person_id}>"

    @classmethod
    def blocked_ids(cls, church_id: int, blocker_person_id: int) -> set[int]:
        return set(
            db.session.scalars(
                db.select(cls.blocked_person_id).where(
                    cls.church_id == church_id,
                    cls.blocker_person_id == blocker_person_id,
                )
            )
        )

    @classmethod
    def for_blocker(cls, church_id: int, blocker_person_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.blocker_person_id == blocker_person_id)
            .order_by(cls.blocked_name)
        )

    @classmethod
    def get_for_blocker(cls, church_id: int, blocker_person_id: int, block_id: int):
        """Scoped to the person asking, so an id cannot remove somebody
        else's block."""
        return db.session.scalar(
            db.select(cls).where(
                cls.id == block_id,
                cls.church_id == church_id,
                cls.blocker_person_id == blocker_person_id,
            )
        )

    @classmethod
    def add(cls, blocker, blocked) -> tuple["PersonBlock", bool]:
        existing = db.session.scalar(
            db.select(cls).where(
                cls.blocker_person_id == blocker.id,
                cls.blocked_person_id == blocked.id,
            )
        )
        if existing is not None:
            return existing, False
        block = cls(
            church_id=blocker.church_id,
            blocker_person_id=blocker.id,
            blocked_person_id=blocked.id,
            blocked_name=blocked.full_name,
        )
        db.session.add(block)
        return block, True
