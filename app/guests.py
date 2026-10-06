"""Turning a submitted connect card into somebody on the roster.

The form is public, with no sign-in, because the person filling it in has no
account and is standing in a lobby with a phone. Everything awkward about
this module follows from that one fact.

**It matches before it creates.** A church's roster fills with duplicates
faster than anything else in it, and a public form is the fastest way yet: a
family fills a card in September and again in January, somebody fills one on
their phone and again on the welcome desk iPad. A card whose email or phone
already belongs to somebody attaches to them instead of making a second
record of the same person.

**It never demotes anybody.** A member who fills in a card out of politeness
must not be moved back to visitor and started on a guest follow-up series
written for strangers. Matching an existing person records the visit and
leaves their stage alone.

**The guest is approved, not queued.** A card could instead create somebody
who is invisible to church-wide email until a human confirms them. That was
the first design and it is wrong: at the volume on the dashboard it is tens
of clicks a week whose only effect is announcement eligibility, staff would
stop doing it within a month, and the failure is silent, with guests quietly
receiving nothing. Defence against a public form belongs at the door, in the
honeypot and the rate limit the view applies, not in a queue nobody clears.
"""

from __future__ import annotations

from datetime import date

from app.automation import enroll_for_stage
from app.extensions import db
from app.models import (
    KIND_CREATED,
    KIND_NOTE,
    GuestCard,
    Person,
    PersonEvent,
)
from app.models.base import utcnow
from app.models.guest import HEARD_CHOICES
from app.phonecode import digits

# What these alerts are, for the opt-out machinery. Transactional: a
# church cannot unsubscribe from being told somebody walked in.
CATEGORY = "guest_card"

MAX_NOTE = 2000

# A card has to be answerable or it is not a connect card. Somebody who wants
# to stay anonymous is welcome to; they just do not need this form.
NEEDS_CONTACT = "needs_contact"
NEEDS_NAME = "needs_name"


class CardRefused(Exception):
    """Why this submission is not a card. Carries a content key."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def clean(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


def match(church_id: int, *, email: str, phone: str) -> Person | None:
    """Somebody already on the roster who this card is about.

    Email first, because it is typed deliberately and is unique in practice.
    Phone second, compared on digits so that (573) 555-0101 finds 5735550101.
    """
    if email:
        found = db.session.scalar(
            db.select(Person).where(
                Person.church_id == church_id,
                Person.is_archived.is_(False),
                db.func.lower(Person.email) == email,
            ).order_by(Person.id)
        )
        if found is not None:
            return found

    wanted = digits(phone)
    if len(wanted) >= 10:
        # `phone_last4` is indexed, so this is a short list to compare rather
        # than a scan of the church. The full comparison still happens below:
        # four digits is not an identity.
        candidates = db.session.scalars(
            db.select(Person).where(
                Person.church_id == church_id,
                Person.is_archived.is_(False),
                Person.phone_last4 == wanted[-4:],
            ).order_by(Person.id)
        ).all()
        for person in candidates:
            if digits(person.phone) == wanted:
                return person

    return None


def submit(church_id: int, *, first_name: str, last_name: str = "",
           email: str = "", phone: str = "", heard: str = "",
           note: str = "", wants_contact: bool = False,
           wants_account: bool = False) -> GuestCard:
    """Record a card and put its person on the roster. Caller commits."""
    first = clean(first_name, 80)
    last = clean(last_name, 80)
    email = clean(email, 255).lower()
    phone = clean(phone, 40)

    if not first:
        raise CardRefused(NEEDS_NAME)
    if not email and not phone:
        raise CardRefused(NEEDS_CONTACT)

    if heard not in HEARD_CHOICES:
        # An unknown value is a stale form or a probe. Dropping it keeps the
        # counts meaningful without refusing a card over a dropdown.
        heard = ""

    person = match(church_id, email=email, phone=phone)
    is_new = person is None

    if is_new:
        person = Person(
            church_id=church_id,
            first_name=first,
            last_name=last,
            email=email or None,
            phone=phone or None,
            stage="visitor",
            # The number on the dashboard. This is the column the first time
            # guests tile counts, so a card submitted today is a guest this
            # week without anything else having to run.
            first_seen_on=date.today(),
            approved_at=utcnow(),
        )
        db.session.add(person)
        db.session.flush()
        PersonEvent.record(person, KIND_CREATED, "Filled in a connect card")
        enroll_for_stage(person)
    else:
        # Fill the gaps, never overwrite. Staff may have corrected a spelling
        # or a number, and a guest retyping it from memory is not better
        # information than a person who looked it up.
        if not person.email and email:
            person.email = email
        if not person.phone and phone:
            person.phone = phone
        if person.first_seen_on is None:
            person.first_seen_on = date.today()
        PersonEvent.record(person, KIND_NOTE, "Filled in a connect card")

    card = GuestCard(
        church_id=church_id,
        person_id=person.id,
        first_name=first,
        last_name=last,
        email=email or None,
        phone=phone or None,
        heard=heard or None,
        note=clean(note, MAX_NOTE) or None,
        wants_contact=bool(wants_contact),
        wants_account=bool(wants_account),
        is_new_person=is_new,
    )
    db.session.add(card)
    db.session.flush()
    return card


# ---------------------------------------------------------------------------
# Telling people a card came in
#
# Two different alerts to two different audiences, and they are separate on
# purpose. A card is the church's to answer: somebody walked in and said
# hello, and a person there should reply. An account request is platform
# administration, which on every deployment so far has been whoever set the
# church up rather than anybody on the church staff.
#
# Neither alert carries what the guest wrote. Staff are told a card exists
# and given a link, for the same reason a pastoral request works that way: a
# copy of somebody's words sitting in four inboxes cannot be taken back.
# ---------------------------------------------------------------------------

def _staff_users(church_id: int):
    from app.models import User

    return db.session.scalars(
        db.select(User).where(
            User.church_id == church_id,
            User.role == "staff",
            User.is_active_account.is_(True),
            User.is_kiosk.is_(False),
        )
    ).all()


def alert_staff(church, card: GuestCard, *, link: str, path: str) -> int:
    """Tell the church a card came in. Returns how many were told.

    `link` is absolute, for the email. `path` is the same place as a path on
    this app, for the notification: a push payload is delivered by a third
    party and is not the place to teach a phone to trust a hostname.

    Caller commits. A failure to notify never fails a submission: the guest
    has already handed over their details and an error page would tell them
    their card did not go through when it did.
    """
    from app.content import PEOPLE
    from app.mail import NotQueued, queue
    from app.notify import notify

    subject = PEOPLE["guest_alert_subject"].format(name=card.full_name)
    body = PEOPLE["guest_alert_body"].format(
        name=card.full_name,
        church=church.name,
        what=PEOPLE["guest_alert_new"] if card.is_new_person
        else PEOPLE["guest_alert_known"],
        link=link,
    )

    told = 0
    named = church.guest_recipients

    if named:
        # Addresses rather than accounts: most of a care team is not a login
        # in this system. Email only, because there is no person to push to.
        for address in named:
            try:
                queue(
                    church_id=church.id,
                    category=CATEGORY,
                    subject=subject,
                    body_text=body,
                    to_email=address,
                    dedupe_key=f"guest:{card.id}:{address}",
                )
            except NotQueued:
                continue
            told += 1
        return told

    # Nobody named, so every active staff account. These are people, so they
    # get the notification on their phone as well as the email, which on a
    # Sunday morning is the half that actually reaches anybody.
    for user in _staff_users(church.id):
        if user.person is not None:
            notify(
                person=user.person,
                church_id=church.id,
                category=CATEGORY,
                subject=subject,
                body_text=body,
                push_title=PEOPLE["guest_push_title"],
                push_body=PEOPLE["guest_push_body"].format(name=card.full_name),
                url=path,
                tag="guest-card",
                dedupe_key=f"guest:{card.id}:{user.id}",
            )
            told += 1
            continue
        if not user.email:
            continue
        try:
            queue(
                church_id=church.id,
                category=CATEGORY,
                subject=subject,
                body_text=body,
                to_email=user.email,
                to_name=user.name,
                dedupe_key=f"guest:{card.id}:{user.id}",
            )
        except NotQueued:
            continue
        told += 1

    return told


def request_account(church, card: GuestCard, *, link: str) -> int:
    """Ask whoever administers this church's app to set somebody up.

    Deliberately carries the name and the address and nothing else. Creating
    a login needs exactly those two things, and the note a guest wrote is
    about the church rather than about an account.
    """
    from app.content import PEOPLE
    from app.mail import NotQueued, queue

    told = 0
    for address in church.account_request_recipients:
        try:
            queue(
                church_id=church.id,
                category=CATEGORY,
                subject=PEOPLE["account_request_subject"].format(
                    name=card.full_name, church=church.name),
                body_text=PEOPLE["account_request_body"].format(
                    name=card.full_name,
                    church=church.name,
                    email=card.email or PEOPLE["account_request_no_email"],
                    phone=card.phone or PEOPLE["account_request_no_phone"],
                    link=link,
                ),
                to_email=address,
                dedupe_key=f"account:{card.id}:{address}",
            )
        except NotQueued:
            continue
        told += 1
    return told
