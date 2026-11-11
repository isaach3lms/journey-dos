"""Dates a volunteer is not available to serve.

A worship leader builds a plan on Thursday and asks six people. Two of them
are away that weekend, and the only way anybody found out was the two of them
answering no, days later, after the invitation had already gone out and the
leader had already moved on. The fix a church asks for is always the same one:
let people say up front when they are away.

**Dates, not datetimes.** "We are away the first two weeks of July" is a
statement about days in the church's own calendar, not about instants. Storing
a date avoids the whole question of what a timezone does to a boundary, and a
service is compared against it by taking the service's local day. Somebody
away on the 5th is away for a 9:00 service on the 5th, whatever UTC thinks
that morning is called.

**Inclusive at both ends.** "5 July to 19 July" means the 19th as well. A
range that quietly excluded its last day would send an invitation on the day
somebody flies home, which is the single most annoying version of this bug.

**A note, not a reason.** The field is there so a leader reading "away" is not
guessing, and because a volunteer who writes "wedding" is less likely to be
chased. It is never required: a volunteer does not owe the church an
explanation for being unavailable.

This records what somebody said about themselves. It does not decide anything
on its own: see app/blueprints/services.py for what a leader is shown and what
is held back when a blocked date is scheduled anyway.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin

# Long enough for "Visiting family in Oregon", short enough that it cannot
# become a place to keep anything that belongs in a pastoral note.
NOTE_MAX = 140


class ServingBlockout(TenantScoped, TimestampMixin, db.Model):
    """One stretch of days one person is not available to serve."""

    __tablename__ = "serving_blockout"
    __table_args__ = (
        # A backwards range is not a short blockout, it is a typo, and one
        # that would silently cover nothing at all.
        CheckConstraint("ends_on >= starts_on", name="ck_blockout_range"),
        Index("ix_blockout_person", "church_id", "person_id", "ends_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    # Inclusive. See the note at the top of the file.
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)

    note: Mapped[Optional[str]] = mapped_column(String(NOTE_MAX))

    def __repr__(self) -> str:
        return f"<ServingBlockout person={self.person_id} {self.starts_on}..{self.ends_on}>"

    @property
    def is_one_day(self) -> bool:
        return self.starts_on == self.ends_on

    def covers(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on

    # -- reading ------------------------------------------------------------

    @classmethod
    def for_person(cls, church_id: int, person_id: int, today: date | None = None):
        """What this person has told the church, soonest first.

        Only what has not finished. A list that kept every holiday somebody
        ever took would need clearing out by hand, and the screen it appears
        on is a list of things that are still true.
        """
        cutoff = today or date.today()
        return (
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.ends_on >= cutoff,
            )
            .order_by(cls.starts_on)
        )

    @classmethod
    def covering(cls, church_id: int, person_id: int, day: date):
        """The blockout over one person on one day, or None."""
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.starts_on <= day,
                cls.ends_on >= day,
            )
        )

    @classmethod
    def away_on(cls, church_id: int, day: date) -> dict[int, "ServingBlockout"]:
        """Everyone unavailable on one day, keyed by person.

        One query for the whole plan screen. Asking per person would be a
        query per row of the picker, on the screen a leader opens most.
        """
        rows = db.session.scalars(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.starts_on <= day,
                cls.ends_on >= day,
            )
        ).all()
        # First wins. Overlapping ranges are a person entering the same
        # fortnight twice, and either one says the same thing.
        out: dict[int, ServingBlockout] = {}
        for row in rows:
            out.setdefault(row.person_id, row)
        return out

    @classmethod
    def get_for_church(cls, church_id: int, blockout_id: int) -> "ServingBlockout | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == blockout_id, cls.church_id == church_id)
        )
