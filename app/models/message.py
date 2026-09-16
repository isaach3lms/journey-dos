"""Messaging.

Three kinds of conversation, and the differences are authorization rules rather
than cosmetics:

**Announcement.** Church-wide, staff post, everyone reads. There are no
membership rows: visibility is "anyone at this church", because writing 400
rows to say "everyone" and then maintaining them as people join and leave is a
synchronization problem with no upside.

**Room.** Invite only. Membership is the authorization, exactly as it is for
groups. The worship team room is not readable by a curious member.

**Direct.** Exactly two people. The pair is canonicalized on the conversation
so two people cannot end up with two parallel threads depending on who wrote
first.

Messages are soft deleted. A church needs an account of what was said in its
own rooms, and a hard delete means a leader can erase a conversation that
somebody later needs to reference. The body is cleared, the row stays, and the
UI says a message was removed.
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

KIND_ANNOUNCEMENT = "announcement"
KIND_ROOM = "room"
KIND_DIRECT = "direct"
CONVERSATION_KINDS = (KIND_ANNOUNCEMENT, KIND_ROOM, KIND_DIRECT)

KIND_LABELS = {
    KIND_ANNOUNCEMENT: "Church-wide",
    KIND_ROOM: "Room",
    KIND_DIRECT: "Direct",
}

_KIND_LIST = ", ".join(f"'{k}'" for k in CONVERSATION_KINDS)


class Conversation(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "conversation"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_LIST})", name="ck_conversation_kind"),
        # One thread per pair, whoever wrote first.
        UniqueConstraint("church_id", "direct_pair", name="uq_conversation_direct_pair"),
        Index("ix_conversation_church_kind", "church_id", "kind", "is_archived"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=KIND_ROOM)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(String(240))

    # "12:47" for the pair of person ids, smallest first. Null on anything that
    # is not a direct message, so the unique constraint only bites where it
    # should.
    direct_pair: Mapped[Optional[str]] = mapped_column(String(40))

    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    members: Mapped[list["ConversationMember"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.kind} {self.title!r}>"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.title())

    @property
    def is_announcement(self) -> bool:
        return self.kind == KIND_ANNOUNCEMENT

    @property
    def visible_messages(self) -> list["Message"]:
        return [m for m in self.messages if not m.is_deleted]

    @property
    def member_count(self) -> int:
        return len(self.members)

    # -- authorization ------------------------------------------------------

    def can_read(self, person) -> bool:
        """An announcement is for everyone the church has confirmed is real.

        Takes the person rather than an id, because the answer depends on
        approval as well as membership and splitting that across two call
        sites is how the two views drift apart.
        """
        person_id = getattr(person, "id", person)

        if self.is_announcement:
            # A stranger who signed up this morning is not yet part of "the
            # church", so a church-wide message is not yet for them. A named
            # room they were invited to is, because somebody chose to invite
            # them.
            return bool(getattr(person, "is_approved", True))

        return any(m.person_id == person_id for m in self.members)

    def can_post(self, person, is_staff: bool = False) -> bool:
        """Only staff post to an announcement.

        A church-wide broadcast that anyone can reply to stops being an
        announcement and becomes a room nobody chose to join.
        """
        if self.is_archived:
            return False
        if self.is_announcement:
            return is_staff
        return self.can_read(person)

    def membership_for(self, person_id: int) -> "ConversationMember | None":
        for member in self.members:
            if member.person_id == person_id:
                return member
        return None

    def unread_for(self, person_id: int) -> int:
        """Messages since this person last opened it.

        An announcement has no membership row for most people, so there is no
        per-person read mark and nothing to count. Returning zero is honest:
        the alternative is inventing a row for every person at the church.
        """
        membership = self.membership_for(person_id)
        if membership is None:
            return 0
        since = membership.last_read_at
        return sum(
            1
            for message in self.messages
            if not message.is_deleted
            and message.author_person_id != person_id
            and (since is None or message.sent_at > since)
        )

    # -- lookups ------------------------------------------------------------

    @staticmethod
    def pair_key(a: int, b: int) -> str:
        low, high = sorted((int(a), int(b)))
        return f"{low}:{high}"

    @classmethod
    def get_for_church(cls, church_id: int, conversation_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == conversation_id, cls.church_id == church_id)
        )

    @classmethod
    def for_church(cls, church_id: int, include_archived: bool = False):
        query = db.select(cls).where(cls.church_id == church_id)
        if not include_archived:
            query = query.where(cls.is_archived.is_(False))
        return query.order_by(cls.last_message_at.desc().nullslast(), cls.id.desc())

    @classmethod
    def visible_to(cls, church_id: int, person):
        """Anything this person may read.

        Rooms they belong to always. Announcements only once the church has
        confirmed they are real, which is the same rule `can_read` applies and
        expressed once so the list and the page cannot disagree.
        """
        person_id = getattr(person, "id", person)
        approved = bool(getattr(person, "is_approved", True))

        member_ids = db.select(ConversationMember.conversation_id).where(
            ConversationMember.church_id == church_id,
            ConversationMember.person_id == person_id,
        )
        readable = (
            db.or_(cls.kind == KIND_ANNOUNCEMENT, cls.id.in_(member_ids))
            if approved
            else cls.id.in_(member_ids)
        )
        return (
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.is_archived.is_(False),
                readable,
            )
            .order_by(cls.last_message_at.desc().nullslast(), cls.id.desc())
        )

    @classmethod
    def find_or_create_direct(cls, church_id: int, a_id: int, b_id: int, title: str):
        pair = cls.pair_key(a_id, b_id)
        existing = db.session.scalar(
            db.select(cls).where(cls.church_id == church_id, cls.direct_pair == pair)
        )
        if existing is not None:
            return existing, False

        conversation = cls(
            church_id=church_id, kind=KIND_DIRECT, title=title[:160], direct_pair=pair
        )
        db.session.add(conversation)
        db.session.flush()
        for person_id in (a_id, b_id):
            db.session.add(
                ConversationMember(
                    church_id=church_id,
                    conversation_id=conversation.id,
                    person_id=person_id,
                )
            )
        return conversation, True


class ConversationMember(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "conversation_member"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "person_id", name="uq_conversation_member_person_id"
        ),
        Index("ix_conv_member_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=False, index=True
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="members")
    person: Mapped["Person"] = relationship()  # noqa: F821

    last_read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<ConversationMember person={self.person_id} conv={self.conversation_id}>"

    def mark_read(self) -> None:
        self.last_read_at = utcnow()


class Message(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "message"
    __table_args__ = (
        Index("ix_message_church_conv_time", "church_id", "conversation_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    author_person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    author: Mapped[Optional["Person"]] = relationship()  # noqa: F821

    # Copied so a thread still reads correctly after somebody is archived or
    # their record is removed. "Marcus said" should not become "someone said".
    author_name: Mapped[Optional[str]] = mapped_column(String(120))

    body: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<Message conv={self.conversation_id} by={self.author_name!r}>"

    @property
    def rendered(self):
        from app.markup import render_message

        return render_message(self.body)

    def soft_delete(self) -> None:
        """Clear the words, keep the row.

        A hard delete lets a leader erase a conversation somebody later needs
        to reference, and leaves a gap nobody can see. This leaves a visible
        hole instead.
        """
        self.is_deleted = True
        self.deleted_at = utcnow()
        self.body = None

    @classmethod
    def get_for_church(cls, church_id: int, message_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == message_id, cls.church_id == church_id)
        )

    @classmethod
    def post(cls, conversation, author_person, body: str) -> "Message":
        message = cls(
            church_id=conversation.church_id,
            conversation_id=conversation.id,
            author_person_id=getattr(author_person, "id", None),
            author_name=getattr(author_person, "full_name", None),
            body=body,
            sent_at=utcnow(),
        )
        db.session.add(message)
        conversation.last_message_at = message.sent_at
        return message

    @classmethod
    def unread_total(cls, church_id: int, person_id: int) -> int:
        """One query for the badge, not one per conversation."""
        rows = db.session.execute(
            db.select(func.count(cls.id))
            .join(
                ConversationMember,
                ConversationMember.conversation_id == cls.conversation_id,
            )
            .where(
                cls.church_id == church_id,
                cls.is_deleted.is_(False),
                cls.author_person_id != person_id,
                ConversationMember.person_id == person_id,
                db.or_(
                    ConversationMember.last_read_at.is_(None),
                    cls.sent_at > ConversationMember.last_read_at,
                ),
            )
        ).scalar()
        return rows or 0
