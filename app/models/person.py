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
        # One code per household per church. This constraint is what makes the
        # generator's retry loop correct rather than hopeful.
        UniqueConstraint("church_id", "checkin_pin", name="uq_household_checkin_pin"),
        Index("ix_household_church_name", "church_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Kiosk check-in code. Identification, not authorization: it answers which
    # family you are, never whether you may collect a child. See
    # app/checkin_pin.py. VARCHAR(6) so widening past four digits is a config
    # change rather than a migration.
    checkin_pin: Mapped[Optional[str]] = mapped_column(String(6), index=True)

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

    def ensure_checkin_pin(self) -> str:
        """Assign a PIN if this household has none. Callers must commit."""
        from app.checkin_pin import generate_pin

        if self.checkin_pin:
            return self.checkin_pin

        def is_taken(code: str) -> bool:
            return db.session.scalar(
                db.select(Household.id).where(
                    Household.church_id == self.church_id,
                    Household.checkin_pin == code,
                )
            ) is not None

        self.checkin_pin = generate_pin(is_taken)
        return self.checkin_pin

    def regenerate_checkin_pin(self) -> str:
        """Rotate the code.

        Needed for custody situations and for a family who believe their code
        is known to someone it should not be. Audit logging arrives with the
        audit surface at increment 15.
        """
        self.checkin_pin = None
        return self.ensure_checkin_pin()

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
        # Deliberately NOT unique.
        #
        # A married couple sharing one address is the normal case in a church,
        # not an edge case, and a unique constraint here means the second
        # spouse cannot be entered at all. That is a wall a church hits on its
        # first afternoon of data entry.
        #
        # The cost is that email is no longer an identifier, so every lookup
        # through it has to decide what to do with more than one row. They all
        # refuse rather than guess: see User.link_person_by_email and
        # app/matching.py. Guessing which spouse gave a gift is exactly the
        # error the matching module exists to prevent.
        Index("ix_person_church_email", "church_id", "email"),
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

    # Whether this person created their own account rather than being added
    # by staff. Recorded because it is the only thing that distinguishes a
    # stranger who signed up from somebody the church already knew.
    self_registered: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )

    # Null means a self-registered person is waiting for a human to confirm
    # they are real. Staff-created people are approved on creation, so this is
    # set for everybody the church entered themselves.
    #
    # Until it is set, the person's own record works normally and church-wide
    # announcements are hidden. The gate is deliberately narrow: somebody
    # waiting should be able to read a plan and see their own details, because
    # the alternative is an app that looks broken to the person who just
    # decided to engage.
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

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
    def age(self) -> int | None:
        """Whole years, or None when nobody recorded a birthday.

        On the roster this is only shown for children, where it is the thing
        the kids team actually needs: which room they are in.
        """
        from datetime import date as _date

        if self.birthdate is None:
            return None
        today = _date.today()
        years = today.year - self.birthdate.year
        if (today.month, today.day) < (self.birthdate.month, self.birthdate.day):
            years -= 1
        return max(0, years)

    @property
    def days_known(self) -> int:
        """Whole days since the church first met them.

        `first_seen_on` when staff recorded a first Sunday, otherwise the day
        the record was created. Shown on the member's own profile, so it reads
        as "Member, 176 days" rather than a date nobody remembers.
        """
        from datetime import date as _date

        start = self.first_seen_on or (self.created_at.date() if self.created_at else None)
        if start is None:
            return 0
        return max(0, (_date.today() - start).days)

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
        children: bool | None = None,
    ):
        """`children` is a third state on purpose.

        None is everybody, True is the children on their own, and False is
        the adults. A stage filter passes False, because a five year old
        whose family are Members is not a Member the church is discipling.
        """
        query = cls.for_church(church_id, include_archived)

        if children is not None:
            query = query.where(cls.is_child.is_(children))

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

        Children are not in these numbers. They carry their family's stage so
        that check-in and the roster have something to sort by, but a rail
        that counts them as Members is telling staff that 32 adults have
        committed to the church when 9 of them are in the kids room.
        """
        rows = db.session.execute(
            db.select(cls.stage, func.count(cls.id))
            .where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.is_child.is_(False),
            )
            .group_by(cls.stage)
        ).all()
        counts = {code: 0 for code in STAGE_CODES}
        for code, count in rows:
            if code in counts:
                counts[code] = count
        return counts

    @classmethod
    def child_count(cls, church_id: int) -> int:
        """Every child on the roster. The last segment of the rail."""
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.is_child.is_(True),
            )
        ) or 0

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
    def is_stuck(self) -> bool:  # noqa: D401 - see _stuck_clause for the SQL twin
        """Both conditions, not either.

        Time in a stage alone is not a problem: a Member who has been a Member
        for three years is exactly where they should be. Silence alone is not a
        problem either, on its own. The two together are what a pastor would
        actually want to look at. Never a child: see `_stuck_clause`, whose
        SQL this property has to agree with exactly.
        """
        return (
            not self.is_child
            and self.is_overdue_in_stage
            and self.is_out_of_contact
        )

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
        # `+ 1` and `<=`, matching `days_since_contact > CONTACT_WINDOW_DAYS`
        # exactly. See the note on the overdue clause below: truncated whole
        # days and exact timestamps disagree for one day unless both sides
        # count the same way.
        contact_cutoff = now - timedelta(days=CONTACT_WINDOW_DAYS + 1)

        # Only transitional stages. A Member of three years is not stuck.
        overdue = db.or_(
            *[
                db.and_(
                    cls.stage == stage.code,
                    # `+ 1` and `<=` so this matches `days_in_stage > limit`
                    # exactly. Whole days elapsed >= limit + 1 is the same
                    # condition the Python property tests.
                    cls.stage_since <= now - timedelta(days=stage.expected_days + 1),
                )
                for stage in TRANSITIONAL_STAGES
            ]
        )
        silent = db.or_(
            cls.last_contact_at.is_(None),
            cls.last_contact_at <= contact_cutoff,
        )
        # Children are never "stuck". A five year old is not somebody staff
        # failed to follow up with, and parents now add their own children
        # from the app, so without this every family that signs up would put
        # its kids on the follow-up list a few weeks later.
        return db.and_(overdue, silent, cls.is_child.is_(False))

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



    # -- approval -----------------------------------------------------------

    @property
    def is_approved(self) -> bool:
        """May this person see anything the church posts to everyone?

        Deliberately not "approved_at is set". Approval exists only for people
        who signed themselves up; somebody staff entered was never in the
        queue. Defining it the other way would mean every new staff-created
        person started invisible to announcements unless somebody remembered
        to stamp a column, and forgetting would be silent.
        """
        return not self.is_waiting_for_approval

    @property
    def is_waiting_for_approval(self) -> bool:
        return self.self_registered and self.approved_at is None

    def approve(self, actor=None) -> bool:
        """Confirm a self-registered person is real. Caller commits."""
        if self.approved_at is not None:
            return False
        self.approved_at = utcnow()
        return True

    @classmethod
    def waiting_for_approval(cls, church_id: int):
        return (
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.self_registered.is_(True),
                cls.approved_at.is_(None),
            )
            .order_by(cls.created_at)
        )

    @classmethod
    def waiting_count(cls, church_id: int) -> int:
        from sqlalchemy import func as sa_func

        return db.session.scalar(
            db.select(sa_func.count(cls.id)).where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                cls.self_registered.is_(True),
                cls.approved_at.is_(None),
            )
        ) or 0


    # -- archiving ----------------------------------------------------------

    def archive(self) -> bool:
        """Take somebody off the roster without destroying the record.

        Archive rather than delete, always. A person row is referenced by
        check-in history, matched giving, service assignments, and messages.
        Deleting one erases a child's check-in record, which a church has to
        keep, and rewrites who said what in a room. Archiving removes them from
        the roster, the counts, the stuck engine, and every sequence, and
        leaves the history intact.
        """
        if self.is_archived:
            return False
        self.is_archived = True
        return True

    def unarchive(self) -> bool:
        if not self.is_archived:
            return False
        self.is_archived = False
        return True

    @classmethod
    def archived_for_church(cls, church_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.is_archived.is_(True))
            .order_by(cls.last_name, cls.first_name)
        )

    @classmethod
    def archived_count(cls, church_id: int) -> int:
        from sqlalchemy import func as sa_func

        return db.session.scalar(
            db.select(sa_func.count(cls.id)).where(
                cls.church_id == church_id, cls.is_archived.is_(True)
            )
        ) or 0

    @classmethod
    def get_many_for_church(cls, church_id: int, ids: list[int]) -> list["Person"]:
        """Load several people, tenant-scoped.

        Takes the ids and returns only the ones that belong to this church,
        silently dropping the rest. A bulk action is exactly where a stray id
        from another tenant would otherwise slip through unnoticed.
        """
        if not ids:
            return []
        return list(
            db.session.scalars(
                db.select(cls).where(cls.church_id == church_id, cls.id.in_(ids))
            )
        )
