"""Removing an archived person from the roster for good.

Archiving hides somebody. This removes them, and it is the only action in the
system with nothing left to look at afterwards: no row, no timeline, no
screen that says they were ever here. Everything in this module exists to
make that deliberate rather than available.

**It only ever applies to somebody already archived.** Two steps, with a
decision in between, and the first one is reversible. Nobody deletes a person
from the roster by misreading a button.

**Two things block it outright**, because deleting through them would quietly
damage something the church needs:

*A sign-in account.* The login would survive with nothing behind it, which is
an account that can still open the app and whose screens have no person to
render. Remove the account first.

*Giving history.* The gift rows survive the person by design, because a
church's financial record for a year must not change because somebody tidied
the roster in March. What they become is unattributed, and an unattributed
gift is a reconciliation problem nobody will connect to this afternoon. The
remedy is to leave them archived, which already hides them everywhere.

**Everything else is counted, not blocked.** The caller shows the counts
before anybody confirms, because "this deletes 14 timeline entries and 3
check-ins" is a decision and "are you sure?" is not.

What the database does with the rest is already settled by the schema and is
not re-decided here: follow-up history, group membership, check-ins,
enrolments and preferences go with the person; messages they wrote, gifts,
connect cards and sent email survive with the person detached, because each
of those is a record of something that happened to the church rather than a
property of the person.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.extensions import db

# Why somebody cannot be deleted. The caller turns these into a sentence.
BLOCKED_ACCOUNT = "account"
BLOCKED_GIVING = "giving"
BLOCKED_NOT_ARCHIVED = "not_archived"


@dataclass(frozen=True)
class Verdict:
    """Whether this person can be deleted, and what it would take with them."""

    person_id: int
    name: str
    blocked_by: str | None = None
    counts: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.blocked_by is None

    @property
    def takes_anything(self) -> bool:
        return any(self.counts.values())


def _count(model, **where) -> int:
    from sqlalchemy import func

    return db.session.scalar(
        db.select(func.count(model.id)).filter_by(**where)
    ) or 0


def inspect(person) -> Verdict:
    """What deleting this person would mean. Reads only.

    Counts are deliberately the things a human recognises: what they did
    here and what was recorded about them. Rows that survive the delete are
    not counted, because listing them would read as a warning about
    something that is not at risk.
    """
    from app.models import (
        Checkin,
        ContactLog,
        ExternalGift,
        ExternalRecurringGift,
        GroupMembership,
        NextStep,
        PersonEvent,
        SequenceEnrollment,
        User,
    )

    name = person.full_name

    if not person.is_archived:
        return Verdict(person.id, name, blocked_by=BLOCKED_NOT_ARCHIVED)

    has_account = db.session.scalar(
        db.select(User.id).where(User.person_id == person.id)
    ) is not None
    if has_account:
        return Verdict(person.id, name, blocked_by=BLOCKED_ACCOUNT)

    gifts = (_count(ExternalGift, person_id=person.id)
             + _count(ExternalRecurringGift, person_id=person.id))
    if gifts:
        return Verdict(person.id, name, blocked_by=BLOCKED_GIVING)

    return Verdict(person.id, name, counts={
        "events": _count(PersonEvent, person_id=person.id),
        "checkins": _count(Checkin, person_id=person.id),
        "contacts": _count(ContactLog, person_id=person.id),
        "steps": _count(NextStep, person_id=person.id),
        "groups": _count(GroupMembership, person_id=person.id),
        "sequences": _count(SequenceEnrollment, person_id=person.id),
    })


def delete(person, verdict: Verdict | None = None) -> Verdict:
    """Remove the person. Caller commits.

    Re-inspects rather than trusting the verdict it was handed. The counts
    on a confirmation screen were true when the page rendered, and somebody
    could have been given an account or matched to a gift in the minutes
    before the button was pressed. The check that decides is the one at the
    moment of deleting, which is the same rule the outbox applies to an
    opt-out.

    Raises `Blocked` rather than returning quietly: by this point a human
    has confirmed, and a delete that silently does nothing is worse than an
    error, because they will come back next week and try again.
    """
    fresh = inspect(person)
    if not fresh.allowed:
        raise Blocked(fresh)

    _detach(person.id)
    db.session.delete(person)
    return fresh


def _detach(person_id: int) -> None:
    """Cut the person out of the rows that outlive them, by hand.

    The schema already says `ON DELETE SET NULL` on every one of these, so
    on Postgres this is belt and braces. It is not optional, because this
    project runs on SQLite locally and SQLite does not enforce foreign keys
    unless somebody turns the pragma on, which nothing here does. Without
    this, deleting a person on a developer's machine leaves a connect card
    pointing at a row that no longer exists, and the first thing anybody
    notices is `card.person` raising on a screen that works in production.

    Doing it in the application also makes the rule readable. "These four
    things survive the person, detached" is a sentence; four `ondelete`
    clauses spread across four model files is an archaeology exercise.
    """
    from app.models import (
        ExternalGift,
        ExternalRecurringGift,
        GuestCard,
        Message,
        OutboxMessage,
    )
    from app.models.moderation import MessageReport

    detachments = (
        # The church still had a guest that Sunday, and the number for that
        # week must not change because somebody tidied the roster in March.
        (GuestCard, GuestCard.person_id),
        # A conversation is a record of what was said in it. Deleting
        # somebody must not rewrite other people's threads.
        (Message, Message.author_person_id),
        # What was sent, was sent. The delivery screen has to stay honest.
        (OutboxMessage, OutboxMessage.person_id),
        # Blocked above, so this never runs today. Here anyway: if that
        # rule is ever relaxed, the money must not vanish with it.
        (ExternalGift, ExternalGift.person_id),
        (ExternalRecurringGift, ExternalRecurringGift.person_id),
        # The report stays so the moderation history is intact; who made it
        # does not.
        (MessageReport, MessageReport.reporter_person_id),
    )

    for model, column in detachments:
        db.session.execute(
            db.update(model).where(column == person_id).values({column: None})
        )


class Blocked(Exception):
    """Raised when a delete is refused. Carries the verdict."""

    def __init__(self, verdict: Verdict):
        self.verdict = verdict
        super().__init__(verdict.blocked_by or "blocked")


def summarise(verdict: Verdict) -> str:
    """One line for the audit log: what went with them.

    Written out rather than left as a count of rows, because the entry is
    the only thing that survives the person and "deleted 1 person" answers
    nothing a year later.
    """
    from app.content import PEOPLE

    bits = []
    for key, template in (
        ("events", "purge_bit_events"),
        ("checkins", "purge_bit_checkins"),
        ("contacts", "purge_bit_contacts"),
        ("steps", "purge_bit_steps"),
        ("groups", "purge_bit_groups"),
        ("sequences", "purge_bit_sequences"),
    ):
        count = verdict.counts.get(key, 0)
        if count:
            bits.append(PEOPLE[template].format(count=count))

    return ", ".join(bits) if bits else PEOPLE["purge_bit_nothing"]
