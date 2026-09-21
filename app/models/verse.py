"""The verse of the week on the member Home tab.

One row per church per week, keyed by the Sunday the week starts on. Staff
can set this week's and schedule weeks ahead; members see the newest verse
whose week has started, so a verse typed on Wednesday for next Sunday does
not appear early, and a church that forgets a week keeps showing last week's
rather than an empty card.

The text is stored as staff entered it. When they leave it blank it is filled
from the World English Bible, which is public domain. A licensed translation
(NIV, ESV) is only ever what a staff member typed themselves, never pulled
from a licensed provider and copied into a table.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin


def week_of(day: date) -> date:
    """The Sunday that starts the week containing `day`."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


class WeeklyVerse(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "weekly_verse"
    __table_args__ = (
        UniqueConstraint("church_id", "starts_on", name="uq_weekly_verse_week"),
        Index("ix_weekly_verse_church_starts", "church_id", "starts_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    translation: Mapped[Optional[str]] = mapped_column(String(20))
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    def __repr__(self) -> str:
        return f"<WeeklyVerse {self.starts_on} {self.reference}>"

    @property
    def ends_on(self) -> date:
        return self.starts_on + timedelta(days=6)

    @classmethod
    def current(cls, church_id: int, today: date) -> "WeeklyVerse | None":
        return db.session.scalar(
            db.select(cls)
            .where(cls.church_id == church_id, cls.starts_on <= today)
            .order_by(cls.starts_on.desc())
            .limit(1)
        )

    @classmethod
    def upcoming(cls, church_id: int, today: date):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.starts_on > today)
            .order_by(cls.starts_on)
        )

    @classmethod
    def past(cls, church_id: int, before: date, limit: int = 6):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.starts_on < before)
            .order_by(cls.starts_on.desc())
            .limit(limit)
        )

    @classmethod
    def for_week(cls, church_id: int, starts_on: date) -> "WeeklyVerse | None":
        return db.session.scalar(
            db.select(cls).where(cls.church_id == church_id, cls.starts_on == starts_on)
        )

    @classmethod
    def get_for_church(cls, church_id: int, verse_id: int) -> "WeeklyVerse | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == verse_id, cls.church_id == church_id)
        )
