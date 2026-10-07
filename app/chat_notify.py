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
from app.mail import NotQueued
from app.notify import notify
from app.models.base import utcnow

WINDOW_MINUTES = 30
EXCERPT_CHARS = 300


def notify_announcement(conversation, message, body: str, *,
                        also_email: bool = False) -> tuple[int, int]:
    """Tell the church an announcement was posted. Returns (emailed, pushed).

    Caller commits.

    **Announcements pushed nobody at all.** `notify_new_message` excludes them
    on purpose, because emailing three hundred people every time somebody
    posts to the whole church is how a church teaches its members to filter
    its mail. But that exclusion was written when email was the only channel,
    and it took the notification out with it: a pastor posted an announcement
    in the app and not one phone made a sound unless they also ticked a box
    labelled "email it as well".

    So the two channels split the way they should have from the start. **The
    notification always goes**, because that is what somebody installing a
    church app is asking for and it costs them a glance. **The email goes only
    when the person posting ticked the box**, because that is a letter and it
    cannot be taken back.

    Who counts as "everyone" comes from `app.broadcast` rather than being
    decided here, so this and the Send email screen cannot disagree about it.
    They did disagree before: the email loop here reached children, archived
    people, and self-registered people still waiting for approval, because it
    checked only for an address and consent.
    """
    from app.broadcast import EVERYONE, resolve
    from app.content import MESSAGES as COPY

    emailed = pushed = 0
    try:
        path = url_for("member.chat_thread", conversation_id=conversation.id)
    except Exception:  # noqa: BLE001
        path = "/"

    # One key per post, so a double-submitted form is one announcement. The
    # message id rather than a count of messages in the room: the old key
    # used `len(conversation.messages)`, which two posts in one second can
    # read the same value for.
    stem = f"announcement:{conversation.id}:message:{message.id}"
    excerpt = (body or "").strip()[:EXCERPT_CHARS]

    for person in resolve(conversation.church_id, EVERYONE):
        try:
            result = notify(
                person=person,
                church_id=conversation.church_id,
                category="announcement",
                subject=COPY["email_subject"].format(
                    church=person.church.name, title=conversation.title
                ),
                body_text=body,
                push_title=conversation.title,
                push_body=excerpt[:140],
                url=path,
                # One tag for the whole channel. An announcement replaces the
                # last one rather than stacking: a member who was away for a
                # week comes back to one badge, not nine.
                tag="announcement",
                dedupe_key=f"{stem}:person:{person.id}",
                email=also_email,
            )
        except NotQueued:
            continue
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Announcement notification failed")
            continue
        if result.emailed:
            person.ensure_unsubscribe_token()
            emailed += 1
        pushed += result.pushed

    return emailed, pushed


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
        # Relative, for the push payload. A notification is delivered by a
        # third party and is not the place to teach a browser a hostname.
        path = url_for("member.chat_thread", conversation_id=conversation.id)
    except Exception:  # noqa: BLE001 - outside a request, e.g. a shell
        link = ""
        path = "/"

    queued = 0
    for membership in conversation.members:
        person = membership.person
        if person is None or person.id == author_id:
            continue
        # No email address is not a reason to skip. `notify` pushes somebody
        # whose roster record has no address, and the `not person.email` gate
        # that used to be on this line threw away their notification along
        # with the email they were never going to get. On an app-first roster
        # that is most of a youth group.
        if person.is_archived:
            continue
        # Somebody who blocked this author asked not to see them. An email is
        # the loudest possible way to ignore that.
        if author_id and author_id in PersonBlock.blocked_ids(conversation.church_id, person.id):
            continue
        try:
            # Both channels, through one call. This used to be `queue` alone,
            # which is why somebody who turned app notifications on for chat
            # never got one.
            result = notify(
                person=person,
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
                push_title=f"{name} in {conversation.title}",
                push_body=excerpt[:140],
                url=path,
                # One room replaces its own notification rather than stacking.
                # Nine messages in a back and forth is one badge, not nine.
                tag=f"chat:{conversation.id}",
                dedupe_key=f"chat:{conversation.id}:person:{person.id}:{window}",
            )
            if result.emailed or result.pushed:
                person.ensure_unsubscribe_token()
                queued += 1
        except NotQueued:
            # Opted out, or no address. Both are answers, not errors.
            continue
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Chat notification failed")
            continue
    return queued
