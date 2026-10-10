"""Queuing a push, and draining the queue.

`enqueue` writes a row and returns. `send_queued` claims rows, re-checks
permission, and hands each to `app.push.send.send_to_person`, which is still
the only thing that talks to the transport.

The claiming is lifted deliberately from `app.mail.outbox._claim`, down to the
conditional UPDATE and the read-back by token, because two queues that claim
work differently are two sets of race conditions to reason about. It behaves
the same on SQLite and Postgres, unlike `FOR UPDATE SKIP LOCKED`.
"""

from __future__ import annotations

import secrets

from flask import current_app

from app.categories import CATEGORY_BY_CODE, category_label
from app.extensions import db
from app.models import Person, PushQueueItem
from app.models.base import utcnow
from app.models.push_queue import PUSH_QUEUED


class NotQueued(Exception):
    """Raised when a push cannot be queued, with the reason."""


def reachable(person) -> bool:
    """Whether this person has anywhere for a notification to go.

    Checked before queuing so the queue holds work rather than rows that
    exist to be thrown away. It is a database read, never a network call,
    which is the whole point of this module.

    With a provider that addresses people by id there is nothing local to
    inspect beyond whether they have ever signed in, so that is the question
    asked: somebody who has never had an account has not installed anything.
    """
    if person is None:
        return False

    # Resolved through `app.push.send` rather than importing
    # `build_push_transport` from the transport module directly, so there is
    # one seam rather than two. With two, this can decide somebody is
    # unreachable while the sender would have reached them, and the
    # notification is dropped at queue time for a reason nothing records.
    from app.push import send as push_send

    transport = push_send.build_push_transport(current_app.config)
    if getattr(transport, "addresses_people", False):
        user = push_send._login_for(person)
        return bool(user is not None and user.push_external_id)

    from app.models import PushSubscription

    return bool(PushSubscription.for_person(person.church_id, person.id))


def enqueue(
    *,
    person,
    category: str,
    title: str,
    body: str = "",
    url: str = "/",
    tag: str | None = None,
    dedupe_key: str | None = None,
) -> PushQueueItem | None:
    """Put one notification in the queue.

    Returns the row, or None when there is nothing to queue: no person, no
    device, opted out, or an identical notification already waiting.

    None rather than an exception for the same reason the outbox does it. A
    person with no phone is the normal case, not an error anybody can act on.
    """
    if category not in CATEGORY_BY_CODE:
        raise NotQueued(f"{category!r} is not a notification category.")
    if person is None:
        return None
    if not (title or "").strip():
        raise NotQueued("A notification needs a title.")

    # Checked again at send time, which is the check that counts. Here it
    # keeps the queue from filling with work that can never go out.
    if not person.allows(category):
        return None

    if not reachable(person):
        return None

    if dedupe_key:
        existing = db.session.scalar(
            db.select(PushQueueItem).where(
                PushQueueItem.church_id == person.church_id,
                PushQueueItem.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return None

    item = PushQueueItem(
        church_id=person.church_id,
        person_id=person.id,
        category=category,
        title=title.strip()[:120],
        body=(body or "")[:300],
        url=url or "/",
        tag=tag,
        dedupe_key=dedupe_key,
        status=PUSH_QUEUED,
        queued_at=utcnow(),
    )
    db.session.add(item)
    return item


def _claim(limit: int, church_id: int | None = None) -> tuple[str, list]:
    """Take up to `limit` waiting rows for this worker, atomically.

    See `app.mail.outbox._claim`. Same shape on purpose.
    """
    token = secrets.token_hex(16)

    selectable = db.select(PushQueueItem.id).where(
        PushQueueItem.status == PUSH_QUEUED,
        PushQueueItem.claim_token.is_(None),
    )
    if church_id is not None:
        selectable = selectable.where(PushQueueItem.church_id == church_id)

    ids = list(db.session.scalars(
        selectable.order_by(PushQueueItem.queued_at, PushQueueItem.id).limit(limit)
    ))
    if not ids:
        return token, []

    db.session.execute(
        db.update(PushQueueItem)
        .where(
            PushQueueItem.id.in_(ids),
            PushQueueItem.status == PUSH_QUEUED,
            PushQueueItem.claim_token.is_(None),
        )
        .values(claim_token=token, claimed_at=utcnow())
    )
    db.session.commit()

    claimed = db.session.scalars(
        db.select(PushQueueItem)
        .where(PushQueueItem.claim_token == token)
        .order_by(PushQueueItem.queued_at, PushQueueItem.id)
    ).all()
    return token, claimed


def send_queued(limit: int = 100, church_id: int | None = None,
                transport=None) -> dict:
    """Send what is waiting. Returns a count per outcome.

    `expired` is a success. A notification about a message from forty minutes
    ago is not news, and delivering it late is how somebody decides to turn
    notifications off for good.
    """
    from app.push.send import send_to_person
    from app.push.transport import PushMessage, build_push_transport

    transport = transport or build_push_transport(current_app.config)
    counts = {"sent": 0, "suppressed": 0, "failed": 0, "retrying": 0, "expired": 0}

    _, claimed = _claim(limit, church_id)

    for item in claimed:
        if item.is_stale():
            item.mark_expired()
            counts["expired"] += 1
            db.session.commit()
            continue

        person = Person.get_for_church(item.church_id, item.person_id)
        if person is None:
            # The row survives a person only until the worker reaches it.
            item.mark_suppressed("The person this was for is no longer on the roster.")
            counts["suppressed"] += 1
            db.session.commit()
            continue

        # The authoritative check. Somebody can turn a category off in the
        # seconds between a message being queued and being sent, and the
        # answer that counts is the one at this moment.
        if not person.allows(item.category):
            item.mark_suppressed(
                f"Opted out of {category_label(item.category)} before sending."
            )
            counts["suppressed"] += 1
            db.session.commit()
            continue

        try:
            result = send_to_person(
                person,
                PushMessage(title=item.title, body=item.body, url=item.url,
                            tag=item.tag),
                item.category,
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            db.session.rollback()
            # Re-read: the rollback detached whatever the failure left behind.
            item = db.session.get(PushQueueItem, item.id)
            if item is not None:
                item.mark_failed(str(exc))
                counts["retrying" if item.status == PUSH_QUEUED else "failed"] += 1
                db.session.commit()
            continue

        item.mark_sent(result.get("sent", 0))
        counts["sent"] += 1
        db.session.commit()

    return counts


def purge(older_than_days: int = 3) -> int:
    """Delete finished rows. Returns how many went.

    Kept for a few days rather than forever. The outbox is a record a church
    may need in March; this is a work queue, and a notification that went out
    on Sunday is on the person's phone, not in a table.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=older_than_days)
    result = db.session.execute(
        db.delete(PushQueueItem).where(
            PushQueueItem.status != PUSH_QUEUED,
            PushQueueItem.created_at < cutoff,
        )
    )
    db.session.commit()
    return result.rowcount or 0


def release_claims(minutes: int = 15) -> int:
    """Return rows a worker claimed and then died holding.

    Without this a crash between claiming and sending leaves a row stamped
    with a token and nothing to act on it, and it waits forever.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(minutes=minutes)
    result = db.session.execute(
        db.update(PushQueueItem)
        .where(
            PushQueueItem.status == PUSH_QUEUED,
            PushQueueItem.claim_token.is_not(None),
            PushQueueItem.claimed_at < cutoff,
        )
        .values(claim_token=None, claimed_at=None)
    )
    db.session.commit()
    return result.rowcount or 0
