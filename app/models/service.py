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
ITEM_KINDS = (ITEM_SONG, ITEM_ELEMENT)

STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
SERVICE_STATUSES = (STATUS_DRAFT, STATUS_SENT)

INVITED = "invited"
ACCEPTED = "accepted"
DECLINED = "declined"
ASSIGNMENT_STATUSES = (INVITED, ACCEPTED, DECLINED)

ASSIGNMENT_LABELS = {
    INVITED: "Waiting on them",
    ACCEPTED: "Accepted",
    DECLINED: "Declined",
}

_ITEM_KINDS = ", ".join(f"'{k}'" for k in ITEM_KINDS)
_SERVICE_STATUSES = ", ".join(f"'{s}'" for s in SERVICE_STATUSES)
_ASSIGNMENT_STATUSES = ", ".join(f"'{s}'" for s in ASSIGNMENT_STATUSES)


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
            return capo_options(self.default_key)
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
    # Aware UTC, rendered through app/timeutil.py in the church's zone.
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_DRAFT
    )
    plan_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    items: Mapped[list["ServiceItem"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceItem.position",
    )
    assignments: Mapped[list["ServiceAssignment"]] = relationship(
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
        from app.music import UnknownKey, capo_options

        if not self.key:
            return []
        try:
            return capo_options(self.key)
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
