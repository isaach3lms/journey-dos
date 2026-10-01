"""Are app notifications actually going out?

The companion to `app/mail/health.py`, and it exists because of a failure that
nothing on any screen would have shown: the push machinery was complete and
wired to nothing, so a member could turn notifications on, see them listed as
on, and never receive one. Nobody could have noticed from inside the product.

Two different things have to be true for a notification to arrive, and this
reports them separately because the fixes are different:

**The transport is real.** `PUSH_TRANSPORT` has to be `webpush` and the VAPID
keys have to be set. Off is the default and is a deliberate choice, not a
fault: a church without keys should run without push rather than refuse to
boot. But "off" and "broken" look identical from a member's phone, so the
screen says which.

**Somebody has turned it on.** Push needs a device to have granted
permission. Zero devices is the normal state of a church that has not asked
anybody yet, and it is the first thing to check when "notifications do not
work" turns out to mean "nobody has enabled them".
"""

from __future__ import annotations

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import PushSubscription
from app.models.push import MAX_FAILURES


def transport_summary(config=None) -> dict:
    config = config or current_app.config
    name = (config.get("PUSH_TRANSPORT") or "null").lower()
    return {
        "transport": name,
        "real": name == "webpush",
        # Whether they are set, never what they are.
        "public_key_present": bool(config.get("VAPID_PUBLIC_KEY")),
        "private_key_present": bool(config.get("VAPID_PRIVATE_KEY")),
    }


def push_health(church_id: int) -> dict:
    summary = transport_summary()

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
        state = "off"
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
        **summary,
    }
