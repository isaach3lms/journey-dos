"""Somebody tapping a tile to say they are interested in something.

**Its own row rather than a next step on their record**, and the distinction
is the whole design. A `NextStep` is what staff decided somebody should do
next: it has an owner, a due date, and a pastor behind it. This is a member
putting their hand up. Writing one straight into the other would mean the
"Your next step" card filled itself in the moment anybody tapped a tile, and
a card that populates itself stops being read.

So the flow is: member asks, staff hear about it, staff decide, staff assign
a step if there is one to assign. The ask is recorded either way, which is
what makes "we never got back to them" findable rather than remembered.

**The offer is stored as a code, not a foreign key.** Offers live in
app/next_steps.py as a tuple, so there is no row to point at. A code also
survives an offer being retired: sign-ups from a Child Dedication the church
stopped running still render, because `next_steps.label_for` falls back to
the code rather than returning nothing.

**Nothing here is rewritten.** The note is what somebody typed, and it stays
that. The only thing that changes is whether a human has dealt with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow


class NextStepSignup(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "next_step_signup"
    __table_args__ = (
        # The only query this table has: one church's open sign-ups, newest
        # first. `handled_at` leads because the list is almost always
        # filtered to the ones nobody has dealt with.
        Index("ix_signup_church_open", "church_id", "handled_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # CASCADE, unlike a guest card. A connect card records that a stranger
    # visited and is worth keeping after their record goes; this records that
    # a member of this church asked for something, and once there is no
    # member there is no ask to answer.
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    offer: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

    handled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    handled_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<NextStepSignup {self.offer} person={self.person_id}>"

    @property
    def offer_label(self) -> str:
        from app.next_steps import label_for

        return label_for(self.offer)

    @property
    def is_handled(self) -> bool:
        return self.handled_at is not None

    def handle(self, actor=None) -> bool:
        """Mark it dealt with. Returns whether anything changed.

        Returns False rather than overwriting when it is already handled, so
        a double-tapped button does not rewrite who dealt with it to whoever
        happened to tap second.
        """
        if self.handled_at is not None:
            return False
        self.handled_at = utcnow()
        self.handled_by_name = getattr(actor, "name", None)
        return True

    # -- lookups ------------------------------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, signup_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == signup_id, cls.church_id == church_id)
        )

    @classmethod
    def open_for_church(cls, church_id: int, limit: int | None = None):
        query = (
            db.select(cls)
            .where(cls.church_id == church_id, cls.handled_at.is_(None))
            .order_by(cls.created_at.desc())
        )
        return query.limit(limit) if limit else query

    @classmethod
    def recent_for_church(cls, church_id: int, limit: int = 50):
        return (
            db.select(cls)
            .where(cls.church_id == church_id)
            .order_by(cls.created_at.desc())
            .limit(limit)
        )

    @classmethod
    def open_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.handled_at.is_(None)
            )
        ) or 0

    @classmethod
    def already_asked(cls, church_id: int, person_id: int, offer: str):
        """An open sign-up by this person for this offer, if there is one.

        Somebody who taps Baptism twice in a week has asked once. A second
        row would put them on the list twice and make the count a measure of
        how many times a tile was tapped rather than how many people are
        waiting to hear back.
        """
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.offer == offer,
                cls.handled_at.is_(None),
            )
        )
