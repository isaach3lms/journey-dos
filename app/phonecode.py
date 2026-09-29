"""Deriving a family's kids code from a parent's phone number.

Turned on per church. When it is on, the last four digits of a parent's phone
number do two jobs that were previously done by two separate values: they
identify the family at the kiosk, and they authorize a pickup at the desk.

**This is a deliberate trade of safety for convenience, made by the church.**
It is written down here rather than left implicit, because the next person to
read this file will otherwise assume the separation in `app/pickup.py` still
holds, and it does not:

- The code is permanent. A rotating pickup code is worthless an hour after the
  service. Phone digits are not, so anyone who has ever seen a tag, or who
  knows the number, can collect that child on any later Sunday they are
  checked in.
- The code is knowable from outside the system. A phone number is semi-public
  in a way a random code is not.
- Four digits is 10,000 values. Two families sharing the last four is not a
  rare event: at 200 households expect roughly two colliding pairs, at 400
  about eight.

The third of those is the one this module and its callers must never get
wrong. A collision that quietly returns two families' children under one code
is a child handed to the wrong adult, so every lookup here returns a *list*
and every screen that uses it asks for a last name before it shows anybody.
The first two are inherent to the choice and are the church's to carry.

The generated codes are still issued and still stored. Turning the church
setting off restores the old behaviour with nothing lost.
"""

from __future__ import annotations

CODE_LENGTH = 4


def digits(value: str | None) -> str:
    """Just the numbers. Phone numbers arrive formatted every possible way."""
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def last4(phone: str | None) -> str | None:
    """The last four digits, or None if there is no usable number.

    A number too short to have four digits gets None rather than a padded or
    partial code: a two digit code at a pickup desk is worse than no code,
    because the family would be handed one and it would collide with everybody.
    """
    found = digits(phone)
    if len(found) < CODE_LENGTH:
        return None
    return found[-CODE_LENGTH:]


def matches_last_name(household, wanted: str) -> bool:
    """Does anybody in this family answer to that last name?

    Checked against the members rather than the household's own name, because
    a household called "The Webb family" and a household called "Marcus and
    Dana Webb" should both answer to Webb, and a blended family may hold two
    surnames.
    """
    wanted = (wanted or "").strip().lower()
    if not wanted:
        return False
    if wanted in (household.name or "").lower():
        return True
    return any(
        wanted == (person.last_name or "").strip().lower()
        for person in household.members
    )
