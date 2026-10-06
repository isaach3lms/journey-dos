"""Taking a request for a login, and getting it in front of a human.

Named for what it holds rather than squeezed into `app/accounts.py`, which
creates logins. This creates nothing: it records that somebody asked, and
tells whoever does the creating.

**Everything on the form goes in the email.** The connect card's alert
deliberately withholds what the guest wrote, because that is a prayer request
and the alert only needs to say a card exists. This is the opposite case:
every field here is the material somebody needs in front of them to set the
account up, and making them open a page to get it is how the request sits
unanswered. It does mean a household's address and children go to whatever
address is configured, so the form says so plainly rather than leaving the
person to assume.
"""

from __future__ import annotations

from app.extensions import db
from app.models import AccountRequest
from app.models.account_request import OPEN

MAX_TEXT = 2000

NEEDS_NAME = "needs_name"
NEEDS_EMAIL = "needs_email"
ALREADY_ASKED = "already_asked"

# Same transactional category as the connect card's alerts. A church cannot
# unsubscribe from being told somebody wants in.
CATEGORY = "guest_card"


class Refused(Exception):
    """Why this is not a request. Carries a content key."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def clean(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


def submit(church, *, first_name: str, last_name: str = "", email: str = "",
           phone: str = "", address: str = "", household: str = "") -> AccountRequest:
    """Record a request. Caller commits."""
    first = clean(first_name, 80)
    email = clean(email, 255).lower()

    if not first:
        raise Refused(NEEDS_NAME)
    if "@" not in email or "." not in email.split("@")[-1]:
        # An account is an email address, so a request without a usable one
        # cannot be fulfilled and should be refused while somebody is still
        # looking at the form rather than days later by a person who cannot
        # reply to tell them.
        raise Refused(NEEDS_EMAIL)

    waiting = db.session.scalar(
        db.select(AccountRequest).where(
            AccountRequest.church_id == church.id,
            db.func.lower(AccountRequest.email) == email,
            AccountRequest.status == OPEN,
        )
    )
    if waiting is not None:
        # Somebody who hears nothing for a week asks again. A second row is
        # not more information, and it makes the list look like two families.
        raise Refused(ALREADY_ASKED)

    made = AccountRequest(
        church_id=church.id,
        first_name=first,
        last_name=clean(last_name, 80),
        email=email,
        phone=clean(phone, 40) or None,
        address=clean(address, MAX_TEXT) or None,
        household=clean(household, MAX_TEXT) or None,
    )
    db.session.add(made)
    db.session.flush()
    return made


def alert(church, made: AccountRequest, *, link: str) -> int:
    """Email whoever sets accounts up. Caller commits.

    Goes to the same setting the connect card's account box uses, so a church
    that moved one moved both and there is no second place to remember.
    """
    from app.content import PEOPLE
    from app.mail import NotQueued, queue

    told = 0
    for address in church.account_request_recipients:
        try:
            queue(
                church_id=church.id,
                category=CATEGORY,
                subject=PEOPLE["request_subject"].format(
                    name=made.full_name, church=church.name),
                body_text=PEOPLE["request_body"].format(
                    name=made.full_name,
                    church=church.name,
                    email=made.email,
                    phone=made.phone or PEOPLE["request_none"],
                    address=made.address or PEOPLE["request_none"],
                    household=made.household or PEOPLE["request_none"],
                    link=link,
                ),
                to_email=address,
                dedupe_key=f"accountreq:{made.id}:{address}",
            )
        except NotQueued:
            continue
        told += 1
    return told
