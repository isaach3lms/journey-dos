"""Are app notifications actually going out?

The companion to `app/mail/health.py`, and it exists because of a failure that
nothing on any screen would have shown: the push machinery was complete and
wired to nothing, so a member could turn notifications on, see them listed as
on, and never receive one. Nobody could have noticed from inside the product.

Two different things have to be true for a notification to arrive, and this
reports them separately because the fixes are different:

**The transport is real.** Either Web Push with VAPID keys, or a provider
with its own credentials. Off is the default and is a deliberate choice, not
a fault: a church without keys should run without push rather than refuse to
boot. But "off" and "broken" look identical from a member's phone, so the
screen says which.

**Somebody has turned it on.** Push needs a device to have granted
permission. Zero is the normal state of a church that has not asked anybody
yet, and it is the first thing to check when "notifications do not work"
turns out to mean "nobody has enabled them".

**Where the count comes from depends on the transport, and that is the whole
reason this module had to change.** Web Push stores a row per device here, so
counting rows answers it. A provider holds the devices instead, so there is
nothing here to count and the only measure is what each phone reported about
its own permission. Counting rows under a provider returns zero forever,
which read as "notifications are off" on a church whose notifications were
working.
"""

from __future__ import annotations

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import PushSubscription, User
from app.models.push import MAX_FAILURES

PROVIDERS = ("onesignal",)


def transport_summary(config=None) -> dict:
    config = config or current_app.config
    name = (config.get("PUSH_TRANSPORT") or "null").lower()

    if name in PROVIDERS:
        # Whether they are set, never what they are. The App ID is public by
        # construction; the key is the credential and never leaves the server.
        return {
            "transport": name,
            "real": bool(config.get("ONESIGNAL_APP_ID")
                         and config.get("ONESIGNAL_API_KEY")),
            "provider": True,
            "public_key_present": bool(config.get("ONESIGNAL_APP_ID")),
            "private_key_present": bool(config.get("ONESIGNAL_API_KEY")),
        }

    return {
        "transport": name,
        "real": name == "webpush",
        "provider": False,
        "public_key_present": bool(config.get("VAPID_PUBLIC_KEY")),
        "private_key_present": bool(config.get("VAPID_PRIVATE_KEY")),
    }


def reach(church_id: int) -> dict:
    """How many accounts could receive a notification right now.

    Only accounts, deliberately. A roster is mostly people who have never
    signed in, and reporting "12 of 400" on a church with 48 app users makes
    a working setup look broken.
    """
    def count(*where):
        return db.session.scalar(
            db.select(func.count(User.id)).where(
                User.church_id == church_id,
                User.is_active_account.is_(True),
                User.is_kiosk.is_(False),
                *where,
            )
        ) or 0

    accounts = count()
    granted = count(User.push_permission == User.PUSH_GRANTED)
    denied = count(User.push_permission == User.PUSH_DENIED)

    return {
        "accounts": accounts,
        "granted": granted,
        "denied": denied,
        # Never asked, or never opened the app. The same thing from here and
        # the same fix: get them into the app once.
        "unasked": accounts - granted - denied,
    }


def push_health(church_id: int) -> dict:
    summary = transport_summary()
    counts = reach(church_id)

    if summary.get("provider"):
        # No rows to count and none to fail. A provider that has dropped a
        # device answers at send time, which is what the check page and the
        # send test are for.
        devices = counts["granted"]
        people = counts["granted"]
        failing = 0
        last_success = None
    else:
        devices = db.session.scalar(
            db.select(func.count(PushSubscription.id)).where(
                PushSubscription.church_id == church_id
            )
        ) or 0

        people = db.session.scalar(
            db.select(func.count(func.distinct(PushSubscription.person_id))).where(
                PushSubscription.church_id == church_id,
                PushSubscription.person_id.is_not(None),
            )
        ) or 0

        failing = db.session.scalar(
            db.select(func.count(PushSubscription.id)).where(
                PushSubscription.church_id == church_id,
                PushSubscription.failure_count >= MAX_FAILURES,
            )
        ) or 0

        last_success = db.session.scalar(
            db.select(func.max(PushSubscription.last_success_at)).where(
                PushSubscription.church_id == church_id
            )
        )

    # Which of the two problems this is, if either. Ordered by what has to be
    # fixed first: keys before devices, because enabling a device against a
    # dead transport teaches somebody that notifications do not work.
    if not summary["real"]:
        state = "off" if summary["transport"] not in PROVIDERS else "no_keys"
    elif not summary["private_key_present"]:
        state = "no_keys"
    elif devices == 0:
        state = "nobody_on"
    elif failing and failing == devices:
        state = "all_failing"
    else:
        state = "on"

    return {
        "state": state,
        "devices": devices,
        "people": people,
        "failing": failing,
        "last_success": last_success,
        **counts,
        **summary,
    }
