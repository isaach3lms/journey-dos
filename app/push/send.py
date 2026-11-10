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

    if transport.addresses_people:
        return _send_through_provider(person, message, transport, counts)

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


def _send_through_provider(person, message: PushMessage, transport,
                           counts: dict) -> dict:
    """One call for the person, with the provider holding their devices.

    There is no row to mark failed and none to delete, which is the point: the
    system that knows a phone has been wiped is the one the phone talks to.
    What this still owns is the opt-out, checked above, and knowing who the
    person is to the provider, which is the login rather than the roster
    record: somebody with no account has not installed anything.
    """
    user = _login_for(person)
    if user is None or not user.push_external_id:
        # Not an error. Most people on a roster have never signed in, and the
        # email is still going.
        return counts

    try:
        transport.send_to_person(user.push_external_id, message)
    except SubscriptionGone:
        # The provider has nobody to deliver to: the app is installed and
        # notifications were never allowed, or were turned off in iOS
        # Settings. Nothing here can fix that and retrying never will.
        counts["gone"] += 1
        return counts
    except PushFailed:
        counts["failed"] += 1
        return counts

    counts["sent"] += 1
    return counts


def _login_for(person):
    """The account behind a roster record, or None."""
    from app.models import User

    return db.session.scalar(
        db.select(User).where(
            User.church_id == person.church_id,
            User.person_id == person.id,
            User.is_active_account.is_(True),
        )
    )


def notify(person, title: str, body: str, category: str,
           url: str = "/", tag: str | None = None, transport=None) -> dict:
    """Convenience wrapper. Caller commits."""
    return send_to_person(
        person,
        PushMessage(title=title, body=body, url=url, tag=tag),
        category,
        transport=transport,
    )
