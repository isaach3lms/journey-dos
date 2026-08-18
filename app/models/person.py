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
from app.stages import (
    CONTACT_WINDOW_DAYS,
    FIRST_STAGE,
    STAGE_CODES,
    expected_days,
    stage_label,
    stage_order,
)

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
        Index("ix_person_church_contact", "church_id", "last_contact_at"),
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

    # Denormalized from contact_log. See app/models/contact.py for why, and
    # `flask recompute-contact` for how it is rebuilt if it ever drifts.
    last_contact_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # The staff member or leader responsible for this person. Nullable, and
    # the dashboard counts the nulls on purpose: a person nobody owns is the
    # most common way someone quietly stops being anyone's problem.
    owner_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    owner_name: Mapped[Optional[str]] = mapped_column(String(120))

    # A global opt-out. Set from the one-click unsubscribe link, and it
    # outranks every per-category preference. Transactional mail still sends;
    # see app/categories.py for why that is not a loophole.
    email_opt_out_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # The secret in an unsubscribe URL. Random and per person, so a link
    # cannot be guessed from an id and one person cannot unsubscribe another.
    # Generated lazily, because most people never need one.
    unsubscribe_token: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    is_child: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)

    notification_preferences: Mapped[list["NotificationPreference"]] = relationship(  # noqa: F821
        back_populates="person",
        cascade="all, delete-orphan",
    )

    contacts: Mapped[list["ContactLog"]] = relationship(  # noqa: F821
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="ContactLog.occurred_at.desc()",
    )
    next_steps: Mapped[list["NextStep"]] = relationship(  # noqa: F821
        back_populates="person",
        cascade="all, delete-orphan",
    )

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


    # -- the stuck engine ---------------------------------------------------
    #
    # Stuck is computed, never stored. A stored flag is wrong the moment
    # somebody logs a phone call, and a nightly job to fix that would mean a
    # pastor sees yesterday's answer. Computing it means the answer is always
    # current, and expressing it in SQL means the dashboard asks the database
    # for four rows rather than loading the roster and looping.

    @property
    def days_since_contact(self) -> int | None:
        if self.last_contact_at is None:
            return None
        return max(0, (utcnow() - self.last_contact_at).days)

    @property
    def is_overdue_in_stage(self) -> bool:
        """Been at a transitional stage longer than that stage expects.

        Always False on a destination stage, which has no expectation to be
        overdue against.
        """
        limit = expected_days(self.stage)
        return limit is not None and self.days_in_stage > limit

    @property
    def is_out_of_contact(self) -> bool:
        """Nobody has spoken to them inside the contact window."""
        days = self.days_since_contact
        return days is None or days > CONTACT_WINDOW_DAYS

    @property
    def is_stuck(self) -> bool:
        """Both conditions, not either.

        Time in a stage alone is not a problem: a Member who has been a Member
        for three years is exactly where they should be. Silence alone is not a
        problem either, on its own. The two together are what a pastor would
        actually want to look at.
        """
        return self.is_overdue_in_stage and self.is_out_of_contact

    @property
    def stuck_reason(self) -> str | None:
        if not self.is_stuck:
            return None
        days = self.days_since_contact
        contact = "never contacted" if days is None else f"no contact in {days} days"
        return f"{self.days_in_stage} days as a {self.stage_label}, {contact}"

    @classmethod
    def _stuck_clause(cls, now=None):
        """The SQL behind `is_stuck`, as one OR of per-stage conditions.

        Seven clauses rather than a CASE expression, because this shape is
        what the `(church_id, stage, stage_since)` index can actually serve.
        """
        from datetime import timedelta

        from app.stages import TRANSITIONAL_STAGES

        now = now or utcnow()
        contact_cutoff = now - timedelta(days=CONTACT_WINDOW_DAYS)

        # Only transitional stages. A Member of three years is not stuck.
        overdue = db.or_(
            *[
                db.and_(
                    cls.stage == stage.code,
                    cls.stage_since < now - timedelta(days=stage.expected_days),
                )
                for stage in TRANSITIONAL_STAGES
            ]
        )
        silent = db.or_(
            cls.last_contact_at.is_(None),
            cls.last_contact_at < contact_cutoff,
        )
        return db.and_(overdue, silent)

    @classmethod
    def stuck(cls, church_id: int, limit: int | None = None):
        query = (
            cls.for_church(church_id)
            .where(cls._stuck_clause())
            .order_by(cls.stage_since)
        )
        return query.limit(limit) if limit else query

    @classmethod
    def stuck_count(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls._stuck_clause(),
            )
        ) or 0

    @classmethod
    def unowned_count(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.owner_user_id.is_(None),
            )
        ) or 0

    @classmethod
    def contacted_since(cls, church_id: int, days: int = 7) -> int:
        from datetime import timedelta

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.last_contact_at >= utcnow() - timedelta(days=days),
            )
        ) or 0


    # -- email permission ---------------------------------------------------

    @property
    def has_opted_out(self) -> bool:
        return self.email_opt_out_at is not None

    def ensure_unsubscribe_token(self) -> str:
        """Mint the token on first use. Callers must commit."""
        import secrets

        if not self.unsubscribe_token:
            self.unsubscribe_token = secrets.token_urlsafe(32)
        return self.unsubscribe_token

    def allows(self, category_code: str) -> bool:
        """May this person be emailed about this category, right now?

        Order matters. Transactional first, because a password reset has to
        reach someone who unsubscribed from the newsletter. Then the global
        opt-out, which outranks per-category settings. Then the stored
        preference, then the category default.
        """
        from app.categories import default_on, is_transactional

        if is_transactional(category_code):
            return True
        if self.has_opted_out:
            return False
        for preference in self.notification_preferences:
            if preference.category == category_code:
                return preference.allowed
        return default_on(category_code)

    def set_preference(self, category_code: str, allowed: bool) -> None:
        from app.categories import CATEGORY_BY_CODE
        from app.models.outbox import NotificationPreference

        if category_code not in CATEGORY_BY_CODE:
            raise ValueError(f"{category_code!r} is not a notification category.")

        for preference in self.notification_preferences:
            if preference.category == category_code:
                preference.allowed = allowed
                return

        self.notification_preferences.append(
            NotificationPreference(
                church_id=self.church_id,
                person_id=self.id,
                category=category_code,
                allowed=allowed,
            )
        )
