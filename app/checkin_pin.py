"""Household check-in PINs.

Per spec v3 section B, the system generates the PIN rather than deriving it
from a phone number. That decision deleted a screen from the build: with a
uniqueness constraint and a retry loop, two households cannot end up sharing a
code, so the disambiguation screen the phone-derived design needed is simply
not required.

**The PIN is identification, not authorization.** It answers "which family are
you" at a kiosk. It must never authorize a pickup. The pickup code, generated
per household per check-in session, is the actual control, and it arrives with
Kids at increment 11. Anyone reading this file later should not be tempted to
reuse `checkin_pin` as a credential.

Capacity: four digits gives 10,000 codes less the blocklist. At Journey's
projected 150 to 250 households that is around 2 percent occupancy, so the
generator finds a free code on the first attempt essentially always. The column
is `VARCHAR(6)` today so widening past 2,500 households is a config change
rather than a migration. Four digits is what a parent can memorize, which is
worth protecting.
"""

from __future__ import annotations

import secrets

PIN_LENGTH = 4
MAX_GENERATION_ATTEMPTS = 50


def _sequences(length: int) -> set[str]:
    """Ascending and descending runs: 1234, 4321, 0123, and so on."""
    digits = "0123456789"
    out = set()
    for start in range(10):
        forward = "".join(digits[(start + i) % 10] for i in range(length))
        out.add(forward)
        out.add(forward[::-1])
    return out


def blocklist(length: int = PIN_LENGTH, church_street_number: str | None = None) -> set[str]:
    """Codes a family should never be handed.

    Repeats and runs are the first things a stranger tries at a kiosk. The
    church's own street number is on the building and on every piece of mail,
    which makes it the single most guessable four digits in the room.
    """
    blocked = {str(d) * length for d in range(10)}
    blocked |= _sequences(length)
    if church_street_number:
        digits = "".join(c for c in church_street_number if c.isdigit())
        if len(digits) == length:
            blocked.add(digits)
    return blocked


def candidate(length: int = PIN_LENGTH) -> str:
    """One random code. `secrets`, not `random`: this ends up on a lanyard."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def generate_pin(
    is_taken,
    length: int = PIN_LENGTH,
    church_street_number: str | None = None,
) -> str:
    """Find an unused, non-guessable code.

    `is_taken` is a callable so this function stays free of database access and
    can be tested against an ordinary set.

    Running out of attempts raises rather than returning a duplicate. A
    collision would give two families the same code at a kiosk, which is a
    child-safety problem, not an inconvenience.
    """
    blocked = blocklist(length, church_street_number)

    for _ in range(MAX_GENERATION_ATTEMPTS):
        code = candidate(length)
        if code in blocked:
            continue
        if is_taken(code):
            continue
        return code

    raise RuntimeError(
        f"Could not find a free {length} digit check-in PIN in "
        f"{MAX_GENERATION_ATTEMPTS} attempts. The code space is close to full. "
        f"Widen PIN_LENGTH to 5; the column is already VARCHAR(6)."
    )
