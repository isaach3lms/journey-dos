"""The weekly prayer check-in.

A member says how the church can pray for them this week. One row per person
per week, and the week is the whole design.

**Why a week and not a request.** `SupportRequest` already exists for somebody
asking their church for help, and it is built for exactly that: it stays open
until a named human closes it, and it is the one thing in this system that
cannot be allowed to sit unread. Routing a routine weekly check-in through it
would put sixty rows a week into the queue that holds "something urgent", and
a queue that is mostly routine is a queue nobody reads to the bottom. The two
are kept apart on purpose, with one deliberate bridge: `wants_contact`.

**The week is the key, which is what makes it reset.** Keyed on the Sunday the
week starts, on the church's own clock, using the same `week_of` the verse of
the week uses, so a member's check-in week and the verse they are reading are
the same week. Nothing expires anything and no job runs: next Sunday is simply
a different key, so the tile goes back to "Not yet this week" on its own.

**Within a week it is edited, not stacked.** Somebody who writes on Sunday and
remembers more on Tuesday has checked in once, with more in it. A second row
would turn "people who checked in" into "times a form was submitted".

**What they wrote stays in the app.** The same rule `SupportRequest` holds to,
for the same reason: a prayer request copied into forty inboxes cannot be
taken back. Nothing here is put in an email or on a lock screen. Staff read
them on the list.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

MAX_BODY_LENGTH = 2000


class PrayerCheckin(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "prayer_checkin"
    __table_args__ = (
        # One per person per week. The constraint is the reset: there is no
        # job and no expiry, and a week with no row is a week not checked in.
        UniqueConstraint(
            "church_id", "person_id", "week_of", name="uq_prayer_checkin_week"
        ),
        # The only query the staff list makes: one church's week, and within
        # it the ones nobody has prayed for yet come first.
        Index(
            "ix_prayer_checkin_church_week",
            "church_id",
            "week_of",
            "prayed_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # CASCADE. Once there is no member there is no check-in: this is a
    # person's own words about their own week, not a record of church
    # activity that outlives them.
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship()  # noqa: F821

    # The Sunday the week starts, on the church's clock. A date rather than a
    # week number: a week number needs a year beside it to mean anything and
    # cannot be compared or printed without unpacking both.
    week_of: Mapped[date] = mapped_column(Date, nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # They want somebody to get in touch, not only to pray. This is the one
    # bridge into the care queue, and it is the member's choice rather than a
    # staff member's reading of what they wrote.
    wants_contact: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Somebody prayed for it. Not a deletion and not a timer: the row stays,
    # and the name is on it, which is what turns a list people read into a
    # list people work.
    prayed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    prayed_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    # Copied, like the one on a support request, so the row still names who
    # prayed for it after that account is removed.
    prayed_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<PrayerCheckin week={self.week_of} person={self.person_id}>"

    @property
    def is_prayed_for(self) -> bool:
        return self.prayed_at is not None

    def mark_prayed(self, actor=None) -> bool:
        """Somebody prayed for it. Returns whether anything changed.

        False rather than overwriting when it is already marked, so a
        double-tapped button does not rewrite whose name is against it to
        whoever happened to tap second.
        """
        if self.prayed_at is not None:
            return False
        self.prayed_at = utcnow()
        self.prayed_by_user_id = getattr(actor, "id", None)
        self.prayed_by_name = getattr(actor, "name", None)
        return True

    def unmark_prayed(self) -> None:
        """Marked by mistake. It happens, and it should not need a database."""
        self.prayed_at = None
        self.prayed_by_user_id = None
        self.prayed_by_name = None

    # -- queries, all tenant scoped -----------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, checkin_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == checkin_id, cls.church_id == church_id)
        )

    @classmethod
    def for_person_week(cls, church_id: int, person_id: int, week_of: date):
        """This person's check-in for this week, or None.

        The only read the member screen makes, and the one the write goes
        through, so a second submission in the same week edits rather than
        stacking.
        """
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.week_of == week_of,
            )
        )

    @classmethod
    def for_week(cls, church_id: int, week_of: date):
        """One church's week, unprayed-for first, then oldest first.

        Unprayed first because the list exists to be worked through, and a
        list that reorders itself as you work down it is a list you lose your
        place in. Within each half it is the order they came in.
        """
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.week_of == week_of)
            .order_by(cls.prayed_at.is_not(None), cls.created_at)
        )

    @classmethod
    def recent_for_person(cls, church_id: int, person_id: int, limit: int = 6):
        """Their last few weeks, newest first.

        For the person's own page, which is what somebody opens before a
        visit. Capped rather than complete: a year of weeks on a profile is
        a page nobody scrolls, and the list of weeks is where history lives.
        """
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.week_of.desc())
            .limit(limit)
        )

    @classmethod
    def unprayed_count(cls, church_id: int, week_of: date) -> int:
        """How many of this week's check-ins nobody has prayed for yet.

        For the button on the People page. A page you have to open to find
        out whether there is anything on it is a page that gets opened on
        Wednesdays, and this one is worth opening on Monday.
        """
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.week_of == week_of,
                cls.prayed_at.is_(None),
            )
        ) or 0

    @classmethod
    def weeks_with_any(cls, church_id: int, limit: int = 12):
        """The weeks this church has check-ins for, newest first.

        Drives the week picker. Built from rows rather than from the calendar
        so a church that started last month gets two weeks in the list and
        not a year of empty ones.
        """
        return (
            db.select(cls.week_of)
            .where(cls.church_id == church_id)
            .group_by(cls.week_of)
            .order_by(cls.week_of.desc())
            .limit(limit)
        )
