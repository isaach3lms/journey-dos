"""Where kids stop and youth begin.

One module, because this line gets drawn in four places that must agree:
the roster filter, the counts on the rail, the check-in screen a volunteer
reads on Sunday, and the person's own record. Four copies of a birthday
calculation is four chances for a fourteen year old to be a youth on one
screen and a kid on another, and the screen that would be wrong is the one
in the lobby.

**Derived from the birthday, overridable by a human.** The rule is an age, so
the ordinary case needs nobody to do anything: a child becomes a youth on
their thirteenth birthday whether or not staff noticed. But a birthday is
optional on this roster and plenty of children have none recorded, and a
church will sometimes want a twelve year old with the youth or a thirteen
year old kept with the kids. So the age decides unless somebody has said
otherwise, and `youth_override` is how they say it.

**A missing birthday means kid.** It has to mean one of the two, and that is
the one that keeps somebody on the check-in screen rather than quietly
removing them from it. A fourteen year old with no birthday on file shows up
with the kids until somebody fixes either the birthday or the override, which
is visible and fixable; the other way round they would vanish from the lobby
iPad and nobody would know why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# The line itself.
#
# A constant rather than a church setting, because every church this is
# deployed for has drawn it at thirteen so far. It becomes a column the first
# time one of them wants twelve, and the only thing that changes is where
# this number is read from; nothing else in this module moves.
YOUTH_FROM_AGE = 13

KID = "kid"
YOUTH = "youth"
ADULT = "adult"

GROUPS = (KID, YOUTH, ADULT)

LABELS = {
    KID: "Kids",
    YOUTH: "Youth",
    ADULT: "Adults",
}


def born_on_or_before(today: date | None = None) -> date:
    """The latest birthday that still makes somebody a youth today.

    Expressed as a date rather than an age so the database can answer the
    question. Computing an age per row means loading the roster to count it;
    comparing a birthday against one cutoff is an index lookup.

    Leap years are the reason this is not one line. Somebody born on 29
    February has no birthday in most years, and `date(year - 13, 2, 29)`
    raises. They become a youth on the first of March, which is the same day
    the law in most places picks.
    """
    today = today or date.today()
    try:
        return today.replace(year=today.year - YOUTH_FROM_AGE)
    except ValueError:
        return date(today.year - YOUTH_FROM_AGE, 3, 1)


def group_for(person, today: date | None = None) -> str:
    """Which of the three this person is in, right now.

    The single answer. Anything that needs to know asks here rather than
    doing its own arithmetic.
    """
    if not getattr(person, "is_child", False):
        return ADULT

    override = getattr(person, "youth_override", None)
    if override is not None:
        return YOUTH if override else KID

    birthdate = getattr(person, "birthdate", None)
    if birthdate is None:
        return KID

    return YOUTH if birthdate <= born_on_or_before(today) else KID


@dataclass(frozen=True)
class Band:
    """One heading on the check-in screen, and who is under it."""

    group: str
    label: str
    members: list


def bands_for(people, today: date | None = None) -> list[Band]:
    """Split a household into kids, youth and adults, in that order.

    Order is the point rather than an accident: the check-in screen is mostly
    used for kids, so they are at the top where a volunteer's thumb already
    is. Nobody is dropped, including adults, because that screen has never
    filtered anybody out and starting now would quietly stop a family who
    have been checking in for months.
    """
    held = {group: [] for group in GROUPS}
    for person in people:
        held[group_for(person, today)].append(person)

    return [Band(group, LABELS[group], held[group]) for group in GROUPS]
