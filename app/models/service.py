"""Services, songs, and teams.

**No lyrics and no chord charts are stored here.** Reproducing either requires
a CCLI SongSelect licence that the *church* holds, not the vendor, and a
platform that stores lyrics for every tenant is reproducing copyrighted work at
scale on behalf of people whose licences it cannot verify. This system stores
what a plan actually needs: title, author, the church's own CCLI number, and a
key. The words live wherever the church already licences them.

Three separate ideas that are easy to collapse and should not be:

- A **Team** is a group of people who serve together. Worship, Kids, Hospitality.
- A **TeamPosition** is a job on that team. Acoustic, Drums, Vocals.
- An **Assignment** is one person, in one position, for one service, with an
  answer. Assignments are what a volunteer sees and responds to.

A `ServiceItem` is one line of the running order. A song item can override the
service key, because the same song does not sit in the same key every week.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# Plan item kinds. A song points at the library; everything else is free text.
ITEM_SONG = "song"
ITEM_ELEMENT = "element"
ITEM_HEADER = "header"
ITEM_KINDS = (ITEM_SONG, ITEM_ELEMENT, ITEM_HEADER)

STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
SERVICE_STATUSES = (STATUS_DRAFT, STATUS_SENT)

INVITED = "invited"
ACCEPTED = "accepted"
DECLINED = "declined"
ASSIGNMENT_STATUSES = (INVITED, ACCEPTED, DECLINED)

ASSIGNMENT_LABELS = {
    INVITED: "Waiting",
    ACCEPTED: "Accepted",
    DECLINED: "Declined",
}

_ITEM_KINDS = ", ".join(f"'{k}'" for k in ITEM_KINDS)
_SERVICE_STATUSES = ", ".join(f"'{s}'" for s in SERVICE_STATUSES)
_ASSIGNMENT_STATUSES = ", ".join(f"'{s}'" for s in ASSIGNMENT_STATUSES)


class ServiceType(TenantScoped, TimestampMixin, db.Model):
    """A recurring kind of service, and the shape its plan usually takes.

    The single biggest thing a worship leader does every week is rebuild last
    week's plan. A type holds the running order that rarely changes, so a new
    service starts as a real plan rather than an empty page. Sunday Morning,
    Wednesday Youth, Christmas Eve.

    The template is a plan like any other, stored as items against the type. It
    is copied into a service, never referenced, so editing this week's plan
    cannot rewrite the template and editing the template cannot rewrite a
    service that already went out.
    """

    __tablename__ = "service_type"
    __table_args__ = (
        UniqueConstraint("church_id", "name", name="uq_service_type_name"),
        Index("ix_service_type_church", "church_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Where the clock starts. A plan's running times are offsets from the
    # service start, so an item knows when it happens without storing a time.
    default_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    template_items: Mapped[list["ServiceTemplateItem"]] = relationship(
        back_populates="service_type",
        cascade="all, delete-orphan",
        order_by="ServiceTemplateItem.position",
    )
    needs: Mapped[list["ServiceTypeNeed"]] = relationship(
        back_populates="service_type", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ServiceType {self.name!r} church={self.church_id}>"

    @property
    def template_minutes(self) -> int:
        return sum(item.minutes or 0 for item in self.template_items)

    @classmethod
    def get_for_church(cls, church_id: int, type_id: int) -> "ServiceType | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == type_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.is_active.is_(True))
            .order_by(cls.name)
        )


class ServiceTemplateItem(TenantScoped, TimestampMixin, db.Model):
    """One line of a service type's usual running order."""

    __tablename__ = "service_template_item"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_ITEM_KINDS})", name="ck_service_template_item_kind"),
        UniqueConstraint(
            "service_type_id", "position", name="uq_service_template_item_position"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_type_id: Mapped[int] = mapped_column(
        ForeignKey("service_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type: Mapped["ServiceType"] = relationship(back_populates="template_items")

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=ITEM_ELEMENT)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    minutes: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # A template may pin a song, though most churches leave songs blank and
    # choose them each week.
    song_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("song.id", ondelete="SET NULL")
    )
    song: Mapped[Optional["Song"]] = relationship()

    def __repr__(self) -> str:
        return f"<ServiceTemplateItem {self.position}. {self.title!r}>"


class ServiceTypeNeed(TenantScoped, TimestampMixin, db.Model):
    """How many people this kind of service usually needs in a position.

    "Two vocals, one drummer, one on the check-in desk." Copied onto each
    service so a leader can see what is still unfilled rather than counting
    names against a mental list.
    """

    __tablename__ = "service_type_need"
    __table_args__ = (
        UniqueConstraint(
            "service_type_id", "position_id", name="uq_service_type_need_position"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_type_id: Mapped[int] = mapped_column(
        ForeignKey("service_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_type: Mapped["ServiceType"] = relationship(back_populates="needs")

    position_id: Mapped[int] = mapped_column(
        ForeignKey("team_position.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped["TeamPosition"] = relationship()
    wanted: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return f"<ServiceTypeNeed position={self.position_id} wanted={self.wanted}>"


class Song(TenantScoped, TimestampMixin, db.Model):
    """Library metadata only. See the module docstring on lyrics."""

    __tablename__ = "song"
    __table_args__ = (
        Index("ix_song_church_title", "church_id", "title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(200))

    # The church's own CCLI number for this song. It points at their licensed
    # copy; it does not license anything on our side.
    ccli_number: Mapped[Optional[str]] = mapped_column(String(20))

    default_key: Mapped[Optional[str]] = mapped_column(String(10))
    tempo_bpm: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Song {self.title!r} {self.default_key} church={self.church_id}>"

    @property
    def capo_options(self):
        from app.music import UnknownKey, capo_options

        if not self.default_key:
            return []
        try:
            return [
                option for option in capo_options(self.default_key)
                if option.capo > 0
            ]
        except UnknownKey:
            return []

    @classmethod
    def get_for_church(cls, church_id: int, song_id: int) -> "Song | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == song_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int, active_only: bool = True):
        query = db.select(cls).where(cls.church_id == church_id)
        if active_only:
            query = query.where(cls.is_active.is_(True))
        return query.order_by(cls.title)

    @classmethod
    def times_used(cls, church_id: int) -> dict[int, int]:
        """How often each song appears in a plan. One query, not one per song."""
        rows = db.session.execute(
            db.select(ServiceItem.song_id, func.count(ServiceItem.id))
            .where(
                ServiceItem.church_id == church_id,
                ServiceItem.song_id.is_not(None),
            )
            .group_by(ServiceItem.song_id)
        ).all()
        return {song_id: count for song_id, count in rows}


class Team(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "team"
    __table_args__ = (
        Index("ix_team_church_name", "church_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    positions: Mapped[list["TeamPosition"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", order_by="TeamPosition.name"
    )
    members: Mapped[list["TeamMembership"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Team {self.name!r} church={self.church_id}>"

    @classmethod
    def get_for_church(cls, church_id: int, team_id: int) -> "Team | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == team_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.is_active.is_(True))
            .order_by(cls.name)
        )

    @classmethod
    def people_serving(cls, church_id: int) -> int:
        """Distinct people on at least one active team.

        The "Adults serving" ratio on the dashboard health card.
        """
        return db.session.scalar(
            db.select(func.count(func.distinct(TeamMembership.person_id)))
            .join(cls, cls.id == TeamMembership.team_id)
            .where(cls.church_id == church_id, cls.is_active.is_(True))
        ) or 0


class TeamPosition(TenantScoped, TimestampMixin, db.Model):
    """A job on a team. Acoustic, Drums, Check-in desk."""

    __tablename__ = "team_position"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_team_position_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team: Mapped["Team"] = relationship(back_populates="positions")
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    def __repr__(self) -> str:
        return f"<TeamPosition {self.name!r} team={self.team_id}>"


class TeamMembership(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "team_membership"
    __table_args__ = (
        UniqueConstraint("team_id", "person_id", name="uq_team_membership_person_id"),
        Index("ix_team_membership_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team: Mapped["Team"] = relationship(back_populates="members")
    person: Mapped["Person"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<TeamMembership person={self.person_id} team={self.team_id}>"


class Service(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "service"
    __table_args__ = (
        CheckConstraint(f"status IN ({_SERVICE_STATUSES})", name="ck_service_status"),
        Index("ix_service_church_time", "church_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False, default="Sunday")

    # Nullable so every service built before types existed still loads. A
    # service without a type is a one-off, which churches genuinely have.
    service_type_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service_type.id", ondelete="SET NULL"), index=True
    )
    service_type: Mapped[Optional["ServiceType"]] = relationship()

    # Aware UTC, rendered through app/timeutil.py in the church's zone.
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_DRAFT
    )
    plan_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    # How many people were in the room. Entered by staff after the service;
    # the dashboard's attendance tile reads nothing else, so an empty week is
    # shown as empty rather than guessed.
    headcount: Mapped[Optional[int]] = mapped_column(Integer)

    items: Mapped[list["ServiceItem"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceItem.position",
    )
    assignments: Mapped[list["ServiceAssignment"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )
    needs: Mapped[list["ServiceNeed"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Service {self.name!r} at={self.starts_at}>"

    @property
    def is_past(self) -> bool:
        return self.starts_at < utcnow()

    @property
    def total_minutes(self) -> int:
        return sum(item.minutes or 0 for item in self.items)

    @property
    def accepted_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == ACCEPTED)

    @property
    def waiting_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == INVITED)

    @property
    def declined_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == DECLINED)

    def next_position(self) -> int:
        return max((item.position for item in self.items), default=0) + 1

    # -- the running order --------------------------------------------------

    @property
    def timed_items(self) -> list[tuple["ServiceItem", datetime | None]]:
        """Each item with the moment it starts.

        Computed from the service start rather than stored, so moving a
        service or changing one item's length reflows the whole plan. A stored
        time would go stale the first time somebody added two minutes to the
        welcome.

        A header has no duration of its own: it labels what follows.
        """
        from datetime import timedelta

        running = self.starts_at
        out = []
        for item in self.items:
            if item.kind == ITEM_HEADER:
                out.append((item, None))
                continue
            out.append((item, running))
            running = running + timedelta(minutes=item.minutes or 0)
        return out

    @property
    def ends_at(self) -> datetime:
        from datetime import timedelta

        return self.starts_at + timedelta(minutes=self.total_minutes)

    def move_item(self, item, direction: int) -> bool:
        """Swap an item with its neighbour. Caller commits.

        Two swaps of a unique column need a gap to pass through, so the first
        value is parked out of range. Without it the unique constraint fires
        halfway.
        """
        ordered = list(self.items)
        index = next((i for i, candidate in enumerate(ordered) if candidate.id == item.id), None)
        if index is None:
            return False

        target = index + direction
        if target < 0 or target >= len(ordered):
            return False

        other = ordered[target]
        parked = -abs(item.position) - 1
        item_position, other_position = item.position, other.position

        item.position = parked
        db.session.flush()
        other.position = item_position
        db.session.flush()
        item.position = other_position
        db.session.flush()
        return True

    def renumber(self) -> None:
        """Close gaps left by deletions and swaps, so positions read 1..n.

        Two passes with a flush between them. Assigning directly would collide
        with the unique constraint the moment a later item takes a number an
        earlier one has not given up yet, so everything is parked out of range
        first.
        """
        db.session.expire(self, ["items"])
        ordered = sorted(self.items, key=lambda i: i.position)

        for offset, item in enumerate(ordered, start=1):
            item.position = -offset
        db.session.flush()

        for index, item in enumerate(ordered, start=1):
            item.position = index
        db.session.flush()

    # -- staffing -----------------------------------------------------------

    @property
    def needs_summary(self) -> list[dict]:
        """What is still unfilled, which is the question a leader actually has.

        Counts accepted and invited separately: somebody who has not answered
        is not the same as a gap, and treating them alike either panics a
        leader or hides a real hole.
        """
        summary = []
        for need in sorted(self.needs, key=lambda n: (n.position_name or "")):
            filled = [
                a for a in self.assignments
                if a.position_id == need.position_id and a.status != DECLINED
            ]
            accepted = [a for a in filled if a.status == ACCEPTED]
            summary.append({
                "position": need.position_name,
                "wanted": need.wanted,
                "filled": len(filled),
                "accepted": len(accepted),
                "short": max(0, need.wanted - len(filled)),
            })
        return summary

    @property
    def unfilled_count(self) -> int:
        return sum(row["short"] for row in self.needs_summary)

    @property
    def is_fully_staffed(self) -> bool:
        return self.unfilled_count == 0

    # -- how a Sunday reads at a glance -------------------------------------

    @property
    def roles_total(self) -> int:
        """Every slot this service is asking for.

        Needs plus anybody asked outside a listed position, because somebody
        invited to help with no formal slot is still a role being filled.
        """
        listed = sum(need.wanted for need in self.needs)
        unlisted = sum(
            1 for a in self.assignments
            if a.position_id is None and a.status != DECLINED
        )
        return listed + unlisted

    @property
    def roles_filled(self) -> int:
        return sum(1 for a in self.assignments if a.status != DECLINED)

    @property
    def readiness(self) -> str:
        """One of: ready, needs_team, draft.

        `ready` means every slot is filled and everybody has answered yes. A
        plan where half the team has not replied is not ready, and calling it
        ready is how a leader finds out on Saturday night.
        """
        if self.roles_total and self.roles_filled >= self.roles_total:
            if all(a.status == ACCEPTED for a in self.assignments):
                return "ready"
            return "draft"
        if self.roles_total:
            return "needs_team"
        return "draft"

    @property
    def readiness_label(self) -> str:
        return {
            "ready": "Ready",
            "needs_team": "Needs team",
            "draft": "Draft",
        }[self.readiness]

    @property
    def open_roles(self) -> list[dict]:
        """Positions still short, for the panel a leader acts on.

        Ordered by how short they are, because the one missing two people
        matters more than the one missing one.
        """
        rows = [row for row in self.needs_summary if row["short"]]
        return sorted(rows, key=lambda row: (-row["short"], row["position"] or ""))

    @property
    def waiting_on(self) -> list["ServiceAssignment"]:
        return [a for a in self.assignments if a.status == INVITED]

    @property
    def songs_in_plan(self) -> list["ServiceItem"]:
        return [item for item in self.items if item.kind == ITEM_SONG]

    @classmethod
    def get_for_church(cls, church_id: int, service_id: int) -> "Service | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == service_id, cls.church_id == church_id)
        )

    @classmethod
    def upcoming(cls, church_id: int, limit: int = 12):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.starts_at >= utcnow())
            .order_by(cls.starts_at)
            .limit(limit)
        )

    @classmethod
    def recent(cls, church_id: int, limit: int = 6):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.starts_at < utcnow())
            .order_by(cls.starts_at.desc())
            .limit(limit)
        )


class ServiceItem(TenantScoped, TimestampMixin, db.Model):
    """One line of the running order."""

    __tablename__ = "service_item"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_ITEM_KINDS})", name="ck_service_item_kind"),
        UniqueConstraint("service_id", "position", name="uq_service_item_position"),
        Index("ix_item_church_song", "church_id", "song_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service: Mapped["Service"] = relationship(back_populates="items")

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=ITEM_ELEMENT)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    minutes: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    song_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("song.id", ondelete="SET NULL")
    )
    song: Mapped[Optional["Song"]] = relationship()

    # The same song does not sit in the same key every week. Null means use
    # the song's default.
    key_override: Mapped[Optional[str]] = mapped_column(String(10))

    def __repr__(self) -> str:
        return f"<ServiceItem {self.position}. {self.title!r}>"

    @property
    def key(self) -> str | None:
        if self.key_override:
            return self.key_override
        return self.song.default_key if self.song else None

    @property
    def capo_options(self):
        """Capo suggestions worth printing.

        A capo of zero is not a capo, it is the absence of one, and the row
        already names the key. Showing "Key G, capo 0, play G" is noise on the
        one screen a musician reads while setting up.
        """
        from app.music import UnknownKey, capo_options

        if not self.key:
            return []
        try:
            return [option for option in capo_options(self.key) if option.capo > 0]
        except UnknownKey:
            return []

    @classmethod
    def get_for_church(cls, church_id: int, item_id: int) -> "ServiceItem | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == item_id, cls.church_id == church_id)
        )


class ServiceAssignment(TenantScoped, TimestampMixin, db.Model):
    """One person, one position, one service, one answer."""

    __tablename__ = "service_assignment"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_ASSIGNMENT_STATUSES})", name="ck_service_assignment_status"
        ),
        UniqueConstraint(
            "service_id", "person_id", "position_id",
            name="uq_service_assignment_position_id",
        ),
        Index("ix_assignment_church_person", "church_id", "person_id", "status"),
        Index("ix_assignment_service_status", "service_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("team_position.id", ondelete="SET NULL")
    )

    service: Mapped["Service"] = relationship(back_populates="assignments")
    person: Mapped["Person"] = relationship()  # noqa: F821
    position: Mapped[Optional["TeamPosition"]] = relationship()

    # Denormalized so the record still reads correctly after a position is
    # renamed or deleted. "Drums, 12 March" should not become "None, 12 March".
    position_name: Mapped[Optional[str]] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=INVITED)
    responded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<ServiceAssignment person={self.person_id} {self.status}>"

    @property
    def status_label(self) -> str:
        return ASSIGNMENT_LABELS.get(self.status, self.status.title())

    @property
    def role_name(self) -> str:
        return self.position_name or (self.position.name if self.position else "Serving")

    def respond(self, status: str) -> None:
        if status not in (ACCEPTED, DECLINED):
            raise ValueError(f"{status!r} is not an answer to an invitation.")
        self.status = status
        self.responded_at = utcnow()

    @classmethod
    def get_for_church(cls, church_id: int, assignment_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == assignment_id, cls.church_id == church_id)
        )

    @classmethod
    def upcoming_for_person(cls, church_id: int, person_id: int, limit: int = 10):
        return (
            db.select(cls)
            .join(Service, Service.id == cls.service_id)
            .where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                Service.starts_at >= utcnow(),
            )
            .order_by(Service.starts_at)
            .limit(limit)
        )


class ServiceNeed(TenantScoped, TimestampMixin, db.Model):
    """How many people this service needs in a position.

    Copied from the service type rather than read through it, so changing the
    type next month does not rewrite what a service in the past was asking
    for.
    """

    __tablename__ = "service_need"
    __table_args__ = (
        UniqueConstraint("service_id", "position_id", name="uq_service_need_position"),
        Index("ix_service_need_church", "church_id", "service_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service: Mapped["Service"] = relationship(back_populates="needs")

    position_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("team_position.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[Optional["TeamPosition"]] = relationship()
    # Denormalized for the same reason the assignment copies it: a renamed or
    # deleted position must not turn last month's plan into blanks.
    position_name: Mapped[Optional[str]] = mapped_column(String(120))

    wanted: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return f"<ServiceNeed {self.position_name!r} wanted={self.wanted}>"


def build_from_type(church_id: int, service_type, name: str, starts_at) -> Service:
    """Create a service preloaded with its type's usual plan and staffing.

    A copy, never a reference. Editing this week cannot rewrite the template,
    and editing the template cannot rewrite a service that already went out.
    """
    service = Service(
        church_id=church_id,
        service_type_id=service_type.id if service_type else None,
        name=name[:160] or "Sunday",
        starts_at=starts_at,
    )
    db.session.add(service)
    db.session.flush()

    if service_type is None:
        return service

    for template in service_type.template_items:
        db.session.add(
            ServiceItem(
                church_id=church_id,
                service_id=service.id,
                position=template.position,
                kind=template.kind,
                title=template.title,
                minutes=template.minutes,
                notes=template.notes,
                song_id=template.song_id,
            )
        )

    for need in service_type.needs:
        db.session.add(
            ServiceNeed(
                church_id=church_id,
                service_id=service.id,
                position_id=need.position_id,
                position_name=need.position.name if need.position else None,
                wanted=need.wanted,
            )
        )

    return service


def save_as_template(service: Service, service_type: "ServiceType",
                     include_needs: bool = True) -> int:
    """Make `service_type`'s template match this service's running order.

    The reverse of `build_from_type`, and a copy in the same way: editing
    this service next week does not touch the template, and the template
    changing does not touch services already planned from it. Replaces
    whatever the template held before. Returns how many items were saved.
    Caller commits.
    """
    for old in list(service_type.template_items):
        service_type.template_items.remove(old)
        db.session.delete(old)
    if include_needs:
        for old in list(service_type.needs):
            service_type.needs.remove(old)
            db.session.delete(old)
    db.session.flush()

    ordered = sorted(service.items, key=lambda item: item.position)
    for position, item in enumerate(ordered, start=1):
        service_type.template_items.append(
            ServiceTemplateItem(
                church_id=service.church_id,
                position=position,
                kind=item.kind,
                title=item.title,
                minutes=item.minutes,
                notes=item.notes,
                song_id=item.song_id,
            )
        )

    if include_needs:
        # One row per position on a template (a unique constraint), so two
        # needs for the same position on this service are added together.
        wanted: dict[int, int] = {}
        for need in service.needs:
            if need.position_id:
                wanted[need.position_id] = wanted.get(need.position_id, 0) + (need.wanted or 1)
        for position_id, count in wanted.items():
            service_type.needs.append(
                ServiceTypeNeed(
                    church_id=service.church_id,
                    position_id=position_id,
                    wanted=count,
                )
            )

    service_type.default_minutes = service.total_minutes or service_type.default_minutes
    return len(ordered)


def copy_plan(source: Service, target: Service) -> int:
    """Copy a running order from one service onto another.

    Replaces rather than appends: "copy last week" means this week looks like
    last week, not like both weeks stacked.

    Assignments are deliberately not copied. Who served last week is not who
    is available this week, and a plan that arrives pre-filled with names
    nobody asked is how a volunteer finds out they are playing by reading it
    on Sunday.
    """
    for item in list(target.items):
        db.session.delete(item)
    db.session.flush()

    copied = 0
    for item in source.items:
        db.session.add(
            ServiceItem(
                church_id=target.church_id,
                service_id=target.id,
                position=item.position,
                kind=item.kind,
                title=item.title,
                minutes=item.minutes,
                notes=item.notes,
                song_id=item.song_id,
                key_override=item.key_override,
            )
        )
        copied += 1

    if not target.needs:
        for need in source.needs:
            db.session.add(
                ServiceNeed(
                    church_id=target.church_id,
                    service_id=target.id,
                    position_id=need.position_id,
                    position_name=need.position_name,
                    wanted=need.wanted,
                )
            )

    return copied
