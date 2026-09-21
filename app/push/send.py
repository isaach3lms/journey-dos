"""Delivering a push to a person.

The opt-out check is the same one email uses. A person who turned off "Next
steps" turned off next steps, not email specifically, and a system that honours
that in one channel and ignores it in the other has not honoured it.

Transactional categories still send, for exactly the reason they do in email:
a pickup code that never arrives because somebody left the newsletter is worse
than no notification at all.
"""

from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import PushSubscription
from app.push.transport import (
    PushFailed,
    PushMessage,
    SubscriptionGone,
    build_push_transport,
)


def send_to_person(person, message: PushMessage, category: str,
                   transport=None) -> dict:
    """Push to every device this person has registered. Caller commits.

    Returns counts. A person with no devices is not an error: most people will
    never turn notifications on, and the email is still going.
    """
    counts = {"sent": 0, "failed": 0, "gone": 0, "suppressed": 0}

    if person is None:
        return counts

    # The same check email makes, at the same moment: immediately before
    # sending, not when the notification was decided on.
    if not person.allows(category):
        counts["suppressed"] += 1
        return counts

    transport = transport or build_push_transport(current_app.config)
    subscriptions = PushSubscription.for_person(person.church_id, person.id)

    for subscription in subscriptions:
        try:
            transport.send(subscription, message)
        except SubscriptionGone:
            # Revoked forever. Deleting beats retrying a browser that no
            # longer exists.
            db.session.delete(subscription)
            counts["gone"] += 1
            continue
        except PushFailed as exc:
            subscription.record_failure(str(exc))
            counts["failed"] += 1
            continue

        subscription.record_success()
        counts["sent"] += 1

    return counts


def notify(person, title: str, body: str, category: str,
           url: str = "/", tag: str | None = None, transport=None) -> dict:
    """Convenience wrapper. Caller commits."""
    return send_to_person(
        person,
        PushMessage(title=title, body=body, url=url, tag=tag),
        category,
        transport=transport,
    )
