"""Queuing and sending.

`queue` writes a row and returns. `send_pending` claims rows, checks
permission, and hands each to the transport. Nothing else in the codebase
touches the transport directly.
"""

from __future__ import annotations

import secrets

from sqlalchemy import event
from sqlalchemy.orm import Session

from flask import current_app, g, has_request_context

from app.categories import CATEGORY_BY_CODE, category_label, is_transactional
from app.extensions import db
from app.mail.transport import SendFailed, build_transport
from app.models import (
    KIND_EMAIL,
    STATUS_QUEUED,
    OutboxMessage,
    Person,
    PersonEvent,
)
from app.models.base import utcnow


class NotQueued(Exception):
    """Raised when a message cannot even be queued, with the reason."""


# Sent the moment the request commits, not on the next worker run. Only mail
# a person is standing there waiting for: confirm your address, reset your
# password, set up your account.
SEND_NOW_CATEGORIES = frozenset({"account"})


def queue(
    *,
    church_id: int,
    category: str,
    subject: str,
    body_text: str,
    to_email: str | None = None,
    to_name: str | None = None,
    person: Person | None = None,
    body_html: str | None = None,
    dedupe_key: str | None = None,
    actor=None,
) -> OutboxMessage | None:
    """Put one message in the outbox.

    Returns the row, or None when an identical message is already queued.
    Returning None rather than raising is deliberate: a double-clicked button
    and a retried request are normal, and neither is an error worth showing
    anyone.
    """
    if category not in CATEGORY_BY_CODE:
        raise NotQueued(f"{category!r} is not a notification category.")

    address = (to_email or (person.email if person else None) or "").strip().lower()
    if not address:
        raise NotQueued("No email address to send to.")

    if not subject.strip() or not body_text.strip():
        raise NotQueued("A message needs a subject and a body.")

    # Suppression is enforced again at send time, which is the check that
    # counts. Checking here too avoids queuing something that can never go.
    if person is not None and not person.allows(category):
        raise NotQueued(
            f"{person.first_name} has opted out of {category_label(category)}."
        )

    if dedupe_key:
        existing = db.session.scalar(
            db.select(OutboxMessage).where(
                OutboxMessage.church_id == church_id,
                OutboxMessage.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return None

    message = OutboxMessage(
        church_id=church_id,
        person_id=person.id if person is not None else None,
        to_email=address,
        to_name=to_name or (person.full_name if person is not None else None),
        category=category,
        subject=subject.strip()[:255],
        body_text=body_text,
        body_html=body_html,
        dedupe_key=dedupe_key,
        status=STATUS_QUEUED,
        queued_at=utcnow(),
        created_by_user_id=getattr(actor, "id", None),
    )
    db.session.add(message)
    if category in SEND_NOW_CATEGORIES and has_request_context():
        g.setdefault("_outbox_send_now", []).append(message)
    return message


@event.listens_for(Session, "after_commit")
def _mark_ready_to_send(session) -> None:
    """Only a committed message is sent now. A request that rolled back its
    sign-up must not email a link to an account that does not exist."""
    if has_request_context() and "_outbox_send_now" in g:
        g.setdefault("_outbox_ready", []).extend(g.pop("_outbox_send_now"))


def deliver_queued_now() -> dict | None:
    """Send this request's account emails now instead of waiting for cron.

    Runs after every request (see create_app). A confirmation link that arrives five minutes
    later, or never because the worker is down, is a person who gives up on
    the sign-up form. Everything stays in the outbox first, so if this send
    fails for any reason the row is still queued and the worker retries it.
    Nothing here can fail the request.
    """
    if not has_request_context():
        return None
    pending = g.pop("_outbox_ready", None) or []
    if not pending or not current_app.config.get("MAIL_SEND_NOW", True):
        return None
    ids = []
    for message in pending:
        try:
            if message.id is not None:
                ids.append(message.id)
        except Exception:  # detached or rolled back: the worker has it
            continue
    if not ids:
        return None
    try:
        return send_pending(limit=len(ids), message_ids=ids)
    except Exception:
        current_app.logger.exception("Immediate send failed; left for the worker")
        db.session.rollback()
        return None


def _claim(limit: int, church_id: int | None = None,
           message_ids: list[int] | None = None) -> tuple[str, list]:
    """Take up to `limit` queued rows for this worker, atomically.

    A conditional UPDATE that stamps a token, then a read of only what carries
    that token. Two workers running at once cannot claim the same row: the
    second UPDATE simply matches nothing. This behaves the same on SQLite and
    Postgres, unlike `SELECT ... FOR UPDATE SKIP LOCKED`.
    """
    token = secrets.token_hex(16)

    selectable = db.select(OutboxMessage.id).where(
        OutboxMessage.status == STATUS_QUEUED,
        OutboxMessage.claim_token.is_(None),
    )
    if church_id is not None:
        selectable = selectable.where(OutboxMessage.church_id == church_id)
    if message_ids is not None:
        if not message_ids:
            return token, []
        selectable = selectable.where(OutboxMessage.id.in_(message_ids))
    ids = [
        row for row in db.session.scalars(
            selectable.order_by(OutboxMessage.queued_at, OutboxMessage.id).limit(limit)
        )
    ]
    if not ids:
        return token, []

    db.session.execute(
        db.update(OutboxMessage)
        .where(
            OutboxMessage.id.in_(ids),
            OutboxMessage.status == STATUS_QUEUED,
            OutboxMessage.claim_token.is_(None),
        )
        .values(claim_token=token, claimed_at=utcnow())
    )
    db.session.commit()

    claimed = db.session.scalars(
        db.select(OutboxMessage)
        .where(OutboxMessage.claim_token == token)
        .order_by(OutboxMessage.queued_at, OutboxMessage.id)
    ).all()
    return token, claimed


def send_pending(limit: int = 50, church_id: int | None = None, transport=None,
                 message_ids: list[int] | None = None) -> dict:
    """Send what is queued. Returns a count per outcome.

    `message_ids` narrows the run to specific rows, which is how a request
    sends its own account email immediately. The claim works the same way, so
    the cron worker and a request can never both send one message.
    """
    transport = transport or build_transport(current_app.config)
    from_address = current_app.config.get(
        "MAIL_FROM", "The Journey Church <no-reply@example.com>"
    )

    counts = {"sent": 0, "suppressed": 0, "failed": 0, "retrying": 0}
    _, claimed = _claim(limit, church_id, message_ids)

    for message in claimed:
        person = (
            Person.get_for_church(message.church_id, message.person_id)
            if message.person_id
            else None
        )

        # The authoritative check. Someone can unsubscribe between a message
        # being queued and being sent, and the answer that matters is the one
        # at this moment.
        if person is not None and not person.allows(message.category):
            message.mark_suppressed(
                f"Opted out of {category_label(message.category)} before sending."
            )
            counts["suppressed"] += 1
            db.session.commit()
            continue

        try:
            provider_id = transport.send(
                to_email=message.to_email,
                to_name=message.to_name,
                subject=message.subject,
                body_text=message.body_text,
                body_html=message.body_html,
                from_address=from_address,
            )
        except SendFailed as exc:
            if exc.permanent:
                # Retrying a rejected address forever damages the sending
                # reputation that every other church on this platform shares.
                message.attempts += 1
                message.status = "failed"
                message.last_error = str(exc)[:2000]
                message.claim_token = None
                counts["failed"] += 1
            else:
                message.mark_failed(str(exc))
                counts["retrying" if message.status == STATUS_QUEUED else "failed"] += 1
            db.session.commit()
            continue

        message.mark_sent(provider_id)
        counts["sent"] += 1

        if person is not None:
            PersonEvent.record(
                person,
                KIND_EMAIL,
                f"Email sent: {message.subject[:180]}",
                detail=f"Category: {category_label(message.category)}",
                occurred_at=message.sent_at,
            )
        db.session.commit()

    return counts


def opt_out(person: Person, reason: str = "one-click unsubscribe") -> None:
    """Global opt-out. Transactional mail is unaffected, by design."""
    if person.email_opt_out_at is None:
        person.email_opt_out_at = utcnow()
        PersonEvent.record(
            person, KIND_EMAIL, "Unsubscribed from email", detail=reason
        )


def opt_in(person: Person) -> None:
    if person.email_opt_out_at is not None:
        person.email_opt_out_at = None
        PersonEvent.record(person, KIND_EMAIL, "Resubscribed to email")


__all__ = [
    "queue",
    "send_pending",
    "opt_out",
    "opt_in",
    "NotQueued",
    "is_transactional",
]
