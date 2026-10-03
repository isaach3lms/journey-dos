"""Sending a notification to yourself, to find out whether it arrives.

Every other row on the check page reads something and reports it. This one
actually sends, because the rows cannot answer the only question that matters.
A phone can hold the app, the notification system, a permission that says yes
and an id the server knows, and still receive nothing: a key rotated on the
provider's side, a device the provider has quietly dropped, a signed-in account
with no roster record behind it. Nothing visible from the phone distinguishes
those from working.

**It sends down the production path, not a shortcut to the provider.** The
temptation is to call the transport directly with the signed-in account's
external id, which is fewer moving parts and tests almost nothing: it skips the
roster lookup that real notifications depend on, so an account whose login is
not attached to a person would pass here and receive nothing in practice. That
is the exact shape of failure this page exists to catch, so the self-test goes
through `send_to_person` like every other notification.

**Transactional, because a diagnostic must not be silently suppressed.** The
category is the account one: somebody who turned off announcements has not
asked to be lied to by a test they just tapped.
"""

from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.push.send import send_to_person
from app.push.transport import PushMessage, build_push_transport

CATEGORY = "account"
TAG = "selftest"

# What happened, in states the page has a sentence for. Each one is a different
# thing to go and fix, which is the whole reason they are not one boolean.
SENT = "sent"
NOT_REGISTERED = "not_registered"
BLOCKED = "blocked"
FAILED = "failed"
UNCONFIGURED = "unconfigured"
UNLINKED = "unlinked"

STATES = (SENT, NOT_REGISTERED, BLOCKED, FAILED, UNCONFIGURED, UNLINKED)


def run(user, *, title: str, body: str, url: str = "/", transport=None) -> str:
    """Push to this account's own phone and name the outcome. Commits."""
    transport = transport or build_push_transport(current_app.config)

    # A transport that logs instead of sending reports a cheerful success to
    # somebody holding a phone that will never buzz. The question here is
    # whether a notification arrives, so a transport that cannot send one is
    # itself the answer rather than a detail to hide behind a green tick.
    if not getattr(transport, "addresses_people", False):
        return UNCONFIGURED

    person = getattr(user, "person", None)
    if person is None:
        # Not an edge case worth folding into "not registered". A login with
        # no roster record receives nothing in production for a reason no
        # amount of reinstalling the app will change.
        return UNLINKED

    counts = send_to_person(
        person,
        PushMessage(title=title, body=body, url=url, tag=TAG),
        CATEGORY,
        transport=transport,
    )
    db.session.commit()

    if counts["sent"]:
        return SENT
    if counts["gone"]:
        # The provider has the app and nobody to deliver to: permission was
        # never granted, or was switched off in iOS Settings afterwards.
        return BLOCKED
    if counts["failed"]:
        return FAILED
    if counts["suppressed"]:
        # Unreachable while the category above is transactional, and kept so
        # that making it optional one day breaks a test rather than quietly
        # reporting a suppressed test as an unregistered phone.
        return BLOCKED
    return NOT_REGISTERED
