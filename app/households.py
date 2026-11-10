"""Putting somebody into a family.

Three places now create or choose a household: a member adding their own
child, staff moving an existing person, and staff entering a child who has no
record yet. They share the parts that are easy to get subtly wrong and that
nothing visible would catch: the size cap, the same child entered twice under
two spellings, and the check-in code a household needs the moment it holds a
child.

**The code is the one that matters.** A household with a child in it and no
PIN is a family that cannot check in on Sunday, and nothing on any staff
screen shows the difference. It is minted here, at the moment a child joins,
rather than left to whichever screen somebody happens to open first.
"""

from __future__ import annotations

from app.extensions import db
from app.models import Household, Person

# A household holds one family. Twelve covers the largest real family, and it
# bounds the kiosk screen, which renders every member of a household at once.
MAX_HOUSEHOLD_MEMBERS = 12

MAX_HOUSEHOLD_NAME = 160


class HouseholdError(Exception):
    """Why this person cannot be placed. Carries a content key, not a sentence.

    The message belongs with the rest of the copy rather than in here, because
    a staff screen and a member screen say the same thing in different words.
    """

    def __init__(self, reason: str, **detail):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


UNKNOWN = "unknown"
FULL = "full"
DUPLICATE = "duplicate"


def default_name(person) -> str:
    """What a new household is called when nobody typed a name."""
    last = (person.last_name or person.first_name or "").strip()
    return f"The {last} family"[:MAX_HOUSEHOLD_NAME] if last else "Family"


def choose(church_id: int, *, household_id=None, new_name=None) -> tuple:
    """Resolve a form's household choice. Returns (household, is_new).

    A typed name wins over a picked id, because somebody who typed one meant
    it. Neither means no household, which is a legitimate answer for an adult
    and never one for a child; that rule lives with the caller who knows
    which it is holding.
    """
    name = (new_name or "").strip()
    if name:
        # A name that already exists joins that family rather than making a
        # second one with the same name.
        #
        # Two unrelated Smith families are real, so this is not obviously
        # right. It is the safer of two wrongs. Joining the wrong family puts
        # a child somewhere visible on their own record, fixable with the
        # picker beside this field. A duplicate family is invisible: two
        # identical rows in every picker from then on, siblings split between
        # them, two check-in codes, and a kiosk showing a parent half their
        # children. Somebody who really does mean a second Smith family can
        # say which one by typing a name that distinguishes it.
        existing = db.session.scalar(
            db.select(Household).where(
                Household.church_id == church_id,
                db.func.lower(Household.name) == name.lower(),
            )
        )
        if existing is not None:
            return existing, False

        household = Household(church_id=church_id, name=name[:MAX_HOUSEHOLD_NAME])
        db.session.add(household)
        db.session.flush()
        return household, True

    raw = str(household_id or "").strip()
    if not raw:
        return None, False

    if not raw.isdigit():
        # A non-numeric id is a typo or a probe, and guessing is how one
        # church's child lands in another church's family.
        raise HouseholdError(UNKNOWN)

    household = Household.get_for_church(church_id, int(raw))
    if household is None:
        raise HouseholdError(UNKNOWN)
    return household, False


def start_for(person) -> Household:
    """The household this person belongs to, created if they have none.

    The state a self-registered parent arrives in is a record with no family
    attached, so this is the ordinary path rather than an edge case.
    """
    if person.household is not None:
        return person.household

    household = Household(church_id=person.church_id, name=default_name(person))
    db.session.add(household)
    db.session.flush()
    person.household_id = household.id
    return household


def check_room(household, first: str, last: str) -> None:
    """Refuse a household that is full or already holds this name.

    Both failures are the same underlying mistake, somebody entered twice,
    and both are worth catching here rather than discovering at a kiosk with
    a queue behind it.
    """
    living = [m for m in household.members if not m.is_archived]

    if len(living) >= MAX_HOUSEHOLD_MEMBERS:
        raise HouseholdError(FULL, max=MAX_HOUSEHOLD_MEMBERS)

    wanted = f"{first} {last}".strip().lower()
    if any(m.full_name.strip().lower() == wanted for m in living):
        raise HouseholdError(DUPLICATE, name=f"{first} {last}".strip())


def place_child(household, *, church_id: int, first: str, last: str,
                birthdate=None, stage: str, notes: str | None = None,
                approved_at=None) -> Person:
    """Create a child in this household, with the code check-in needs.

    Caller commits, and caller has already called `check_room`. Splitting
    those apart lets the two staff screens and the member screen phrase the
    refusal in their own words while the record is built one way.
    """
    child = Person(
        church_id=church_id,
        first_name=first[:80],
        last_name=last[:80],
        birthdate=birthdate,
        is_child=True,
        household_id=household.id,
        stage=stage,
        notes=(notes or "").strip()[:500] or None,
        approved_at=approved_at,
    )
    db.session.add(child)
    db.session.flush()

    # Before anybody leaves the screen, rather than the first time somebody
    # opens the family row. A household holding a child and no code is a
    # family that cannot check in, and no screen shows the difference.
    household.ensure_checkin_pin()
    return child
