"""Groups, meetings, and RSVPs.

A group leader is a `GroupMembership` with `role='leader'`, not a `User` with
role `leader`. The two are different things and conflating them would be a
quiet authorization bug: the woman who hosts the Wednesday women's group is a
leader *of that group* and has no business in the church-wide roster, while a
staff member with the `leader` login role may lead no group at all.

Meetings are stored as aware UTC and rendered through `app/timeutil.py` in the
church's own zone. A group in Jackson reading 6:00pm because the server runs on
UTC would have people arriving an hour late.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

ROLE_MEMBER = "member"
ROLE_LEADER = "leader"
GROUP_ROLES = (ROLE_MEMBER, ROLE_LEADER)

RSVP_GOING = "going"
RSVP_NOT_GOING = "not_going"
RSVP_MAYBE = "maybe"
RSVP_CHOICES = (RSVP_GOING, RSVP_MAYBE, RSVP_NOT_GOING)

RSVP_LABELS = {
    RSVP_GOING: "Going",
    RSVP_MAYBE: "Maybe",
    RSVP_NOT_GOING: "Can't make it",
}

_ROLE_LIST = ", ".join(f"'{r}'" for r in GROUP_ROLES)
_RSVP_LIST = ", ".join(f"'{r}'" for r in RSVP_CHOICES)


class Group(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "group"
    __table_args__ = (
        Index("ix_group_church_active", "church_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Free text on purpose. "Wednesdays at the Hollands' house" is more useful
    # to a member than a structured recurrence rule, and a group that meets
    # fortnightly except in August is not worth modelling.
    meeting_pattern: Mapped[Optional[str]] = mapped_column(String(200))
    location: Mapped[Optional[str]] = mapped_column(String(200))

    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list["GroupMembership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    meetings: Mapped[list["GroupMeeting"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="GroupMeeting.meets_at",
    )

    def __repr__(self) -> str:
        return f"<Group {self.name!r} church={self.church_id}>"

    @property
    def member_count(self) -> int:
        return len(self.memberships)

    @property
    def leaders(self) -> list["GroupMembership"]:
        return [m for m in self.memberships if m.role == ROLE_LEADER]

    @property
    def leader_names(self) -> str:
        return ", ".join(m.person.full_name for m in self.leaders if m.person)

    def has_person(self, person_id: int) -> bool:
        return any(m.person_id == person_id for m in self.memberships)

    def next_meeting(self) -> "GroupMeeting | None":
        upcoming = [m for m in self.meetings if m.meets_at >= utcnow()]
        return upcoming[0] if upcoming else None

    @classmethod
    def get_for_church(cls, church_id: int, group_id: int) -> "Group | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == group_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int, active_only: bool = True):
        query = db.select(cls).where(cls.church_id == church_id)
        if active_only:
            query = query.where(cls.is_active.is_(True))
        return query.order_by(cls.name)

    @classmethod
    def for_person(cls, church_id: int, person_id: int):
        return (
            db.select(cls)
            .join(GroupMembership, GroupMembership.group_id == cls.id)
            .where(
                cls.church_id == church_id,
                cls.is_active.is_(True),
                GroupMembership.person_id == person_id,
            )
            .order_by(cls.name)
        )

    @classmethod
    def people_in_a_group(cls, church_id: int) -> int:
        """Distinct people in at least one active group.

        This is the "Members in a group" ratio on the dashboard health card,
        which until now had nothing behind it.
        """
        return db.session.scalar(
            db.select(func.count(func.distinct(GroupMembership.person_id)))
            .join(cls, cls.id == GroupMembership.group_id)
            .where(cls.church_id == church_id, cls.is_active.is_(True))
        ) or 0


class GroupMembership(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "group_membership"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_LIST})", name="ck_group_membership_role"),
        UniqueConstraint("group_id", "person_id", name="uq_group_membership_person_id"),
        Index("ix_membership_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int] = mapped_column(
        ForeignKey("group.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )

    group: Mapped["Group"] = relationship(back_populates="memberships")
    person: Mapped["Person"] = relationship()  # noqa: F821

    role: Mapped[str] = mapped_column(String(20), nullable=False, default=ROLE_MEMBER)
    joined_on: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<GroupMembership person={self.person_id} group={self.group_id} {self.role}>"

    @property
    def is_leader(self) -> bool:
        return self.role == ROLE_LEADER


class GroupMeeting(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "group_meeting"
    __table_args__ = (
        Index("ix_meeting_church_time", "church_id", "meets_at"),
        Index("ix_meeting_group_time", "group_id", "meets_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int] = mapped_column(
        ForeignKey("group.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group: Mapped["Group"] = relationship(back_populates="meetings")

    # Aware UTC. Rendered through app/timeutil.py in the church's zone.
    meets_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(200))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    rsvps: Mapped[list["MeetingRSVP"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<GroupMeeting group={self.group_id} at={self.meets_at}>"

    @property
    def is_past(self) -> bool:
        return self.meets_at < utcnow()

    @property
    def going_count(self) -> int:
        return sum(1 for r in self.rsvps if r.response == RSVP_GOING)

    def response_for(self, person_id: int) -> str | None:
        for rsvp in self.rsvps:
            if rsvp.person_id == person_id:
                return rsvp.response
        return None

    @classmethod
    def get_for_church(cls, church_id: int, meeting_id: int) -> "GroupMeeting | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == meeting_id, cls.church_id == church_id)
        )

    @classmethod
    def upcoming_for_person(cls, church_id: int, person_id: int, limit: int = 5):
        return (
            db.select(cls)
            .join(Group, Group.id == cls.group_id)
            .join(GroupMembership, GroupMembership.group_id == Group.id)
            .where(
                cls.church_id == church_id,
                cls.meets_at >= utcnow(),
                Group.is_active.is_(True),
                GroupMembership.person_id == person_id,
            )
            .order_by(cls.meets_at)
            .limit(limit)
        )


class MeetingRSVP(TenantScoped, TimestampMixin, db.Model):
    """One person's answer for one meeting. Changing it updates the row.

    Unlike a session completion, which is a fact about a moment and stays,
    an RSVP is a current intention. "I said yes last Tuesday" is not useful;
    "she is coming" is.
    """

    __tablename__ = "meeting_rsvp"
    __table_args__ = (
        CheckConstraint(f"response IN ({_RSVP_LIST})", name="ck_meeting_rsvp_response"),
        UniqueConstraint("meeting_id", "person_id", name="uq_meeting_rsvp_person_id"),
        Index("ix_rsvp_church_meeting", "church_id", "meeting_id", "response"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("group_meeting.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )

    meeting: Mapped["GroupMeeting"] = relationship(back_populates="rsvps")
    person: Mapped["Person"] = relationship()  # noqa: F821

    response: Mapped[str] = mapped_column(String(20), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<MeetingRSVP person={self.person_id} {self.response}>"

    @property
    def response_label(self) -> str:
        return RSVP_LABELS.get(self.response, self.response.title())

    @classmethod
    def set(cls, church_id: int, meeting, person_id: int, response: str) -> "MeetingRSVP":
        if response not in RSVP_CHOICES:
            raise ValueError(f"{response!r} is not an RSVP.")

        existing = db.session.scalar(
            db.select(cls).where(
                cls.meeting_id == meeting.id, cls.person_id == person_id
            )
        )
        if existing is not None:
            existing.response = response
            existing.responded_at = utcnow()
            return existing

        rsvp = cls(
            church_id=church_id,
            meeting_id=meeting.id,
            person_id=person_id,
            response=response,
        )
        db.session.add(rsvp)
        return rsvp
