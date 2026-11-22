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
from app.models.base import utcnow
from app.models.push import MAX_FAILURES
from app.push import send as push_send
from app.push.transport import build_push_transport

PROVIDERS = ("onesignal",)

# Runs a provider and Web Push side by side, so both halves of the summary
# below are true at once and the screen has to show both.
COMBINED = "both"


def transport_summary(config=None) -> dict:
    config = config or current_app.config
    name = (config.get("PUSH_TRANSPORT") or "null").lower()

    if name == COMBINED:
        # Four credentials, all required. Reported as one `real` so the state
        # machine below stays a single question, plus the four flags so the
        # screen can say which one is missing.
        app_id = bool(config.get("ONESIGNAL_APP_ID"))
        api_key = bool(config.get("ONESIGNAL_API_KEY"))
        vapid_public = bool(config.get("VAPID_PUBLIC_KEY"))
        vapid_private = bool(config.get("VAPID_PRIVATE_KEY"))
        return {
            "transport": name,
            "real": app_id and api_key and vapid_private,
            # True because a provider is one of the parts: the device rows are
            # no longer the whole picture, which is what this flag governs.
            "provider": True,
            "combined": True,
            "public_key_present": app_id and vapid_public,
            "private_key_present": api_key and vapid_private,
            "provider_keys_present": app_id and api_key,
            "webpush_keys_present": vapid_private,
        }

    if name in PROVIDERS:
        # Whether they are set, never what they are. The App ID is public by
        # construction; the key is the credential and never leaves the server.
        return {
            "transport": name,
            "real": bool(config.get("ONESIGNAL_APP_ID")
                         and config.get("ONESIGNAL_API_KEY")),
            "provider": True,
            "combined": False,
            "public_key_present": bool(config.get("ONESIGNAL_APP_ID")),
            "private_key_present": bool(config.get("ONESIGNAL_API_KEY")),
        }

    return {
        "transport": name,
        "real": name == "webpush",
        "provider": False,
        "combined": False,
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


def devices_for(church_id: int, user) -> int:
    """How many of this person's devices have notifications switched on.

    **Why this is not just a row count.** It used to be, and under a provider
    transport that is always zero. The provider holds the devices; nothing in
    this database has a row for the phone in somebody's hand. So the one
    screen a member opens to check whether notifications are on told every
    member of a church running a provider that they were off, while their
    phone was buzzing.

    What is knowable differs by transport, so this answers the question the
    screen asks rather than the one the schema makes easy:

      - Web Push keeps a row per device, so the rows are the count.
      - A provider does not say how many devices it holds. The last thing an
        app on this account reported is all there is, and it answers "are
        they on" rather than "on how many", so it contributes one.

    A best guess by design. The screen says "on this device", and only the
    device can answer that, so the page corrects this client-side as soon as
    it loads. This is what somebody sees in the meantime, and being roughly
    right beats being confidently wrong.
    """
    config = current_app.config
    total = 0

    for part in push_send.parts_of(build_push_transport(config)):
        if getattr(part, "addresses_people", False):
            if getattr(user, "push_permission", None) == User.PUSH_GRANTED:
                total += 1
        else:
            total += PushSubscription.device_count(church_id, user.id)

    return total


def _webpush_counts(church_id: int) -> dict:
    """The Web Push half: a row per device, so the rows are the measure.

    One function rather than a query inline in each branch that needs it. Two
    copies drifted apart would mean the combined transport and Web Push alone
    reporting different numbers for the same church.
    """
    return {
        "devices": db.session.scalar(
            db.select(func.count(PushSubscription.id)).where(
                PushSubscription.church_id == church_id
            )
        ) or 0,
        "people": db.session.scalar(
            db.select(func.count(func.distinct(PushSubscription.person_id))).where(
                PushSubscription.church_id == church_id,
                PushSubscription.person_id.is_not(None),
            )
        ) or 0,
        "failing": db.session.scalar(
            db.select(func.count(PushSubscription.id)).where(
                PushSubscription.church_id == church_id,
                PushSubscription.failure_count >= MAX_FAILURES,
            )
        ) or 0,
        "last_success": db.session.scalar(
            db.select(func.max(PushSubscription.last_success_at)).where(
                PushSubscription.church_id == church_id
            )
        ),
    }


def _combined_people(church_id: int) -> int:
    """How many distinct people are reachable by either pipe.

    Adding the two counts would double every person who has the app on their
    phone and the site open on a laptop, and a "people" figure above the
    account count reads as a bug rather than as a person with two devices.

    Accounts with no roster record are still people who can receive, so they
    are keyed by account rather than dropped: a church whose staff logins are
    not on the roster would otherwise report fewer people than phones.
    """
    reachable_keys = set()

    granted = db.session.execute(
        db.select(User.id, User.person_id).where(
            User.church_id == church_id,
            User.is_active_account.is_(True),
            User.is_kiosk.is_(False),
            User.push_permission == User.PUSH_GRANTED,
        )
    ).all()
    for user_id, person_id in granted:
        reachable_keys.add(person_id if person_id is not None else ("user", user_id))

    subscribed = db.session.scalars(
        db.select(func.distinct(PushSubscription.person_id)).where(
            PushSubscription.church_id == church_id,
            PushSubscription.person_id.is_not(None),
        )
    ).all()
    reachable_keys.update(subscribed)

    return len(reachable_keys)


def push_health(church_id: int) -> dict:
    summary = transport_summary()
    counts = reach(church_id)

    if summary.get("combined"):
        # Both pipes, so both measures count and neither alone is the answer.
        # A phone on the native app has a granted permission and no
        # subscription row; a wrapper around the live site has a row and no
        # provider identity. Reporting either number on its own halves a
        # working church.
        rows = _webpush_counts(church_id)
        devices = counts["granted"] + rows["devices"]
        # Deduplicated, because one person owning an iPhone on the app and a
        # laptop in a browser is one person, and a "people" figure larger than
        # the account count would read as a bug on the screen.
        people = _combined_people(church_id)
        failing = rows["failing"]
        last_success = rows["last_success"]
    elif summary.get("provider"):
        # No rows to count and none to fail. A provider that has dropped a
        # device answers at send time, which is what the check page and the
        # send test are for.
        devices = counts["granted"]
        people = counts["granted"]
        failing = 0
        last_success = None
    else:
        rows = _webpush_counts(church_id)
        devices = rows["devices"]
        people = rows["people"]
        failing = rows["failing"]
        last_success = rows["last_success"]

    # Which of the two problems this is, if either. Ordered by what has to be
    # fixed first: keys before devices, because enabling a device against a
    # dead transport teaches somebody that notifications do not work.
    if not summary["real"]:
        state = (
            "off"
            if summary["transport"] not in PROVIDERS
            and not summary.get("combined")
            else "no_keys"
        )
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
        **queue_health(church_id),
        **counts,
        **summary,
    }


# A queue this deep for this long means nobody is draining it. Two minutes,
# because the worker's pass is two seconds: anything waiting sixty times
# longer than that is not waiting, it is stuck.
STUCK_MINUTES = 2


def queue_health(church_id: int) -> dict:
    """Is anything actually sending the notifications.

    The whole reason this row exists: when push moved out of the web request
    and into a worker, "notifications stopped arriving" became a thing that
    can be true while the app itself is perfectly healthy. A worker that died
    at two in the morning looks exactly like a quiet Tuesday unless somebody
    is counting what is waiting.

    Depth alone does not answer it. Ten waiting is healthy two seconds after
    somebody posts and a dead worker ten minutes later. The age of the oldest
    one is the number that tells you which.
    """
    from app.models import PushQueueItem

    waiting = PushQueueItem.queued_count(church_id)
    oldest = PushQueueItem.oldest_queued_at(church_id)

    stuck = False
    waiting_minutes = 0.0
    if oldest is not None:
        waiting_minutes = (utcnow() - oldest).total_seconds() / 60
        stuck = waiting_minutes > STUCK_MINUTES

    return {
        "queue_waiting": waiting,
        "queue_oldest": oldest,
        "queue_minutes": round(waiting_minutes, 1),
        "queue_stuck": stuck,
    }
