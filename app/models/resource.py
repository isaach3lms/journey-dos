"""Resources: what a church writes and publishes to its own people.

Unlike stages and notification categories, this content is **tenant data, not
Python**. Stages are shape, the same seven for everyone until a church asks
otherwise. A five day plan on Hebrews is something The Journey Church wrote and
owns, so it lives in rows, on their side of the tenant boundary, and it appears
inside their app under their name with no other brand on it.

Progress is stored as one row per person per session completed. Absence means
not done. That is deliberately append-only: a completion is a fact about a
moment, and it survives a plan being edited afterwards, which a boolean column
on a join row would not.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

KIND_READING_PLAN = "reading_plan"
KIND_NEXT_STEPS = "next_steps"
KIND_TRAINING = "training"
KIND_STUDY = "study"
KIND_COURSE = "course"

RESOURCE_KINDS = (
    KIND_READING_PLAN,
    KIND_NEXT_STEPS,
    KIND_TRAINING,
    KIND_STUDY,
    KIND_COURSE,
)

KIND_LABELS = {
    KIND_READING_PLAN: "Reading plan",
    KIND_NEXT_STEPS: "Next steps path",
    KIND_TRAINING: "Training",
    KIND_STUDY: "Bible study",
    KIND_COURSE: "Course",
}

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"
RESOURCE_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED, STATUS_ARCHIVED)

STATUS_LABELS = {
    STATUS_DRAFT: "Draft",
    STATUS_PUBLISHED: "Published",
    STATUS_ARCHIVED: "Archived",
}

# Cover artwork: six gradient presets drawn from the church's own brand
# tokens in CSS (.cover-0 to .cover-5), so a rebrand in app/brand.py recolours
# every cover without touching a row. Stored as a number, not a colour.
COVER_COUNT = 6

# A church can put its own artwork on a resource instead of a gradient. Stored
# in the database with everything else a church uploads, for the reason in
# app/models/teamfile.py: Render wipes the disk on every deploy.
TAG_STAGE = "stage"
TAG_THEME = "theme"
TAG_KINDS = (TAG_STAGE, TAG_THEME)

# Themes are whatever a church types. Long enough for "Marriage and family",
# short enough to stay a label rather than a sentence.
MAX_THEME_LENGTH = 60

_TAG_KIND_LIST = ", ".join(f"'{k}'" for k in TAG_KINDS)

_KIND_LIST = ", ".join(f"'{k}'" for k in RESOURCE_KINDS)
_STATUS_LIST = ", ".join(f"'{s}'" for s in RESOURCE_STATUSES)


class Resource(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "resource"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_LIST})", name="ck_resource_kind"),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_resource_status"),
        Index("ix_resource_church_status", "church_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default=KIND_READING_PLAN)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)
    cover: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # The church's own artwork, when they have uploaded some. Deferred, or
    # every list of resources would drag every image into memory to print a
    # title. thumb_set_at is what the URL carries, so a replaced image is not
    # served from a browser cache for a week.
    thumb_data: Mapped[Optional[bytes]] = deferred(mapped_column(LargeBinary))
    thumb_type: Mapped[Optional[str]] = mapped_column(String(40))
    thumb_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    thumb_set_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    sessions: Mapped[list["ResourceSession"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
        order_by="ResourceSession.position",
    )

    tags: Mapped[list["ResourceTag"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
        order_by="ResourceTag.value",
    )

    def __repr__(self) -> str:
        return f"<Resource {self.title!r} {self.status} church={self.church_id}>"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.replace("_", " ").title())

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def is_published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    def publish(self) -> None:
        """Refuse to publish an empty plan.

        A member tapping into a published resource with no days would see a
        blank screen and conclude the app is broken. Blocking it here is one
        line; explaining it to a pastor afterwards is not.
        """
        if not self.sessions:
            raise ValueError(
                "This has no days in it yet. Add at least one before publishing, "
                "or the people who open it will find an empty screen."
            )
        self.status = STATUS_PUBLISHED
        self.published_at = utcnow()

    def unpublish(self) -> None:
        self.status = STATUS_DRAFT
        self.published_at = None

    def next_position(self) -> int:
        return max((s.position for s in self.sessions), default=0) + 1

    # -- artwork ------------------------------------------------------------

    @property
    def has_thumb(self) -> bool:
        return bool(self.thumb_bytes)

    @property
    def thumb_version(self) -> str:
        """Cache key for the image URL. Changes when the image changes."""
        return str(int(self.thumb_set_at.timestamp())) if self.thumb_set_at else "0"

    def set_thumb(self, data: bytes, content_type: str) -> None:
        self.thumb_data = data
        self.thumb_type = content_type
        self.thumb_bytes = len(data)
        self.thumb_set_at = utcnow()

    def clear_thumb(self) -> None:
        """Back to the gradient. The row stays, the bytes go."""
        self.thumb_data = None
        self.thumb_type = None
        self.thumb_bytes = 0
        self.thumb_set_at = None

    # -- tags ---------------------------------------------------------------

    @property
    def stage_codes(self) -> list[str]:
        from app.stages import stage_order

        codes = [t.value for t in self.tags if t.kind == TAG_STAGE]
        return sorted(codes, key=stage_order)

    @property
    def stage_labels(self) -> list[str]:
        from app.stages import stage_label

        return [stage_label(code) for code in self.stage_codes]

    @property
    def themes(self) -> list[str]:
        return sorted((t.value for t in self.tags if t.kind == TAG_THEME), key=str.lower)

    def set_tags(self, kind: str, values) -> None:
        """Replace every tag of one kind. The other kind is left alone."""
        wanted = list(dict.fromkeys(values))
        for tag in [t for t in self.tags if t.kind == kind and t.value not in wanted]:
            self.tags.remove(tag)
        have = {t.value for t in self.tags if t.kind == kind}
        for value in wanted:
            if value not in have:
                self.tags.append(
                    ResourceTag(church_id=self.church_id, kind=kind, value=value)
                )

    @property
    def cover_class(self) -> str:
        return f"cover-{self.cover if 0 <= (self.cover or 0) < COVER_COUNT else 0}"

    @property
    def total_minutes(self) -> int:
        return sum(s.minutes or 0 for s in self.sessions)

    def move_session(self, session, direction: int) -> bool:
        """Swap a session with its neighbour. Returns False at either end.

        Positions are unique per resource, so the swap goes through a
        temporary slot rather than writing a duplicate for an instant.
        """
        ordered = sorted(self.sessions, key=lambda s: s.position)
        index = ordered.index(session)
        target = index + direction
        if target < 0 or target >= len(ordered):
            return False
        other = ordered[target]
        mine, theirs = session.position, other.position
        session.position = -1
        db.session.flush()
        other.position = mine
        db.session.flush()
        session.position = theirs
        db.session.flush()
        return True

    def renumber(self) -> None:
        """Close gaps after a delete so members read Day 1, 2, 3, not 1, 3, 4."""
        ordered = sorted(self.sessions, key=lambda s: s.position)
        # Move everything out of the way first, then back in order, so the
        # unique constraint never sees two rows on one position.
        for i, session in enumerate(ordered):
            session.position = -(i + 1)
        db.session.flush()
        for i, session in enumerate(ordered):
            session.position = i + 1
        db.session.flush()

    # -- lookups, all tenant scoped -----------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, resource_id: int) -> "Resource | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == resource_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(
        cls,
        church_id: int,
        published_only: bool = False,
        stage: str | None = None,
        theme: str | None = None,
    ):
        query = db.select(cls).where(cls.church_id == church_id)
        if published_only:
            query = query.where(cls.status == STATUS_PUBLISHED)
        else:
            query = query.where(cls.status != STATUS_ARCHIVED)

        # A filter per tag kind, each its own EXISTS. Two joins would return
        # one row per matching tag and silently double a resource carrying
        # two of them.
        for kind, value in ((TAG_STAGE, stage), (TAG_THEME, theme)):
            if value:
                query = query.where(
                    db.select(ResourceTag.id)
                    .where(
                        ResourceTag.resource_id == cls.id,
                        ResourceTag.kind == kind,
                        ResourceTag.value == value,
                    )
                    .exists()
                )
        return query.order_by(cls.status, cls.title)

    @classmethod
    def published_for_member(cls, church_id: int, resource_id: int) -> "Resource | None":
        """A draft is invisible to a member, not merely unlinked.

        Guessing an id must not reveal work in progress.
        """
        return db.session.scalar(
            db.select(cls).where(
                cls.id == resource_id,
                cls.church_id == church_id,
                cls.status == STATUS_PUBLISHED,
            )
        )


class ResourceSession(TenantScoped, TimestampMixin, db.Model):
    """One day, lesson, or step."""

    __tablename__ = "resource_session"
    __table_args__ = (
        UniqueConstraint("resource_id", "position", name="uq_resource_session_position"),
        Index("ix_session_church_resource", "church_id", "resource_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource: Mapped["Resource"] = relationship(back_populates="sessions")

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # A reference, not the text. Scripture arrives at increment 8, and storing
    # a passage here would mean two places to fix when the translation changes.
    passage_ref: Mapped[Optional[str]] = mapped_column(String(120))

    body: Mapped[Optional[str]] = mapped_column(Text)
    question: Mapped[Optional[str]] = mapped_column(Text)
    # Rough reading time. Optional: a plan reads fine without it, and a
    # guessed number is worse than none.
    minutes: Mapped[Optional[int]] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<ResourceSession {self.position}. {self.title!r}>"

    @property
    def rendered_body(self):
        from app.markup import render

        return render(self.body)

    @classmethod
    def get_for_church(cls, church_id: int, session_id: int) -> "ResourceSession | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == session_id, cls.church_id == church_id)
        )


class SessionCompletion(TenantScoped, TimestampMixin, db.Model):
    """One person finished one session. Append only."""

    __tablename__ = "session_completion"
    __table_args__ = (
        UniqueConstraint(
            "person_id", "session_id", name="uq_session_completion_session_id"
        ),
        Index("ix_completion_church_resource", "church_id", "resource_id"),
        Index("ix_completion_church_person", "church_id", "person_id", "resource_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("resource_session.id", ondelete="CASCADE"), nullable=False, index=True
    )

    completed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<SessionCompletion person={self.person_id} session={self.session_id}>"

    # -- progress -----------------------------------------------------------

    @classmethod
    def completed_session_ids(cls, church_id: int, person_id: int, resource_id: int) -> set[int]:
        return set(
            db.session.scalars(
                db.select(cls.session_id).where(
                    cls.church_id == church_id,
                    cls.person_id == person_id,
                    cls.resource_id == resource_id,
                )
            )
        )

    @classmethod
    def started_counts(cls, church_id: int) -> dict[int, int]:
        """How many distinct people have completed at least one session.

        One grouped query for every resource, not one query per card. A
        resources screen with twenty plans should still be two queries.
        """
        rows = db.session.execute(
            db.select(cls.resource_id, func.count(func.distinct(cls.person_id)))
            .where(cls.church_id == church_id)
            .group_by(cls.resource_id)
        ).all()
        return {resource_id: count for resource_id, count in rows}

    @classmethod
    def mark(cls, church_id: int, person_id: int, session) -> "SessionCompletion | None":
        """Record a completion. Returns None if it was already recorded.

        Idempotent because a double tap on a phone is normal, and because a
        second row would inflate every count that reads this table.
        """
        existing = db.session.scalar(
            db.select(cls).where(
                cls.person_id == person_id, cls.session_id == session.id
            )
        )
        if existing is not None:
            return None

        completion = cls(
            church_id=church_id,
            person_id=person_id,
            resource_id=session.resource_id,
            session_id=session.id,
        )
        db.session.add(completion)
        return completion

    @classmethod
    def unmark(cls, church_id: int, person_id: int, session_id: int) -> bool:
        """Undo one completion. Tapping the wrong day happens."""
        existing = db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.person_id == person_id,
                cls.session_id == session_id,
            )
        )
        if existing is None:
            return False
        db.session.delete(existing)
        return True


class ResourceTag(TenantScoped, TimestampMixin, db.Model):
    """What a resource is for, and what it is about.

    One table for both, because they are the same shape: a label attached to
    a resource that members filter by. `kind` says which list it belongs to.
    A stage tag holds a stage code from app/stages.py; a theme tag holds
    whatever the church typed.

    A church's themes are the distinct theme values across its resources.
    That means a theme nothing is tagged with stops existing, which is the
    behaviour a church wants: the filter never offers an empty result.
    """

    __tablename__ = "resource_tag"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_TAG_KIND_LIST})", name="ck_resource_tag_kind"),
        UniqueConstraint("resource_id", "kind", "value", name="uq_resource_tag_value"),
        Index("ix_resource_tag_church_kind", "church_id", "kind", "value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource: Mapped["Resource"] = relationship(back_populates="tags")

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(String(MAX_THEME_LENGTH), nullable=False)

    def __repr__(self) -> str:
        return f"<ResourceTag {self.kind}={self.value!r} resource={self.resource_id}>"

    @classmethod
    def themes_for_church(cls, church_id: int, published_only: bool = False) -> list[str]:
        """Every theme in use, alphabetical, case kept as it was typed."""
        query = (
            db.select(cls.value)
            .join(Resource, Resource.id == cls.resource_id)
            .where(cls.church_id == church_id, cls.kind == TAG_THEME)
            .distinct()
        )
        if published_only:
            query = query.where(Resource.status == STATUS_PUBLISHED)
        else:
            query = query.where(Resource.status != STATUS_ARCHIVED)
        return sorted(db.session.scalars(query).all(), key=str.lower)

    @classmethod
    def stages_in_use(cls, church_id: int, published_only: bool = False) -> set[str]:
        """Which stages have anything tagged for them. Filters offer no
        dead ends."""
        query = (
            db.select(cls.value)
            .join(Resource, Resource.id == cls.resource_id)
            .where(cls.church_id == church_id, cls.kind == TAG_STAGE)
            .distinct()
        )
        if published_only:
            query = query.where(Resource.status == STATUS_PUBLISHED)
        else:
            query = query.where(Resource.status != STATUS_ARCHIVED)
        return set(db.session.scalars(query).all())


def bytes_used(church_id: int) -> int:
    """Thumbnails a church is storing. Part of the one file quota."""
    return int(db.session.scalar(
        db.select(func.coalesce(func.sum(Resource.thumb_bytes), 0)).where(
            Resource.church_id == church_id
        )
    ) or 0)
