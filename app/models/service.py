"""Services, songs, and teams.

**No lyrics are stored here, as text.** Reproducing them requires a CCLI
SongSelect licence that the *church* holds, not the vendor, and a platform that
turns downloads into a searchable lyric store for every tenant is reproducing
copyrighted work at scale. The song record holds what a plan needs: title,
author, the church's own CCLI number, and a key.

**Charts are the exception, as files.** A church can attach the SongSelect PDF
it printed under its own licence to a song, so its musicians can open it from
the plan. It is kept as that file, never extracted, and only the people
scheduled to play it can open it. See app/models/songchart.py.

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
from sqlalchemy import event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# Plan item kinds. A song points at the library; everything else is free text.
ITEM_SONG = "song"
ITEM_ELEMENT = "element"
ITEM_HEADER = "header"
ITEM_KINDS = (ITEM_SONG, ITEM_ELEMENT, ITEM_HEADER)

# Draft: staff are still building it. Volunteers can be asked to serve, but
# the running order stays off their Serve tab. Published: the team sees the
# plan in the app. Emailing the plan publishes it too, because an emailed plan
# that the app still hides would contradict itself.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_SENT = STATUS_PUBLISHED  # the old name, kept for callers
SERVICE_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)
SERVICE_STATUS_LABELS = {STATUS_DRAFT: "Draft", STATUS_PUBLISHED: "Published"}

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

    # SongSelect PDFs the band plays from. See app/models/songchart.py.
    charts: Mapped[list["SongChart"]] = relationship(  # noqa: F821
        back_populates="song", cascade="all, delete-orphan",
        order_by="SongChart.created_at",
    )

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

    @property
    def songselect_url(self) -> str | None:
        """The church's own licensed copy on SongSelect, where the words live."""
        if not self.ccli_number:
            return None
        return f"https://songselect.ccli.com/songs/{self.ccli_number}"

    @classmethod
    def import_meta(cls, church_id: int, meta) -> tuple[str, "Song"]:
        """Add a song from a SongSelect download, or fill in the one we have.

        Matched on CCLI number first, because titles repeat ("Holy, Holy,
        Holy" has several settings) and the number does not. Without a number,
        matched on title. An existing song only has its blanks filled: a key or
        tempo staff typed by hand is theirs, and a download does not overwrite
        it. Importing an archived song brings it back, since importing it is
        asking for it.

        Returns ("added" | "updated" | "unchanged", song). Caller commits.
        """
        song = None
        if meta.ccli_number:
            song = db.session.scalar(
                db.select(cls).where(
                    cls.church_id == church_id, cls.ccli_number == meta.ccli_number
                )
            )
        if song is None:
            song = db.session.scalar(
                db.select(cls).where(
                    cls.church_id == church_id,
                    func.lower(cls.title) == meta.title.lower(),
                ).order_by(cls.id)
            )

        if song is None:
            song = cls(
                church_id=church_id,
                title=meta.title,
                author=meta.author,
                ccli_number=meta.ccli_number,
                default_key=meta.default_key,
                tempo_bpm=meta.tempo_bpm,
            )
            db.session.add(song)
            return "added", song

        changed = False
        for field in ("author", "ccli_number", "default_key", "tempo_bpm"):
            incoming = getattr(meta, field)
            if incoming and not getattr(song, field):
                setattr(song, field, incoming)
                changed = True
        if not song.is_active:
            song.is_active = True
            changed = True
        return ("updated" if changed else "unchanged"), song


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
    files: Mapped[list["TeamFile"]] = relationship(  # noqa: F821
        back_populates="team", cascade="all, delete-orphan",
        order_by="TeamFile.created_at.desc()",
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


def staffing_summary(needs, assignments) -> list[dict]:
    """What is still unfilled, which is the question a leader actually has.

    Counts accepted and invited separately: somebody who has not answered is
    not the same as a gap, and treating them alike either panics a leader or
    hides a real hole.

    Takes the two lists rather than an object, so a Sunday and one strand of it
    get the same arithmetic from the same place.
    """
    summary = []
    for need in sorted(needs, key=lambda n: (n.position_name or "")):
        filled = [
            a for a in assignments
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


class ServiceTrack(TenantScoped, TimestampMixin, db.Model):
    """One strand of a Sunday: its own running order and its own volunteers.

    A church does not run three services on a Sunday morning. It runs one
    Sunday, with the main service, kids, and the operations team all happening
    inside it. Modelling those as three separate services meant three things to
    create, three to publish, three to send, and three rows on a dashboard that
    should have shown one.

    So a Sunday is one `Service` and the things that differ by strand hang off
    a track instead: the running order, the people serving, and the roles still
    to fill. What belongs to the Sunday as a whole stays on the service: the
    date, the name, whether it is published, and how many people were in the
    room.

    A track is one `ServiceType` happening on one Sunday, which is why the
    templates that already existed feed it directly. The name is copied at
    creation rather than read through the type, for the same reason every other
    copied label in this file is: renaming "Kids Service" next year must not
    rewrite what last March's plan was called.

    Tracks share the Sunday's start time. Each running order clocks from the
    same moment, which is the church's own choice and the simpler model; if
    strands ever need to start at different times, that is a column here and
    nothing else moves.
    """

    __tablename__ = "service_track"
    __table_args__ = (
        UniqueConstraint("service_id", "position", name="uq_service_track_position"),
        Index("ix_service_track_church", "church_id", "service_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service: Mapped["Service"] = relationship(back_populates="tracks")

    # Nullable, like the service's own type was: a track added by hand for one
    # Sunday is a real case and does not need a type behind it.
    service_type_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service_type.id", ondelete="SET NULL"), index=True
    )
    service_type: Mapped[Optional["ServiceType"]] = relationship()

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    items: Mapped[list["ServiceItem"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        order_by="ServiceItem.position",
    )
    assignments: Mapped[list["ServiceAssignment"]] = relationship(
        back_populates="track", cascade="all, delete-orphan"
    )
    needs: Mapped[list["ServiceNeed"]] = relationship(
        back_populates="track", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ServiceTrack {self.name!r} service={self.service_id}>"

    @property
    def total_minutes(self) -> int:
        return sum(item.minutes or 0 for item in self.items)

    def next_position(self) -> int:
        return max((item.position for item in self.items), default=0) + 1

    @property
    def starts_at(self):
        """The Sunday's start. Tracks do not carry their own."""
        return self.service.starts_at if self.service is not None else None

    @property
    def timed_items(self) -> list[tuple["ServiceItem", datetime | None]]:
        """Each item with the moment it starts, clocked from the Sunday."""
        from datetime import timedelta

        running = self.starts_at
        out = []
        for item in self.items:
            if item.kind == ITEM_HEADER:
                out.append((item, None))
                continue
            out.append((item, running))
            if running is not None:
                running = running + timedelta(minutes=item.minutes or 0)
        return out

    @property
    def roles_total(self) -> int:
        return sum(need.wanted for need in self.needs)

    @property
    def roles_filled(self) -> int:
        return sum(1 for a in self.assignments if a.status != DECLINED)

    @property
    def ends_at(self):
        from datetime import timedelta

        if self.starts_at is None:
            return None
        return self.starts_at + timedelta(minutes=self.total_minutes)

    @property
    def needs_summary(self) -> list[dict]:
        return staffing_summary(self.needs, self.assignments)

    @property
    def open_roles(self) -> list[dict]:
        return [row for row in self.needs_summary if row["short"]]

    @property
    def unfilled_count(self) -> int:
        return sum(row["short"] for row in self.needs_summary)

    @property
    def is_fully_staffed(self) -> bool:
        return self.unfilled_count == 0 and self.roles_total > 0

    def move_item(self, item, direction: int) -> bool:
        """Swap an item with its neighbour. Caller commits."""
        ordered = list(self.items)
        index = next(
            (i for i, candidate in enumerate(ordered) if candidate.id == item.id), None
        )
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

    def reorder_items(self, ordered_ids: list[int]) -> bool:
        """Put this track's running order in the given order. Caller commits.

        All or nothing, and scoped to this track: a list naming an item from
        the kids plan must not reorder the main service.
        """
        items = {item.id: item for item in self.items}
        if len(ordered_ids) != len(items) or set(ordered_ids) != set(items):
            return False

        for offset, item_id in enumerate(ordered_ids, start=1):
            items[item_id].position = -offset
        db.session.flush()

        for index, item_id in enumerate(ordered_ids, start=1):
            items[item_id].position = index
        db.session.flush()
        return True

    def renumber(self) -> None:
        """Close gaps left by deletions and swaps, so positions read 1..n."""
        db.session.expire(self, ["items"])
        ordered = sorted(self.items, key=lambda i: i.position)

        for offset, item in enumerate(ordered, start=1):
            item.position = -offset
        db.session.flush()

        for index, item in enumerate(ordered, start=1):
            item.position = index
        db.session.flush()

    @classmethod
    def get_for_church(cls, church_id: int, track_id: int) -> "ServiceTrack | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == track_id, cls.church_id == church_id)
        )


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

    tracks: Mapped[list["ServiceTrack"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceTrack.position",
    )

    def __repr__(self) -> str:
        return f"<Service {self.name!r} at={self.starts_at}>"

    # -- the whole Sunday, across every track -------------------------------
    #
    # These were relationships before tracks existed and are properties now.
    # Keeping the names means the dashboard, the member app and every count on
    # a card go on reading "the whole Sunday" without caring that it is made of
    # strands, which is exactly what those screens mean.

    @property
    def items(self) -> list["ServiceItem"]:
        return [item for track in self.tracks for item in track.items]

    @property
    def assignments(self) -> list["ServiceAssignment"]:
        return [a for track in self.tracks for a in track.assignments]

    @property
    def needs(self) -> list["ServiceNeed"]:
        return [need for track in self.tracks for need in track.needs]

    @property
    def main_track(self) -> "ServiceTrack | None":
        """The first track. What a one-track Sunday means by "the plan"."""
        return self.tracks[0] if self.tracks else None

    def track_named(self, name: str) -> "ServiceTrack | None":
        wanted = (name or "").strip().lower()
        return next(
            (t for t in self.tracks if (t.name or "").strip().lower() == wanted), None
        )

    def next_track_position(self) -> int:
        return max((track.position for track in self.tracks), default=0) + 1

    @property
    def is_published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    @property
    def status_label(self) -> str:
        return SERVICE_STATUS_LABELS.get(self.status, self.status.title())

    def publish(self) -> None:
        self.status = STATUS_PUBLISHED

    def unpublish(self) -> None:
        self.status = STATUS_DRAFT

    @property
    def is_past(self) -> bool:
        return self.starts_at < utcnow()

    @property
    def accepted_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == ACCEPTED)

    @property
    def waiting_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == INVITED)

    @property
    def declined_count(self) -> int:
        return sum(1 for a in self.assignments if a.status == DECLINED)

    # -- the running order --------------------------------------------------
    #
    # A Sunday has several running orders now, one per track, so the verbs that
    # change one live on the track. Only the read-only view of "the plan" stays
    # here, and it means the first track: on a Sunday with one strand that is
    # the whole plan, which is what every screen written before tracks meant.
    #
    # move_item, reorder_items, renumber and next_position were deliberately
    # NOT kept as delegating shims. A caller that reorders "the service" on a
    # three-track Sunday is a caller with a bug, and an AttributeError at the
    # call site is a far better outcome than silently rearranging the main
    # service when somebody dragged a row in the kids plan.

    @property
    def timed_items(self) -> list[tuple["ServiceItem", datetime | None]]:
        """The first track's running order, with the time each line starts."""
        return self.main_track.timed_items if self.main_track else []

    @property
    def total_minutes(self) -> int:
        """The longest strand, not the sum of them.

        The strands run alongside each other, not one after another. Adding
        them would say a Sunday with a 70 minute service and a 70 minute kids
        programme takes two hours and twenty minutes.
        """
        return max((track.total_minutes for track in self.tracks), default=0)

    @property
    def ends_at(self):
        from datetime import timedelta

        return self.starts_at + timedelta(minutes=self.total_minutes)

    # -- staffing -----------------------------------------------------------

    @property
    def needs_summary(self) -> list[dict]:
        """Every strand's staffing, as one list for the whole Sunday."""
        return staffing_summary(self.needs, self.assignments)

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
        """One of: draft, published, needs_team, waiting, ready.

        Two questions in one chip, in the order they matter. First, can
        anybody see this? An unpublished plan is a draft whatever state its
        team is in, because nobody has been asked yet.

        Then, once it is published, how is the team? `ready` means every slot
        is filled and everybody has answered yes. A plan where half the team
        has not replied is not ready, and calling it ready is how a leader
        finds out on Saturday night.

        This used to return "draft" for a published plan with no roles on it,
        and for a published plan waiting on replies. Both read on screen as
        "you have not published this yet", which was wrong twice over.
        """
        if not self.is_published:
            return "draft"
        if not self.roles_total:
            return "published"
        if self.roles_filled < self.roles_total:
            return "needs_team"
        if all(a.status == ACCEPTED for a in self.assignments):
            return "ready"
        return "waiting"

    @property
    def readiness_label(self) -> str:
        return {
            "draft": "Draft",
            "published": "Published",
            "needs_team": "Needs team",
            "waiting": "Waiting on replies",
            "ready": "Ready",
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
        UniqueConstraint("track_id", "position", name="uq_service_item_position"),
        Index("ix_item_church_song", "church_id", "song_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    track_id: Mapped[int] = mapped_column(
        ForeignKey("service_track.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: Mapped["ServiceTrack"] = relationship(back_populates="items")

    # The Sunday this line belongs to, copied rather than walked to. A track
    # never moves between services, so this cannot drift, and keeping it means
    # "what songs did we sing on this Sunday" stays one join instead of two.
    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Plain, not back-populated: Service.items is a view across tracks now,
    # not a relationship, so there is nothing on the other side to populate.
    service: Mapped["Service"] = relationship()

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
    track_id: Mapped[int] = mapped_column(
        ForeignKey("service_track.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("team_position.id", ondelete="SET NULL")
    )

    track: Mapped["ServiceTrack"] = relationship(back_populates="assignments")
    service: Mapped["Service"] = relationship()
    person: Mapped["Person"] = relationship()  # noqa: F821
    position: Mapped[Optional["TeamPosition"]] = relationship()

    # Denormalized so the record still reads correctly after a position is
    # renamed or deleted. "Drums, 12 March" should not become "None, 12 March".
    position_name: Mapped[Optional[str]] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=INVITED)
    responded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # When the volunteer was actually asked, as opposed to when a leader put
    # them on the plan. Those are different moments and conflating them is how
    # a leader believes they sent invites they never sent.
    invited_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # The credential in an emailed accept or decline link.
    #
    # Scoped to one assignment and nothing else: it answers one question about
    # one Sunday and cannot read a profile, see a roster, or sign anybody in.
    # That narrowness is what makes it safe to put in an email, which is the
    # whole point: a volunteer answering from a phone on a Tuesday evening
    # will not go and find their password first.
    respond_token: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    def __repr__(self) -> str:
        return f"<ServiceAssignment person={self.person_id} {self.status}>"

    def ensure_respond_token(self) -> str:
        """Mint the token on first use. Callers must commit."""
        import secrets

        if not self.respond_token:
            self.respond_token = secrets.token_urlsafe(32)
        return self.respond_token

    @property
    def was_invited(self) -> bool:
        return self.invited_at is not None

    @property
    def has_answered(self) -> bool:
        return self.status in (ACCEPTED, DECLINED)

    @classmethod
    def by_respond_token(cls, token: str) -> "ServiceAssignment | None":
        """Looked up by token alone, with no church in context.

        Deliberately not tenant scoped: the link arrives in an email, is
        opened cold, and the token is the only thing identifying anything. A
        64 character secret is the tenancy check.
        """
        token = (token or "").strip()
        if len(token) < 20:
            return None
        return db.session.scalar(db.select(cls).where(cls.respond_token == token))

    @property
    def team_id(self) -> int | None:
        """The team this slot belongs to, or None.

        None is a real case, not a bug: somebody asked to help with no formal
        position has no team, and a position deleted after the fact leaves
        position_name behind but nulls the link.
        """
        return self.position.team_id if self.position is not None else None

    @property
    def team_name(self) -> str | None:
        if self.position is None or self.position.team is None:
            return None
        return self.position.team.name

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
        UniqueConstraint("track_id", "position_id", name="uq_service_need_position"),
        Index("ix_service_need_church", "church_id", "service_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    track_id: Mapped[int] = mapped_column(
        ForeignKey("service_track.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: Mapped["ServiceTrack"] = relationship(back_populates="needs")

    service_id: Mapped[int] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )

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


def add_track(service, service_type=None, name: str | None = None) -> "ServiceTrack":
    """Put one strand on a Sunday, preloaded from its type. Caller commits.

    A copy, never a reference, for the same reason the old service-level build
    was: editing this week cannot rewrite the template, and editing the
    template cannot rewrite a Sunday that already went out.
    """
    track = ServiceTrack(
        church_id=service.church_id,
        service_id=service.id,
        service_type_id=service_type.id if service_type else None,
        name=(name or (service_type.name if service_type else "Service"))[:120],
        position=service.next_track_position(),
    )
    db.session.add(track)
    db.session.flush()

    if service_type is not None:
        _fill_from_type(track, service_type)
        db.session.flush()
    return track


def _fill_from_type(track, service_type) -> None:
    """Copy a type's usual plan and staffing onto one strand."""
    for template in service_type.template_items:
        db.session.add(
            ServiceItem(
                church_id=track.church_id,
                service_id=track.service_id,
                track_id=track.id,
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
                church_id=track.church_id,
                service_id=track.service_id,
                track_id=track.id,
                position_id=need.position_id,
                position_name=need.position.name if need.position else None,
                wanted=need.wanted,
            )
        )


def build_from_type(church_id: int, service_type, name: str, starts_at,
                    types=None) -> Service:
    """Create a Sunday with a track for each strand it runs.

    `types` is what this Sunday is made of: the main service, kids, operations.
    Passing none falls back to the single type given, which is what a one-off
    service still is, and passing nothing at all leaves the Sunday with the one
    empty strand the model guarantees it.

    The tracks are attached before the first flush on purpose. A Service
    flushed without any gets a default one, and this would otherwise produce a
    Sunday with four strands when it was asked for three.
    """
    service = Service(
        church_id=church_id,
        service_type_id=service_type.id if service_type else None,
        name=name[:160] or "Sunday",
        starts_at=starts_at,
    )

    wanted = [t for t in (list(types) if types else [service_type]) if t is not None]
    for index, one in enumerate(wanted, start=1):
        service.tracks.append(
            ServiceTrack(
                church_id=church_id,
                service_type_id=one.id,
                name=one.name[:120],
                position=index,
            )
        )

    db.session.add(service)
    db.session.flush()

    # Now the tracks have ids, so their plans can be copied in. A copy, never a
    # reference: editing this week cannot rewrite the template, and editing the
    # template cannot rewrite a Sunday that already went out.
    for track in service.tracks:
        if track.service_type is None:
            continue
        _fill_from_type(track, track.service_type)
    db.session.flush()

    return service


def copy_plan(source: Service, target: Service) -> int:
    """Copy a Sunday's whole plan onto another Sunday, track by track.

    Replaces rather than appends: "copy last week" means this week looks like
    last week, not like both weeks stacked.

    Tracks are matched by name, which is what a leader means. Last week's kids
    plan copies onto this week's kids plan even though they are different rows,
    and a strand the source had but the target does not is created. A strand
    the target has and the source does not is emptied rather than left holding
    a plan from a Sunday it was never copied from.

    Assignments are deliberately not copied. Who served last week is not who is
    available this week, and a plan that arrives pre-filled with names nobody
    asked is how a volunteer finds out they are playing by reading it on
    Sunday.
    """
    copied = 0

    for source_track in source.tracks:
        target_track = target.track_named(source_track.name)
        if target_track is None:
            target_track = add_track(
                target, source_track.service_type, name=source_track.name
            )

        for item in list(target_track.items):
            db.session.delete(item)
        db.session.flush()

        for item in source_track.items:
            db.session.add(
                ServiceItem(
                    church_id=target.church_id,
                    service_id=target.id,
                    track_id=target_track.id,
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

        if not target_track.needs:
            for need in source_track.needs:
                db.session.add(
                    ServiceNeed(
                        church_id=target.church_id,
                        service_id=target.id,
                        track_id=target_track.id,
                        position_id=need.position_id,
                        position_name=need.position_name,
                        wanted=need.wanted,
                    )
                )

    # A strand this Sunday has that last week did not. Leaving its old plan in
    # place would mean "copy last week" quietly kept something from a week
    # nobody chose.
    copied_names = {(t.name or "").strip().lower() for t in source.tracks}
    for track in target.tracks:
        if (track.name or "").strip().lower() in copied_names:
            continue
        for item in list(track.items):
            db.session.delete(item)
    db.session.flush()

    return copied


# ---------------------------------------------------------------------------
# A Sunday always has somewhere to put a plan
# ---------------------------------------------------------------------------
#
# `delete_track` refuses to remove the last strand because a Sunday with none
# has nowhere to hold a running order and no way to add one. That is an
# invariant, so it is enforced where the row is made rather than left to every
# caller to remember: a Service flushed without a track gets one.
#
# This is deliberately the ONLY thing filled in automatically. An item or an
# assignment still has to name its strand, because a row that quietly lands on
# the main service when somebody meant kids is the failure this whole model
# exists to prevent, and an error at the call site is the better outcome.

@event.listens_for(db.session, "before_flush")
def _every_sunday_has_a_strand(session, _flush_context, _instances):
    for obj in session.new:
        if isinstance(obj, Service) and not obj.tracks:
            obj.tracks.append(
                ServiceTrack(
                    church_id=obj.church_id,
                    name=(obj.name or "Service")[:120],
                    position=1,
                    service_type_id=obj.service_type_id,
                )
            )
