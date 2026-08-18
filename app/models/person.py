"""Households and people. The pastoral record.

Tenant scoping is the whole risk in this file. Every query below goes through a
classmethod that takes `church_id` and puts it in the WHERE clause. There is no
bare `db.session.get(Person, id)` anywhere in the codebase, because that call
takes a primary key and nothing else: a person id from one church, pasted into
another church's URL, would load and render. `Person.get_for_church` exists so
that mistake is not available to make.

`stage_since` is the column increment 3's stuck engine reads. Increment 2 only
writes it and shows the elapsed time, but writing it correctly now is what
makes "43 days as a Guest, no contact in 3 weeks" possible later without a
backfill.
"""

from __future__ import annotations

# SQLAlchemy evaluates the annotation inside `Mapped[...]` at class-definition
# time, so `from __future__ import annotations` does not defer it. Optional[]
# resolves on every Python version; `str | None` needs 3.10 or newer.
from typing import Optional

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
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
from app.stages import FIRST_STAGE, STAGE_CODES, stage_label, stage_order

_STAGE_LIST = ", ".join(f"'{code}'" for code in STAGE_CODES)


class Household(TenantScoped, TimestampMixin, db.Model):
    """A family unit. Optional: a person does not need one."""

    __tablename__ = "household"
    __table_args__ = (
        Index("ix_household_church_name", "church_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    address_line: Mapped[Optional[str]] = mapped_column(String(200))
    city: Mapped[Optional[str]] = mapped_column(String(80))
    postal_code: Mapped[Optional[str]] = mapped_column(String(20))

    members: Mapped[list["Person"]] = relationship(
        back_populates="household",
        order_by="Person.last_name, Person.first_name",
    )

    def __repr__(self) -> str:
        return f"<Household {self.name!r} church={self.church_id}>"

    @classmethod
    def get_for_church(cls, church_id: int, household_id: int) -> "Household | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == household_id, cls.church_id == church_id)
        )

    @classmethod
    def find_or_create(cls, church_id: int, name: str) -> "Household":
        name = (name or "").strip()
        existing = db.session.scalar(
            db.select(cls).where(cls.church_id == church_id, cls.name == name)
        )
        if existing:
            return existing
        household = cls(church_id=church_id, name=name)
        db.session.add(household)
        return household


class Person(TenantScoped, TimestampMixin, db.Model):
    """Someone the church is responsible for knowing.

    A person is not a login. Most people in this table will never have one:
    guests, children, and anyone who simply attends. `User.person_id` links the
    two when a login exists.
    """

    __tablename__ = "person"
    __table_args__ = (
        CheckConstraint(f"stage IN ({_STAGE_LIST})", name="ck_person_stage"),
        UniqueConstraint("church_id", "email", name="uq_person_church_email"),
        Index("ix_person_church_stage", "church_id", "stage"),
        Index("ix_person_church_last_first", "church_id", "last_name", "first_name"),
        Index("ix_person_church_stage_since", "church_id", "stage", "stage_since"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Nullable on purpose. A visitor who filled in nothing but a name on a
    # connect card is still a person the church is responsible for.
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    birthdate: Mapped[Optional[date]] = mapped_column(Date)

    household_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), index=True
    )
    household: Mapped[Optional["Household"]] = relationship(back_populates="members")

    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FIRST_STAGE, index=True
    )
    # When they entered the stage they are in now. Increment 3 reads this.
    stage_since: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    first_seen_on: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    is_child: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)

    events: Mapped[list["PersonEvent"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="PersonEvent.occurred_at.desc(), PersonEvent.id.desc()",
    )

    def __repr__(self) -> str:
        return f"<Person {self.full_name!r} {self.stage} church={self.church_id}>"

    # -- presentation -------------------------------------------------------

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        return (self.first_name[:1] + self.last_name[:1]).upper()

    @property
    def stage_label(self) -> str:
        return stage_label(self.stage)

    @property
    def stage_order(self) -> int:
        return stage_order(self.stage)

    @property
    def days_in_stage(self) -> int:
        """Whole days since entering the current stage.

        Both sides of this subtraction are aware UTC, which is the entire
        point of the UTCDateTime decorator. On SQLite without it, stage_since
        would come back naive and this would raise, but only in production,
        where Postgres returns aware values.
        """
        return max(0, (utcnow() - self.stage_since).days)

    # -- lookups. Every one of these takes church_id. ------------------------

    @classmethod
    def get_for_church(cls, church_id: int, person_id: int) -> "Person | None":
        """The only way to load one person. Never `db.session.get`."""
        return db.session.scalar(
            db.select(cls).where(cls.id == person_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int, include_archived: bool = False):
        """Base select for a church. Every list view starts here."""
        query = db.select(cls).where(cls.church_id == church_id)
        if not include_archived:
            query = query.where(cls.is_archived.is_(False))
        return query

    @classmethod
    def search(
        cls,
        church_id: int,
        term: str | None = None,
        stage: str | None = None,
        include_archived: bool = False,
    ):
        query = cls.for_church(church_id, include_archived)

        if stage:
            query = query.where(cls.stage == stage)

        if term:
            needle = f"%{term.strip().lower()}%"
            query = query.where(
                db.or_(
                    func.lower(cls.first_name).like(needle),
                    func.lower(cls.last_name).like(needle),
                    func.lower(cls.email).like(needle),
                    func.lower(cls.first_name + " " + cls.last_name).like(needle),
                )
            )

        return query.order_by(cls.last_name, cls.first_name)

    @classmethod
    def stage_counts(cls, church_id: int) -> dict[str, int]:
        """Counts per stage for the rail. One query, not seven.

        Stages with nobody in them are returned as 0 rather than missing, so
        the rail renders every stage whether or not anyone is standing on it.
        """
        rows = db.session.execute(
            db.select(cls.stage, func.count(cls.id))
            .where(cls.church_id == church_id, cls.is_archived.is_(False))
            .group_by(cls.stage)
        ).all()
        counts = {code: 0 for code in STAGE_CODES}
        for code, count in rows:
            if code in counts:
                counts[code] = count
        return counts

    @classmethod
    def total_for_church(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.is_archived.is_(False)
            )
        ) or 0
