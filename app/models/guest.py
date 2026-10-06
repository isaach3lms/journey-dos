"""A connect card, as the guest filled it in.

Kept as its own row rather than folded into the person it creates, because
the two answer different questions. The roster record answers "who is this
and where are they up to". The card answers "what did they write on the day",
and most of that has nowhere to live on a person: how they heard about the
church, whether they asked to be contacted, and whatever they wrote in the
box. Flattening it into a note on the person would lose the structure the
moment anybody wanted to count how guests are hearing about the church.

**The card is never rewritten.** It is what somebody submitted, and that is
its whole value. Staff correct the roster record; the card keeps saying what
was handed over. The one thing that changes is a link to the person it
became, and a discard flag for the rubbish that any public form eventually
collects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# How somebody found the church. A tuple rather than a table: adding one is a
# deploy, not a migration, and the counts only mean anything if the options
# stay stable across a year.
HEARD_FRIEND = "friend"
HEARD_FAMILY = "family"
HEARD_ONLINE = "online"
HEARD_SOCIAL = "social"
HEARD_DROVE_PAST = "drove_past"
HEARD_EVENT = "event"
HEARD_OTHER = "other"

HEARD_CHOICES = (
    HEARD_FRIEND, HEARD_FAMILY, HEARD_ONLINE, HEARD_SOCIAL,
    HEARD_DROVE_PAST, HEARD_EVENT, HEARD_OTHER,
)

HEARD_LABELS = {
    HEARD_FRIEND: "A friend invited me",
    HEARD_FAMILY: "Family",
    HEARD_ONLINE: "Found you online",
    HEARD_SOCIAL: "Social media",
    HEARD_DROVE_PAST: "Drove past",
    HEARD_EVENT: "An event",
    HEARD_OTHER: "Something else",
}


class GuestCard(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "guest_card"
    __table_args__ = (
        # The only query this table has: one church's cards, newest first,
        # with the discarded ones out of the way.
        Index("ix_guest_church_time", "church_id", "discarded_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Nullable and SET NULL rather than CASCADE. A person deleted from the
    # roster should not take the record of their visit with them: the church
    # still had a guest that Sunday, and the dashboard number for that week
    # should not change retrospectively.
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    person: Mapped[Optional["Person"]] = relationship()  # noqa: F821

    # Copied, not read through the person. The card says what was written on
    # it, and a name corrected on the roster next week must not rewrite what
    # somebody handed over on Sunday.
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(40))

    heard: Mapped[Optional[str]] = mapped_column(String(20))
    note: Mapped[Optional[str]] = mapped_column(Text)
    wants_contact: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )

    # Whether this card created a roster record or matched one that existed.
    # Worth keeping: "we had 27 guests" and "27 people we had never met" are
    # different numbers and a church will eventually ask for the second.
    is_new_person: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )

    discarded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    discarded_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def heard_label(self) -> str | None:
        return HEARD_LABELS.get(self.heard or "")

    @property
    def is_discarded(self) -> bool:
        return self.discarded_at is not None

    def discard(self, actor=None) -> bool:
        """Mark this card as rubbish. Caller commits.

        Not a delete. A public form collects junk, and a church that cannot
        clear it stops reading the list, but a row that vanishes also takes
        with it any evidence of what a form was being used for.
        """
        if self.discarded_at is not None:
            return False
        self.discarded_at = utcnow()
        self.discarded_by_name = getattr(actor, "name", None)
        return True

    def restore(self) -> bool:
        """Discarded by mistake. Caller commits."""
        if self.discarded_at is None:
            return False
        self.discarded_at = None
        self.discarded_by_name = None
        return True

    @classmethod
    def for_church(cls, church_id: int, *, include_discarded: bool = False):
        query = db.select(cls).where(cls.church_id == church_id)
        if not include_discarded:
            query = query.where(cls.discarded_at.is_(None))
        return query.order_by(cls.created_at.desc(), cls.id.desc())

    @classmethod
    def get_for_church(cls, church_id: int, card_id: int) -> "GuestCard | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == card_id, cls.church_id == church_id)
        )

    @classmethod
    def waiting_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.discarded_at.is_(None),
            )
        ) or 0

    def __repr__(self) -> str:
        return f"<GuestCard {self.full_name!r} church={self.church_id}>"
