"""A family nobody has met, checking a child in for the first time.

Before this, the lobby iPad could only find a family it already knew. A
first-time visitor with a four year old got a volunteer, a paper form, and a
promise that somebody would add them later, which is the moment a church most
wants to look like it has its act together and the moment it has the least to
work with.

Three decisions shape this module.

**It goes through `app.guests`, not around it.** A child checked in by a
stranger is a visiting family, which is exactly what a connect card records.
Writing a second path to the roster would mean two definitions of what a
guest is, two matching rules, two places the follow-up sequence has to be
started, and one of them would drift. So this builds the family and then
hands the parent to `guests.submit`, which already matches against the
roster, never demotes a member, enrols the sequence, and produces the card
the Guests screen and the staff alert are built on.

**A returning family is not a new one.** The form is on the screen a parent
reaches when the kiosk did not recognise them, and plenty of people land
there because they mistyped their number or because their record has a
different phone on it. Matching on the phone before creating anything means
the second Sunday attaches to the first family rather than making a parallel
one, and a church's roster does not fill up with the same household four
times.

**Children are created with no contact details and no login.** They are on
the roster so they can be checked in and collected, nothing more. The
household holds the parent's phone, which is what the kiosk searches on
next week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.extensions import db
from app.guests import CardRefused, clean
from app.models import KIND_CREATED, Household, Person, PersonEvent
from app.models.base import utcnow

# What a child row on the form can carry. A first name is the only thing
# required: a parent standing at a tablet with a toddler on one hip will
# give you a name, and anything else has to be optional or the form does not
# get finished.
MAX_CHILDREN = 6

NEEDS_PARENT_NAME = "needs_parent_name"
NEEDS_PHONE = "needs_phone"
NEEDS_CHILD = "needs_child"


@dataclass(frozen=True)
class ChildEntry:
    """One row of the form, after cleaning."""

    first_name: str
    last_name: str = ""
    birthdate: date | None = None


def read_children(form, last_name: str = "") -> list:
    """Pull the child rows out of a submitted form.

    Rows are read by index rather than by `getlist`, because the three lists
    would have to line up and a browser that drops an empty field would
    silently shift a birthday onto the wrong child.
    """
    children = []
    for index in range(MAX_CHILDREN):
        first = clean(form.get(f"child_first_{index}"), 80)
        if not first:
            continue
        children.append(ChildEntry(
            first_name=first,
            last_name=clean(form.get(f"child_last_{index}"), 80) or last_name,
            birthdate=_as_date(form.get(f"child_birthdate_{index}")),
        ))
    return children


def _as_date(value):
    """A birthday, or nothing.

    Never raises. A date that will not parse is a browser with no date
    picker and somebody typing into a plain box, and refusing the whole
    family over it would be absurd: the birthday only decides which room
    the app suggests, and a missing one already means kid.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    # A future birthday is a typo, and an age of minus three would put a
    # child in no room at all.
    return parsed if parsed <= date.today() else None


def household_name(last_name: str, first_name: str) -> str:
    """What the family is called on the roster and on the tag.

    "The Slinkards" reads like a family. A surname ending in s would come
    out "The Joneses" if this tried to be clever about plurals, so it does
    not try: the name is editable on the household screen and a volunteer
    reading a tag needs it to be recognisable, not grammatical.
    """
    last = (last_name or "").strip()
    if last:
        return f"The {last} family"
    return f"{(first_name or 'Guest').strip()}'s family"


def register(church, *, parent_first: str, parent_last: str, phone: str,
             children: list, note: str = ""):
    """Create the family, record the connect card, return what was made.

    Returns `(household, parent, kids, card)`. Caller commits.

    Nothing here checks anybody in. Check-in needs the open session and the
    pickup code, which belong to the kiosk, and keeping them out of this
    module is what lets a church add a family from a desk without a service
    running.
    """
    from app import guests

    first = clean(parent_first, 80)
    last = clean(parent_last, 80)
    phone = clean(phone, 40)

    if not first:
        raise CardRefused(NEEDS_PARENT_NAME)
    if not phone:
        raise CardRefused(NEEDS_PHONE)
    if not children:
        raise CardRefused(NEEDS_CHILD)

    # The card first, because `guests.submit` is what decides whether this
    # parent is somebody new or somebody the church already has. Creating a
    # household before asking that question is how a family ends up with two.
    card = guests.submit(
        church.id,
        first_name=first,
        last_name=last,
        phone=phone,
        note=note,
        # They are standing in the building having handed over a phone
        # number so somebody can reach them about their child. That is a
        # request for contact in every sense that matters.
        wants_contact=True,
    )
    parent = db.session.get(Person, card.person_id)

    household = parent.household
    if household is None:
        household = Household(
            church_id=church.id,
            name=household_name(last, first),
        )
        db.session.add(household)
        db.session.flush()
        parent.household_id = household.id

    # So the family can check themselves in next week whichever way this
    # church has the kiosk set up: by the code if it asks for a code, by the
    # parent's number if it asks for a number.
    household.ensure_checkin_pin()

    kids = []
    for entry in children:
        existing = _already_here(household, entry)
        if existing is not None:
            # A parent who taps the first-time link twice, or comes back next
            # Sunday and uses it again rather than their number. Their child
            # exists; making a second record would split the attendance
            # history and put two identical names on the check-in screen.
            if existing.birthdate is None and entry.birthdate is not None:
                existing.birthdate = entry.birthdate
            kids.append(existing)
            continue

        child = Person(
            church_id=church.id,
            first_name=entry.first_name,
            last_name=entry.last_name or last,
            household_id=household.id,
            is_child=True,
            birthdate=entry.birthdate,
            stage="visitor",
            first_seen_on=date.today(),
            # Approved on the spot. A child waiting in a queue for somebody
            # to confirm them is a child who cannot be checked in, and there
            # is a volunteer standing next to the parent who just gave their
            # name.
            approved_at=utcnow(),
        )
        db.session.add(child)
        db.session.flush()
        PersonEvent.record(child, KIND_CREATED, "Added at kids check-in")
        kids.append(child)

    return household, parent, kids, card


def _already_here(household, entry: ChildEntry):
    """A child of this household who is plainly the same person.

    Matched on the first name alone, within one household. Two siblings
    called Jack is not a case worth splitting a record over, and the cost of
    being wrong here is one child's history attached to another's name in a
    family that can see and fix it, against a roster that doubles every time
    a parent uses the wrong button.
    """
    wanted = entry.first_name.strip().lower()
    for member in household.members:
        if not member.is_child:
            continue
        if member.first_name.strip().lower() == wanted:
            return member
    return None
