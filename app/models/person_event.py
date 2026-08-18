"""The timeline.

One row per thing that happened to a person. This is the table the person
drawer reads, and it is append-only by convention: events are recorded, not
edited, because a timeline that can be rewritten is not a record of anything.

Event kinds are declared here rather than in the database as an enum. Later
increments add their own kinds (a logged phone call at increment 3, a sent
email at increment 4, a check-in at increment 11), and a new kind should be one
constant plus the feature that emits it, not a migration.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# Kinds shipped in increment 2. Later increments append to this tuple.
KIND_CREATED = "created"
KIND_STAGE_CHANGE = "stage_change"
KIND_NOTE = "note"
KIND_IMPORTED = "imported"

EVENT_KINDS = (KIND_CREATED, KIND_STAGE_CHANGE, KIND_NOTE, KIND_IMPORTED)

# How each kind reads in the timeline. Copy lives in a data structure, not in
# the template, so rewording an event never touches markup.
KIND_LABELS = {
    KIND_CREATED: "Added to the roster",
    KIND_STAGE_CHANGE: "Moved a stage",
    KIND_NOTE: "Note",
    KIND_IMPORTED: "Imported",
}


class PersonEvent(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "person_event"
    __table_args__ = (
        # The person drawer's only query: this person, newest first.
        Index("ix_event_church_person_time", "church_id", "person_id", "occurred_at"),
        Index("ix_event_church_kind_time", "church_id", "kind", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person: Mapped["Person"] = relationship(back_populates="events")  # noqa: F821

    kind: Mapped[str] = mapped_column(String(40), nullable=False)

    # What a pastor reads. Written at emit time rather than assembled at read
    # time, so the timeline still says what happened even after the stage
    # labels or the person's name change.
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    # Who or what caused it. Null means the system did.
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    actor_name: Mapped[Optional[str]] = mapped_column(String(120))

    def __repr__(self) -> str:
        return f"<PersonEvent {self.kind} person={self.person_id}>"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.replace("_", " ").title())

    @classmethod
    def record(
        cls,
        person,
        kind: str,
        summary: str,
        detail: str | None = None,
        actor=None,
        occurred_at: datetime | None = None,
    ) -> "PersonEvent":
        """Append an event. The church comes from the person, never an argument.

        Taking `church_id` as a parameter here would make it possible to file
        an event against the wrong tenant by passing the wrong number. Deriving
        it from the person removes that option.
        """
        event = cls(
            church_id=person.church_id,
            person_id=person.id,
            kind=kind,
            summary=summary,
            detail=detail,
            occurred_at=occurred_at or utcnow(),
            actor_user_id=getattr(actor, "id", None),
            actor_name=getattr(actor, "name", None),
        )
        db.session.add(event)
        return event

    @classmethod
    def for_person(cls, church_id: int, person_id: int, limit: int = 50):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.occurred_at.desc(), cls.id.desc())
            .limit(limit)
        )
