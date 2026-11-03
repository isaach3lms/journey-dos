"""Emails staff send often enough to be tired of writing.

A church that turns the welcome series off is not sending fewer emails, it is
sending the same emails by hand. Typing "Thanks for being with us on Sunday"
from scratch forty times is how writing to new people quietly stops happening
in week three, which is the thing the automation was there to prevent.

So a saved template is the middle: the wording is written once and the person
sending it still reads it, puts their own line in it, and presses send. What
arrives is a note somebody chose to send rather than a note that sent itself.

**Filled when it is loaded, not when it is sent.** A template holding
`{first_name}` becomes "Amy" the moment it is pulled into the form, so what
staff edit and what the person receives are the same words. Filling at send
time would mean approving a draft full of braces and trusting the system
about the rest.

**Placeholders are replaced literally**, never through `str.format`. These
are written by staff, not checked into the repo: a template mentioning "{the
details}" or a price in braces would raise on format, and format strings can
reach attributes of whatever they are given. See `fill`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

NAME_MAX = 120
SUBJECT_MAX = 200

# What a saved template is for. The same row shape serves both, because the
# thing being saved is the same thing: words somebody writes often enough to
# be tired of writing. An email has a subject and a notification category; a
# chat message has neither, and carries empty strings in those columns.
#
# The table is still called `email_template`, which is where it started. A
# table rename on a live church to make a name read better is not a trade
# worth making, so the name is historical and this comment is the signpost.
TEMPLATE_EMAIL = "email"
TEMPLATE_MESSAGE = "message"
TEMPLATE_KINDS = (TEMPLATE_EMAIL, TEMPLATE_MESSAGE)

# What a template may say, and nothing else. A short list on purpose: every
# token here is one more thing that has to be true of every person a template
# is ever used on, and "{spouse_name}" that is blank half the time produces
# letters addressed to a gap.
PLACEHOLDERS = ("first_name", "last_name", "full_name", "church")


def fill(text: str, person, church) -> str:
    """Put the person into a staff-written template.

    Literal replacement, deliberately, where `app.sequences.render` uses
    `str.format`. That one reads templates checked into this repo by us; this
    one reads whatever a staff member typed into a box. Two differences
    follow:

    - A brace that is not one of ours survives. "Bring {whatever you like}"
      is a sentence somebody might reasonably write, and `str.format` raises
      KeyError on it, losing their email at the moment they press send.
    - A format string can reach attributes of what it is given, so
      `{person.church.id}` and worse are readable through one. Nothing is
      passed to a formatter here, so there is nothing to reach through.
    """
    if not text:
        return text
    values = {
        "first_name": person.first_name or "",
        "last_name": person.last_name or "",
        "full_name": person.full_name,
        "church": church.name,
    }
    for token in PLACEHOLDERS:
        text = text.replace("{" + token + "}", values[token])
    return text


class EmailTemplate(TenantScoped, TimestampMixin, db.Model):
    """One saved email, belonging to a church."""

    __tablename__ = "email_template"
    __table_args__ = (
        Index("ix_template_church_name", "church_id", "name"),
        Index("ix_template_church_kind", "church_id", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)

    # An email or a chat message. Defaults to email, which is what every row
    # written before this column existed is.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TEMPLATE_EMAIL, server_default=TEMPLATE_EMAIL
    )
    # Which notification category it sends under, so picking a template sets
    # the whole send and not most of it. Plain text, not a foreign key: the
    # categories are a Python structure, like the stages and the sequences.
    category: Mapped[str] = mapped_column(String(40), nullable=False)

    subject: Mapped[str] = mapped_column(String(SUBJECT_MAX), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Who wrote it, for the person who finds it a year later and wonders.
    # SET NULL rather than CASCADE: a template outlives the staff member who
    # wrote it, and a church losing its welcome email because somebody left
    # is the worst possible reading of "delete this account".
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    created_by: Mapped[Optional["User"]] = relationship()  # noqa: F821

    # What the picker sorts by. A church settles on two or three templates
    # it actually uses, and those should not sit under eleven it tried once.
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # Removed from the picker, kept on the emails already sent from it.
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    def __repr__(self) -> str:
        return f"<EmailTemplate {self.name!r} church={self.church_id}>"

    def used(self) -> None:
        """Record that somebody sent from it. Caller commits."""
        self.used_count = (self.used_count or 0) + 1
        self.last_used_at = utcnow()

    def rendered(self, person, church) -> tuple[str, str]:
        """Subject and body with this person in them."""
        return fill(self.subject, person, church), fill(self.body, person, church)

    @property
    def preview(self) -> str:
        """The first line, for a list that has to fit on one row."""
        first = (self.body or "").strip().splitlines()
        return first[0][:90] if first else ""

    # -- reading ------------------------------------------------------------

    @classmethod
    def for_church(
        cls,
        church_id: int,
        include_archived: bool = False,
        kind: str = TEMPLATE_EMAIL,
    ):
        """Most used first, then alphabetical.

        Use before name, because the two a church sends every week are what
        somebody is reaching for, and alphabetical order puts them wherever
        their first letter happens to fall.

        Always filtered by kind. An email picker that offered chat messages
        would offer templates with no subject line, and a chat picker that
        offered emails would quietly drop the subject somebody wrote.
        """
        query = db.select(cls).where(cls.church_id == church_id, cls.kind == kind)
        if not include_archived:
            query = query.where(cls.is_archived.is_(False))
        return query.order_by(cls.used_count.desc(), cls.name)

    @classmethod
    def get_for_church(
        cls, church_id: int, template_id: int, kind: str | None = None
    ) -> "EmailTemplate | None":
        query = db.select(cls).where(
            cls.id == template_id, cls.church_id == church_id
        )
        if kind is not None:
            query = query.where(cls.kind == kind)
        return db.session.scalar(query)

    @classmethod
    def named(
        cls, church_id: int, name: str, kind: str = TEMPLATE_EMAIL
    ) -> "EmailTemplate | None":
        """An existing template with this name, archived ones included.

        Saving over a name is how somebody edits a template: they open the
        one they use, change a line, and save it under the same name. A
        second row with the same name would leave them picking between two
        entries they cannot tell apart.

        Scoped to the kind, so a church may have an email and a chat message
        both called "First visit". They are two different things that happen
        to be about the same moment, and saving one must not overwrite the
        other.
        """
        cleaned = (name or "").strip()
        if not cleaned:
            return None
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.kind == kind,
                db.func.lower(cls.name) == cleaned.lower(),
            )
        )
