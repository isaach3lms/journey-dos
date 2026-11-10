"""Pickup codes.

Two codes exist in kids check-in and they do different jobs. Confusing them is
the failure that matters, so it is worth stating plainly:

**The household PIN identifies.** It answers "which family are you" at a
kiosk. It is permanent, printed on a label, visible in the member app, and
never changes on its own. See `app/checkin_pin.py`.

**The pickup code authorizes.** It answers "may this person collect this
child". It is generated fresh for one household for one session, shared across
siblings so a parent carries one code rather than three, and it is meaningless
an hour later.

A permanent code cannot authorize a pickup. If it could, anyone who ever saw a
label, a phone screen, or a check-in sticker on a coat could collect a child
weeks later. That is why these are separate values with separate lifetimes, and
why nothing in this codebase should ever accept a `checkin_pin` where a pickup
code is expected.

Letters, not digits. A four-digit PIN and a four-digit pickup code on the same
label would be confused by a tired volunteer at 11am on a Sunday, and the
confusion would run in the dangerous direction.
"""

from __future__ import annotations

import secrets

# No I, O, S, or Z. On a printed label at arm's length these read as 1, 0, 5,
# and 2, and a misread code at a pickup desk means either a child handed to the
# wrong adult or a queue while somebody finds a staff member.
ALPHABET = "ABCDEFGHJKLMNPQRTUVWXY"

CODE_LENGTH = 4
MAX_GENERATION_ATTEMPTS = 50


def candidate(length: int = CODE_LENGTH) -> str:
    """`secrets`, not `random`. This code decides who leaves with a child."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def generate_pickup_code(is_taken, length: int = CODE_LENGTH) -> str:
    """Find a code not already in use in this session.

    Uniqueness is scoped to one session, not forever. Two families having the
    same code six months apart is fine; two families having it on the same
    Sunday is a child handed to the wrong adult.

    Running out of attempts raises rather than returning a duplicate.
    """
    for _ in range(MAX_GENERATION_ATTEMPTS):
        code = candidate(length)
        if not is_taken(code):
            return code

    raise RuntimeError(
        f"Could not find a free {length} character pickup code in "
        f"{MAX_GENERATION_ATTEMPTS} attempts. That means roughly "
        f"{len(ALPHABET) ** length:,} codes are in use in one session, which "
        f"is not a real church. Something is wrong with the session scoping."
    )


def normalize(code: str | None) -> str:
    """Clean what somebody typed at a pickup desk.

    Uppercased and stripped of spaces, because a parent reading off a phone
    types it however it comes out.
    """
    if not code:
        return ""
    return "".join(ch for ch in code.upper() if ch.isalnum())
