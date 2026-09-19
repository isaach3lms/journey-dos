"""The audit surface.

**Append only.** There is no update path and no delete route, and there never
should be. A log a leader can edit is not a log; it is a story. The whole value
of this table is that a church can answer "who did that, and when" six months
later without anybody's memory being involved.

**Never contains a secret.** Not an API key, not a password, not a reset token,
not a pickup code. A test asserts this over every recorded action, because the
natural instinct when writing an audit entry is to include the thing that
changed, and here that instinct is exactly wrong.

**Deliberately not a log of everything.** A record of every page view is a
record nobody reads, and it buries the twelve entries that matter under
thousands that do not. What is recorded is the set of actions that change who
can do what, move money, touch a child's record, or remove something somebody
else wrote.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime, timedelta

from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# The actions worth keeping. Adding one is a constant plus the call site.
SIGN_IN = "sign_in"
SIGN_IN_FAILED = "sign_in_failed"
PASSWORD_RESET = "password_reset"
ROLE_CHANGED = "role_changed"
BRAND_CHANGED = "brand_changed"
CREDENTIAL_CHANGED = "credential_changed"
GIFT_MATCHED = "gift_matched"
MESSAGE_DELETED = "message_deleted"
CHAT_DELETED = "chat_deleted"
MESSAGE_REPORTED = "message_reported"
PERSON_BLOCKED = "person_blocked"
REPORT_RESOLVED = "report_resolved"
CHILD_CHECKED_OUT = "child_checked_out"
PIN_ROTATED = "pin_rotated"
SEQUENCE_STOPPED = "sequence_stopped"
PERSON_ARCHIVED = "person_archived"

ACTIONS = (
    SIGN_IN,
    SIGN_IN_FAILED,
    PASSWORD_RESET,
    ROLE_CHANGED,
    BRAND_CHANGED,
    CREDENTIAL_CHANGED,
    GIFT_MATCHED,
    MESSAGE_DELETED,
    CHAT_DELETED,
    MESSAGE_REPORTED,
    PERSON_BLOCKED,
    REPORT_RESOLVED,
    CHILD_CHECKED_OUT,
    PIN_ROTATED,
    SEQUENCE_STOPPED,
    PERSON_ARCHIVED,
)

ACTION_LABELS = {
    SIGN_IN: "Signed in",
    SIGN_IN_FAILED: "Failed sign-in",
    PASSWORD_RESET: "Password reset",
    ROLE_CHANGED: "Access changed",
    BRAND_CHANGED: "Branding changed",
    CREDENTIAL_CHANGED: "Provider keys changed",
    GIFT_MATCHED: "Gift matched to a person",
    MESSAGE_DELETED: "Message deleted",
    CHAT_DELETED: "Chat deleted",
    MESSAGE_REPORTED: "Message reported",
    PERSON_BLOCKED: "Person blocked",
    REPORT_RESOLVED: "Report decided",
    CHILD_CHECKED_OUT: "Child collected",
    PIN_ROTATED: "Check-in code rotated",
    SEQUENCE_STOPPED: "Sequence stopped by staff",
    PERSON_ARCHIVED: "Person archived",
}

# How long entries are kept. Long enough to answer a question about last
# season, short enough that the table is not a liability nobody manages.
RETENTION_DAYS = 400


class AuditEvent(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_church_time", "church_id", "occurred_at"),
        Index("ix_audit_church_action", "church_id", "action", "occurred_at"),
        Index("ix_audit_church_actor", "church_id", "actor_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    action: Mapped[str] = mapped_column(String(40), nullable=False)

    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    # Copied, so an entry still names who did it after the account is removed.
    # "Someone changed the giving keys" is not an audit trail.
    actor_name: Mapped[Optional[str]] = mapped_column(String(120))

    # What it happened to, as loose text rather than a polymorphic foreign key.
    # A hard link would mean deleting a person erases the record that they were
    # archived, which is the opposite of the point.
    subject_type: Mapped[Optional[str]] = mapped_column(String(40))
    subject_id: Mapped[Optional[int]] = mapped_column()
    subject_label: Mapped[Optional[str]] = mapped_column(String(160))

    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<AuditEvent {self.action} by={self.actor_name!r}>"

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action.replace("_", " ").title())

    @classmethod
    def recent(cls, church_id: int, action: str | None = None, limit: int = 100):
        query = db.select(cls).where(cls.church_id == church_id)
        if action:
            query = query.where(cls.action == action)
        return query.order_by(cls.occurred_at.desc(), cls.id.desc()).limit(limit)

    @classmethod
    def count_since(cls, church_id: int, days: int = 30) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.occurred_at >= utcnow() - timedelta(days=days),
            )
        ) or 0

    @classmethod
    def purge_old(cls, older_than_days: int = RETENTION_DAYS) -> int:
        result = db.session.execute(
            db.delete(cls).where(
                cls.occurred_at < utcnow() - timedelta(days=older_than_days)
            )
        )
        return result.rowcount
