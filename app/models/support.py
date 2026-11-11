"""Somebody asking their church for help.

The most sensitive row in this system. A person writes it about themselves,
often on the worst week of their year, and it is the one thing here that
cannot be allowed to sit unread.

Three rules follow from that, and they are enforced here rather than left to
each screen:

1. **The words stay in the app.** What somebody wrote is never put in an
   email. Staff are told a request exists and given a link. A copy in forty
   inboxes cannot be taken back, and a pastoral request forwarded by accident
   is the kind of harm a church does not recover from.
2. **It is closed by a person, not by time.** There is no auto-expiry. An
   open request stays on the dashboard until somebody says they have dealt
   with it and their name is recorded against that.
3. **It is never deleted when it is answered.** Answering sets a status. The
   record of who asked, and who replied, is what a church needs if a question
   is ever raised about whether somebody was looked after.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# What somebody is asking for. Ordered by how fast it needs a person, which
# is also the order they are shown in on the dashboard.
KIND_URGENT = "urgent"
KIND_VISIT = "visit"
KIND_TALK = "talk"
KIND_PRAYER = "prayer"
KIND_OTHER = "other"

SUPPORT_KINDS = (KIND_URGENT, KIND_VISIT, KIND_TALK, KIND_PRAYER, KIND_OTHER)

KIND_LABELS = {
    KIND_URGENT: "Something urgent",
    KIND_VISIT: "A visit",
    KIND_TALK: "A conversation",
    KIND_PRAYER: "Prayer",
    KIND_OTHER: "Something else",
}

# How they want to be reached. "Either" is the default because most people
# do not mind, and forcing a choice on this screen is friction at the worst
# possible moment.
CONTACT_EITHER = "either"
CONTACT_PHONE = "phone"
CONTACT_EMAIL = "email"
CONTACT_CHOICES = (CONTACT_EITHER, CONTACT_PHONE, CONTACT_EMAIL)

CONTACT_LABELS = {
    CONTACT_EITHER: "Either is fine",
    CONTACT_PHONE: "A phone call",
    CONTACT_EMAIL: "Email",
}

STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
SUPPORT_STATUSES = (STATUS_OPEN, STATUS_ANSWERED)

MAX_MESSAGE_LENGTH = 4000

_KIND_LIST = ", ".join(f"'{k}'" for k in SUPPORT_KINDS)
_CONTACT_LIST = ", ".join(f"'{c}'" for c in CONTACT_CHOICES)
_STATUS_LIST = ", ".join(f"'{s}'" for s in SUPPORT_STATUSES)


class SupportRequest(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "support_request"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_LIST})", name="ck_support_request_kind"),
        CheckConstraint(
            f"contact_pref IN ({_CONTACT_LIST})", name="ck_support_request_contact"
        ),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_support_request_status"),
        Index("ix_support_church_status", "church_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=KIND_TALK)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    contact_pref: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CONTACT_EITHER
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_OPEN)
    answered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    answered_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    # Copied, so the record still names who dealt with it after an account is
    # removed. "Someone answered it" is not an answer.
    answered_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<SupportRequest {self.kind} {self.status} person={self.person_id}>"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.title())

    @property
    def contact_label(self) -> str:
        return CONTACT_LABELS.get(self.contact_pref, self.contact_pref.title())

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    @property
    def is_urgent(self) -> bool:
        return self.kind == KIND_URGENT

    @property
    def summary_line(self) -> str:
        """The first line, for a list. Never the whole thing.

        A dashboard is a screen people read over each other's shoulders in an
        office. The full message is on the person's own page.
        """
        first = (self.message or "").strip().splitlines()[0] if self.message else ""
        return first if len(first) <= 90 else first[:87].rstrip() + "..."

    def days_waiting(self, church) -> int:
        """Whole days since they asked, on the church's own calendar.

        Both sides have to be the same clock. Comparing the stored UTC date
        against the church's local date printed "Waiting -1 days" for
        anything asked late in the evening in Missouri.
        """
        from app.timeutil import now_local, to_local

        asked = to_local(self.created_at, church).date() if self.created_at else None
        if asked is None:
            return 0
        return max(0, (now_local(church).date() - asked).days)

    def answer(self, user) -> bool:
        """Mark it dealt with. False if somebody already had."""
        if self.status == STATUS_ANSWERED:
            return False
        self.status = STATUS_ANSWERED
        self.answered_at = utcnow()
        self.answered_by_user_id = getattr(user, "id", None)
        self.answered_by_name = getattr(user, "name", None)
        return True

    def reopen(self) -> None:
        self.status = STATUS_OPEN
        self.answered_at = None
        self.answered_by_user_id = None
        self.answered_by_name = None

    # -- lookups, all tenant scoped -----------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, request_id: int) -> "SupportRequest | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == request_id, cls.church_id == church_id)
        )

    @classmethod
    def open_for_church(cls, church_id: int, limit: int | None = None):
        """Urgent first, then oldest first: the one waiting longest is the
        one a church is most at risk of having forgotten."""
        query = (
            db.select(cls)
            .where(cls.church_id == church_id, cls.status == STATUS_OPEN)
            .order_by((cls.kind != KIND_URGENT), cls.created_at)
        )
        return query.limit(limit) if limit else query

    @classmethod
    def open_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return int(db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == STATUS_OPEN
            )
        ) or 0)

    @classmethod
    def answered_for_church(cls, church_id: int, limit: int = 100):
        """What has already been dealt with, most recently answered first.

        Kept rather than cleared: a church asked what somebody asked for last
        spring is a question the log has to be able to answer.
        """
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.status == STATUS_ANSWERED)
            .order_by(cls.answered_at.desc().nullslast(), cls.created_at.desc())
            .limit(limit)
        )

    @classmethod
    def answered_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return int(db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == STATUS_ANSWERED
            )
        ) or 0)

    @classmethod
    def for_person(cls, church_id: int, person_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.created_at.desc())
        )

    @classmethod
    def open_for_person(cls, church_id: int, person_id: int) -> "SupportRequest | None":
        """At most one open request per person at a time.

        A second ask while the first is open is the same conversation, not a
        second one, and two rows would have staff calling twice about one
        thing.
        """
        return db.session.scalar(
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.status == STATUS_OPEN,
            )
            .order_by(cls.created_at.desc())
        )
