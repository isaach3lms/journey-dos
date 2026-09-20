"""Emailing people when somebody posts in their room.

Three rules keep this from becoming the reason people leave the app.

**Rooms only.** A church-wide announcement already has its own "email it"
button that staff choose deliberately. Emailing everyone automatically every
time somebody posts to the whole church is how a church teaches its members
to filter its mail.

**One email per person per room per half hour.** A back-and-forth of nine
messages is one notification, not nine. The outbox's dedupe key does this: a
second message inside the window finds an email already queued and stops.

**Everything a person has chosen is honoured.** Somebody who turned chat
email off gets nothing, somebody who blocked the author gets nothing, and the
author never emails themselves.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app, url_for

from app.content import MESSAGES
from app.extensions import db
from app.mail import NotQueued, queue
from app.models.base import utcnow

WINDOW_MINUTES = 30
EXCERPT_CHARS = 300


def _window_key(now) -> str:
    """The half hour this message falls in, as a stable string."""
    stamp = now.replace(second=0, microsecond=0)
    return f"{stamp.date().isoformat()}:{stamp.hour}:{0 if stamp.minute < WINDOW_MINUTES else 1}"


def notify_new_message(conversation, message, author_person=None, author_name=None) -> int:
    """Queue an email to everyone else in the room. Caller commits.

    Returns how many were queued. Never raises: a chat post must not fail
    because a notification could not be built.
    """
    from app.models.message import KIND_ANNOUNCEMENT
    from app.models.moderation import PersonBlock

    if conversation.kind == KIND_ANNOUNCEMENT or conversation.is_archived:
        return 0

    author_id = getattr(author_person, "id", None)
    name = author_name or getattr(author_person, "full_name", None) or "Someone"
    body = (message.body or "").strip()
    excerpt = body[:EXCERPT_CHARS] + ("…" if len(body) > EXCERPT_CHARS else "")
    window = _window_key(utcnow())

    try:
        link = url_for("member.chat_thread", conversation_id=conversation.id,
                       _external=True, _scheme="https")
    except Exception:  # noqa: BLE001 - outside a request, e.g. a shell
        link = ""

    queued = 0
    for membership in conversation.members:
        person = membership.person
        if person is None or person.id == author_id or not person.email:
            continue
        if person.is_archived:
            continue
        # Somebody who blocked this author asked not to see them. An email is
        # the loudest possible way to ignore that.
        if author_id and author_id in PersonBlock.blocked_ids(conversation.church_id, person.id):
            continue
        try:
            if queue(
                church_id=conversation.church_id,
                category="chat",
                subject=MESSAGES["chat_email_subject"].format(name=name, room=conversation.title),
                body_text=MESSAGES["chat_email_body"].format(
                    first_name=person.first_name,
                    name=name,
                    room=conversation.title,
                    excerpt=excerpt,
                    link=link,
                ),
                person=person,
                dedupe_key=f"chat:{conversation.id}:person:{person.id}:{window}",
            ) is not None:
                person.ensure_unsubscribe_token()
                queued += 1
        except NotQueued:
            # Opted out, or no address. Both are answers, not errors.
            continue
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Chat notification failed")
            continue
    return queued
