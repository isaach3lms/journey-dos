"""Matching a donor to a person.

Tithely donor identity is not a DOS person, and no amount of code makes it one.
This narrows the gap and then stops, because the failure mode here is not "we
missed a match", it is **money attributed to the wrong person**, which is worse
than money attributed to nobody.

The order is email, then phone, then name, and each rung is weaker than the one
above it:

- **Email** is close to an identifier. People share addresses inside a
  household, which is why an exact match on a unique address is accepted and a
  match on an address held by two people is not.
- **Phone** is a household number as often as a personal one, so the same rule
  applies.
- **Name** is not an identifier at all. Two Chris Vaughns in a church of 400 is
  ordinary, and a church plant with three Webbs is the normal case, not the
  edge case.

**The rule that matters: ambiguity never auto-matches.** If a candidate lookup
returns more than one person, the gift goes to the review queue with the
candidates attached and a human decides. A confident wrong answer here shows up
in a giving statement at year end, addressed to somebody who did not give it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.extensions import db
from app.models import Person

CONFIDENCE_EMAIL = "email"
CONFIDENCE_PHONE = "phone"
CONFIDENCE_NAME = "name"
CONFIDENCE_NONE = "none"
CONFIDENCE_AMBIGUOUS = "ambiguous"

CONFIDENCE_LABELS = {
    CONFIDENCE_EMAIL: "Matched on email",
    CONFIDENCE_PHONE: "Matched on phone",
    CONFIDENCE_NAME: "Matched on name",
    CONFIDENCE_AMBIGUOUS: "More than one person could be this",
    CONFIDENCE_NONE: "Nobody on the roster matches",
}

# Only email is trusted enough to attach without a human. Phone is often a
# household line and a name is not an identifier, so both are suggested and
# neither is applied. Widening this set is a decision with real consequences.
AUTO_MATCH_CONFIDENCE = frozenset({CONFIDENCE_EMAIL})


@dataclass(frozen=True)
class MatchResult:
    confidence: str
    person: Person | None
    candidates: list

    @property
    def is_auto(self) -> bool:
        return self.confidence in AUTO_MATCH_CONFIDENCE and self.person is not None

    @property
    def label(self) -> str:
        return CONFIDENCE_LABELS.get(self.confidence, self.confidence.title())


def normalize_phone(raw: str | None) -> str:
    """Digits only, last ten.

    "(573) 555-4085", "573-555-4085", and "+1 573 555 4085" are one number, and
    a church's records contain all three spellings.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_name(raw: str | None) -> str:
    """Lowercased, punctuation dropped, whitespace collapsed."""
    if not raw:
        return ""
    cleaned = re.sub(r"[^a-z\s]", " ", raw.lower())
    return " ".join(cleaned.split())


def split_donor_name(raw: str | None) -> tuple[str, str]:
    """Best effort first and last from a single provider name field."""
    parts = normalize_name(raw).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def match_donor(
    church_id: int,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> MatchResult:
    """Find the person a donor record refers to, or say why not."""

    if email:
        candidates = list(
            db.session.scalars(
                db.select(Person).where(
                    Person.church_id == church_id,
                    Person.is_archived.is_(False),
                    db.func.lower(Person.email) == email.strip().lower(),
                )
            )
        )
        if len(candidates) == 1:
            return MatchResult(CONFIDENCE_EMAIL, candidates[0], candidates)
        if len(candidates) > 1:
            # A shared household address. Guessing which spouse gave is the
            # error this whole module exists to avoid.
            return MatchResult(CONFIDENCE_AMBIGUOUS, None, candidates)

    normalized_phone = normalize_phone(phone)
    if normalized_phone:
        candidates = [
            person
            for person in db.session.scalars(
                db.select(Person).where(
                    Person.church_id == church_id,
                    Person.is_archived.is_(False),
                    Person.phone.is_not(None),
                )
            )
            if normalize_phone(person.phone) == normalized_phone
        ]
        if len(candidates) == 1:
            return MatchResult(CONFIDENCE_PHONE, candidates[0], candidates)
        if len(candidates) > 1:
            return MatchResult(CONFIDENCE_AMBIGUOUS, None, candidates)

    first, last = split_donor_name(name)
    if first and last:
        candidates = [
            person
            for person in db.session.scalars(
                db.select(Person).where(
                    Person.church_id == church_id, Person.is_archived.is_(False)
                )
            )
            if normalize_name(person.first_name) == first
            and normalize_name(person.last_name) == last
        ]
        if len(candidates) == 1:
            return MatchResult(CONFIDENCE_NAME, candidates[0], candidates)
        if len(candidates) > 1:
            return MatchResult(CONFIDENCE_AMBIGUOUS, None, candidates)

    return MatchResult(CONFIDENCE_NONE, None, [])


def suggest_for_gift(gift) -> MatchResult:
    """What the review queue offers a human, without deciding for them."""
    return match_donor(
        gift.church_id,
        email=gift.donor_email,
        phone=None,
        name=gift.donor_name,
    )
