"""Emailing a group of people at once.

The church already had two ways to reach everybody and neither was the one a
pastor wanted on a Tuesday afternoon. Posting an announcement with "also email
it" reaches the whole church, but only the whole church, and only as something
that also lives on a screen forever. A direct room reaches exactly the people
you add, one at a time. Neither answers "email the serving teams that we are
starting at eight on Sunday".

So this module owns one question: given a church and an audience, who gets it.
Everything else about sending already exists and is not duplicated here. The
actual send goes through `app.notify.notify`, which means the email and the app
notification go out together and the opt-out is checked once.

**Why audiences are a data structure, not rows.** An audience is shape, the
same way a discipleship stage is: its meaning lives in the function that
resolves it. "Everyone" is not a list somebody maintains, it is a query, and
the moment it becomes a stored list it starts being wrong the day after
somebody joins. A church that later wants saved segments gets a table of
*filters*, not a table of names, and every read already goes through
`resolve`.

**Two filters that are not obvious and matter.**

Children are excluded. A nine year old's row usually carries a parent's
address, so including them sends the parent two copies of the same email and
makes the recipient count a lie.

People waiting for approval are excluded. `Person.is_approved` is already the
gate on seeing anything the church posts to everyone, and an email is more
public than a screen: somebody who typed an address into a sign-up form has
not been confirmed to be a real person at this church yet.

Addresses are deduplicated. A household that shares one inbox gets one email,
not one per member, and the count shown before sending is the number of emails
that will actually leave.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.models import Group, GroupMembership, Person, Team, TeamMembership
from app.notify import notify
from app.stages import STAGES

# The category every one of these goes out under. Opt-out-able, and that is
# correct: a church-wide email is the thing somebody most reasonably wants to
# stop receiving, and the system must honour that without staff having to
# remember who asked.
CATEGORY = "announcement"

EVERYONE = "everyone"


@dataclass(frozen=True)
class Audience:
    """One choice in the audience list, with its size already counted.

    The count is part of the choice rather than something fetched afterwards,
    because the number is the thing that stops a mistake. "Everyone" and "Men's
    Tuesday study" look equally harmless in a dropdown until one of them says
    312 next to it.
    """

    value: str
    label: str
    recipients: tuple

    @property
    def count(self) -> int:
        return len(self.recipients)


def _eligible(person) -> bool:
    """Can this person be emailed as part of a church-wide send?

    Not the same question as `allows`, which is about consent. This is about
    whether the row represents somebody with an inbox who belongs to the
    church yet.
    """
    if person.is_child or person.is_archived:
        return False
    if not person.is_approved:
        return False
    if not (person.email or "").strip():
        return False
    return person.allows(CATEGORY)


def _dedupe(people) -> tuple:
    """One address, one email. First occurrence wins so the name on the email
    is stable rather than depending on query order."""
    seen, out = set(), []
    for person in people:
        address = (person.email or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        out.append(person)
    return tuple(out)


def resolve(church_id: int, value: str) -> tuple:
    """The people an audience value means, right now.

    An unknown value returns nothing rather than raising. A stale dropdown
    option, a group deleted between loading the form and submitting it, a
    hand-edited request: the right answer to all three is that there is nobody
    to email, which the caller reports as such.
    """
    everybody = list(db.session.scalars(Person.for_church(church_id)))
    eligible = [person for person in everybody if _eligible(person)]

    if value == EVERYONE:
        return _dedupe(eligible)

    kind, _, raw_id = value.partition(":")

    if kind == "stage":
        return _dedupe(person for person in eligible if person.stage == raw_id)

    by_id = {person.id: person for person in eligible}

    if kind == "group":
        group = Group.get_for_church(church_id, _as_int(raw_id))
        if group is None:
            return ()
        ids = db.session.scalars(
            db.select(GroupMembership.person_id).where(
                GroupMembership.church_id == church_id,
                GroupMembership.group_id == group.id,
            )
        )
        return _dedupe(by_id[pid] for pid in ids if pid in by_id)

    if kind == "team":
        team = Team.get_for_church(church_id, _as_int(raw_id))
        if team is None:
            return ()
        ids = db.session.scalars(
            db.select(TeamMembership.person_id).where(
                TeamMembership.church_id == church_id,
                TeamMembership.team_id == team.id,
            )
        )
        return _dedupe(by_id[pid] for pid in ids if pid in by_id)

    return ()


def _as_int(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def audiences(church_id: int) -> list[Audience]:
    """Every audience this church can pick, each with its size.

    Built in the order somebody reaches for them: the whole church, then the
    teams who are asked to turn up early, then the groups, then the stages,
    which are the one staff use least and the one most likely to be a mistake.

    Empty audiences are kept rather than hidden. A team with nobody on it still
    answers a question: the team exists and nobody is on it. Dropping the row
    would read as the team not existing.
    """
    out = [
        Audience(EVERYONE, "Everyone at the church", resolve(church_id, EVERYONE))
    ]

    for team in db.session.scalars(Team.for_church(church_id)):
        value = f"team:{team.id}"
        out.append(Audience(value, f"{team.name} team", resolve(church_id, value)))

    for group in db.session.scalars(Group.for_church(church_id)):
        value = f"group:{group.id}"
        out.append(Audience(value, group.name, resolve(church_id, value)))

    for stage in STAGES:
        value = f"stage:{stage.code}"
        out.append(Audience(value, f"{stage.label} stage", resolve(church_id, value)))

    return out


def audience_label(church_id: int, value: str) -> str:
    for audience in audiences(church_id):
        if audience.value == value:
            return audience.label
    return "nobody"


@dataclass(frozen=True)
class Sent:
    emailed: int
    pushed: int
    skipped: int

    @property
    def attempted(self) -> int:
        return self.emailed + self.skipped


def send(
    *,
    church_id: int,
    audience: str,
    subject: str,
    body_text: str,
    push: bool = True,
    actor=None,
    reference: str,
) -> Sent:
    """Queue one email per recipient. The caller commits.

    `reference` makes the dedupe key, and the dedupe key is what makes a
    double-submitted form harmless. Two clicks on Send produce one email per
    person, not two, and the second click reports zero sent rather than
    silently doubling a church-wide blast.

    Nothing is sent inline. Every message goes in the outbox and leaves on the
    next worker run, which is also what makes a 300 person send survive the
    request timing out halfway through.
    """
    emailed = pushed = skipped = 0

    for person in resolve(church_id, audience):
        result = notify(
            person=person,
            church_id=church_id,
            category=CATEGORY,
            subject=subject,
            body_text=body_text,
            push_title=subject,
            push_body=_push_body(body_text),
            url="/",
            tag=f"broadcast:{reference}",
            dedupe_key=f"broadcast:{reference}:person:{person.id}",
            # Email only when staff asked for email only. Blanking the push
            # text would not have done it: an empty notification is still a
            # notification, and it would have arrived saying nothing.
            push=push,
        )
        if result.emailed:
            emailed += 1
            person.ensure_unsubscribe_token()
        else:
            skipped += 1
        pushed += result.pushed

    return Sent(emailed=emailed, pushed=pushed, skipped=skipped)


def _push_body(body_text: str) -> str:
    """The first sentence or so, because a notification has about a hundred
    characters and an email body has no length limit."""
    flat = " ".join((body_text or "").split())
    if len(flat) <= 140:
        return flat
    return flat[:137].rstrip() + "..."
