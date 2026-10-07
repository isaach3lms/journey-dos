"""Telling somebody they are on the plan.

Being scheduled to serve notified nobody. A leader added twelve people to a
Sunday and twelve people found out when somebody chased them, because the
only thing that ever sent was a button called "Ask the team" that a leader
had to remember to press. The plan screen said "asked to play drums" in a
flash message and the person it said that about had not been asked anything.

**Assigning is asking.** So the send moved to the moment somebody is put on
the plan, and "Ask the team" became what its name suggests: the chase for
the ones who have not answered. One function does both, with the same
wording and the same token, because two functions is how the first one ends
up being the one that forgets push.

**A draft sends nothing.** A leader building a Sunday on Thursday moves
people on and off it, and a notification for each move is how a worship team
mutes the app. Publishing is already the moment this system uses for "the
team can see this now", so publishing is the moment the asks go out, and an
assignment added to an already published plan goes immediately.

**Being taken off only tells somebody who was told.** Removing a person from
a draft is a leader changing their mind before anybody knew. Removing one
from a published plan they were asked about is a thing they need to hear, or
they turn up on Sunday to a slot somebody else is in.
"""

from __future__ import annotations

from flask import current_app, url_for

from app.content import SERVICES
from app.models.base import utcnow
from app.notify import notify
from app.timeutil import format_local


def _when(service, church) -> str:
    return format_local(service.starts_at, church, "%A %-d %B, %-I:%M%p")


def ask_to_serve(service, assignment, church) -> bool:
    """Ask one person whether they can serve. Returns whether anything went.

    Caller commits. The dedupe key is one ask per person per slot per
    calendar day, which is what makes the two callers safe together: a leader
    who adds somebody and then presses "Ask the rest again" in the same
    minute sends one notification, and a genuine chase tomorrow still goes.

    Never raises. A plan edit must not fail because a notification could not
    be built.
    """
    person = assignment.person
    if person is None:
        return False

    # No email is not a reason to skip. `notify` pushes a person who has no
    # address on their roster record, and the gate that used to be here
    # turned "we could not email them" into "we told them nothing", which on
    # an app-first roster is most of a youth team.
    try:
        token = assignment.ensure_respond_token()
        link = url_for("invite.respond", token=token, _external=True,
                       _scheme="https")
        when = _when(service, church)

        result = notify(
            person=person,
            church_id=church.id,
            # Serving, not marketing. Somebody who left the newsletter still
            # needs to be asked whether they can play on Sunday.
            category="group",
            subject=SERVICES["invite_subject"].format(date=when),
            body_text=SERVICES["invite_body"].format(
                name=person.first_name,
                service=service.name,
                date=when,
                position=assignment.role_name,
                link=link,
                church=church.name,
            ),
            push_title=SERVICES["invite_push_title"].format(church=church.name),
            push_body=SERVICES["invite_push_body"].format(
                position=assignment.role_name, date=when
            ),
            url=url_for("invite.respond", token=token),
            tag=f"invite:{assignment.id}",
            dedupe_key=f"invite:{service.id}:{assignment.id}:"
            f"{utcnow().date().isoformat()}",
        )
    except Exception:  # noqa: BLE001
        current_app.logger.exception(
            "Serving ask failed for assignment %s", getattr(assignment, "id", None)
        )
        return False

    if result.emailed or result.pushed:
        person.ensure_unsubscribe_token()
        assignment.invited_at = utcnow()
        return True
    return False


def ask_everyone_waiting(service, church) -> int:
    """Ask every unanswered slot on this plan. Returns how many were asked.

    Used by publishing, which is the moment a plan built as a draft becomes
    real, and by the "Ask the rest again" button. Only people who have not
    answered are asked: re-running it after adding somebody chases the new
    person and leaves the rest alone, which is the behaviour a leader wants on
    a Thursday when half the team has replied.
    """
    if not service.is_published:
        return 0
    return sum(
        1 for assignment in service.assignments
        if not assignment.has_answered and ask_to_serve(service, assignment, church)
    )


def tell_removed(service, assignment, church) -> bool:
    """Tell somebody they are off the plan, if they were ever told they were on.

    Caller commits, and must call this before deleting the row: the person and
    the slot name are read off it.

    Silence is correct for a slot nobody was asked about. A leader moving
    people around a draft on Thursday is not an event in anybody's week, and a
    notification for one would teach a team that these mean nothing.
    """
    person = assignment.person
    if person is None or not assignment.was_invited:
        return False

    try:
        when = _when(service, church)
        result = notify(
            person=person,
            church_id=church.id,
            category="group",
            subject=SERVICES["removed_subject"].format(date=when),
            body_text=SERVICES["removed_body"].format(
                name=person.first_name,
                service=service.name,
                date=when,
                position=assignment.role_name,
                church=church.name,
            ),
            push_title=SERVICES["removed_push_title"].format(church=church.name),
            push_body=SERVICES["removed_push_body"].format(
                position=assignment.role_name, date=when
            ),
            url=url_for("member.serve"),
            tag=f"invite:{assignment.id}",
            # Keyed on the assignment rather than the day. Being taken off a
            # plan happens once, and a leader who removes and re-adds somebody
            # is a different assignment row.
            dedupe_key=f"unassigned:{service.id}:{assignment.id}",
        )
    except Exception:  # noqa: BLE001
        current_app.logger.exception(
            "Removal notice failed for assignment %s", getattr(assignment, "id", None)
        )
        return False

    return bool(result.emailed or result.pushed)
