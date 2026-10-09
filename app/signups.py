"""Recording a next step somebody asked for, and telling staff.

Thin on purpose. The offers are in app/next_steps.py, the row is in
app/models/signup.py, and the staff alert is `app.alerts.tell_staff`, which
already knows how to reach a church on both channels and already excludes the
lobby iPad. What is left here is the small amount of judgement that belongs to
this feature rather than to any of those.

**Two taps is one ask.** Somebody who taps Baptism, reads the form, goes back
and taps it again has asked once. A second row would put them on the staff
list twice and turn the count from "people waiting to hear back" into "times
a tile was tapped".

**Nothing here can fail the submission.** A member tapping a tile and getting
an error page learns that the app is broken, when in fact their ask was
recorded and only the notification failed. The alert is wrapped for the same
reason the connect card's is.
"""

from __future__ import annotations

from flask import current_app, url_for

from app.extensions import db
from app.models import NextStepSignup
from app.next_steps import get as offer_for

CATEGORY = "signup"


class NotSigned(Exception):
    """Raised when a sign-up cannot be recorded, with the reason."""


UNKNOWN_OFFER = "That is not something this church offers."
NO_PERSON = "Your login is not linked to a record yet."


def submit(church_id: int, person, offer_code: str, note: str = ""):
    """Record the ask, or return the open one that already exists.

    Returns `(signup, is_new)`. Caller commits.

    `is_new` is False when they had already asked and nobody has dealt with
    it yet. The caller uses it to say "you have already asked and we have it"
    rather than thanking them a second time for something they did last week,
    which reads as nobody having looked.
    """
    offer = offer_for(offer_code)
    if offer is None:
        raise NotSigned(UNKNOWN_OFFER)
    if person is None:
        raise NotSigned(NO_PERSON)

    existing = NextStepSignup.already_asked(church_id, person.id, offer.code)
    if existing is not None:
        # The note is still worth keeping if they wrote one this time and
        # had not before: they came back to add something, and dropping it
        # loses the only new information in the second visit.
        if note and not existing.note:
            existing.note = note
        return existing, False

    signup = NextStepSignup(
        church_id=church_id,
        person_id=person.id,
        offer=offer.code,
        note=note or None,
    )
    db.session.add(signup)
    db.session.flush()
    return signup, True


def alert_staff(church, signup, person) -> int:
    """Tell the church somebody put their hand up. Returns how many heard.

    Never raises. See the module docstring.
    """
    from app.alerts import tell_staff
    from app.content import PEOPLE
    from app.next_steps import label_for

    offer = offer_for(signup.offer)
    did = offer.staff_line if offer else f"asked about {label_for(signup.offer)}"

    try:
        link = url_for("people.detail", person_id=person.id, _external=True,
                       _scheme="https")
        path = url_for("people.signups")
    except Exception:  # noqa: BLE001 - outside a request, e.g. a shell
        link, path = "", "/"

    try:
        return tell_staff(
            church,
            category=CATEGORY,
            subject=PEOPLE["signup_alert_subject"].format(
                name=person.full_name, offer=label_for(signup.offer)),
            body_text=PEOPLE["signup_alert_body"].format(
                name=person.full_name,
                did=did,
                # Whether, not what. See the copy for why.
                note=PEOPLE["signup_alert_note"] if signup.note
                else PEOPLE["signup_alert_no_note"],
                link=link,
                church=church.name,
            ),
            push_title=PEOPLE["signup_push_title"].format(
                offer=label_for(signup.offer)),
            push_body=PEOPLE["signup_push_body"].format(name=person.full_name),
            path=path,
            # One per offer, so three people asking about baptism on a Sunday
            # replace each other on the lock screen rather than stacking.
            # The list behind the tap has all three.
            tag=f"signup:{signup.offer}",
            key=f"signup:{signup.id}",
        ).reached
    except Exception:  # noqa: BLE001
        current_app.logger.exception(
            "Sign-up alert failed for signup %s", getattr(signup, "id", None)
        )
        return 0
