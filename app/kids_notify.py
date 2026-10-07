"""Telling a household that their child is in a room, and that they are out.

Check-in notified nobody. A volunteer tapped four names, a printer produced
four tags, and the only person who knew anything had happened was the
volunteer. The parent walking away from the desk had the tag in their hand so
check-in was arguably covered, but **check-out was the real gap**: a
grandparent collects a child at 11:40 and the parent in the service finds out
when they get to the room and it is empty.

Three decisions hold this together.

**The pickup code is never in a notification.** It is a live credential for
removing a child from a room. A push payload is handed to a third party and
rendered on a lock screen in a crowded lobby, and both of those are worse
places for it than the printed tag it already lives on. The same goes for
email: the forgot-PIN screen exists for the household PIN and that is a
different secret with a different job. See app/pickup.py.

**Adults only, and not the one standing at the desk.** A child does not need
a notification about themselves, and the volunteer who just tapped the button
does not need to be told what they did. Youth are adults for this purpose in
one direction only: a fifteen year old checked in by a parent is told nothing,
because the notification is for the person responsible for them.

**Transactional.** Under `kids_checkin`, which ignores the opt-out, for the
same reason the pickup code email does: a parent who left the newsletter has
not asked to stop being told where their child is.
"""

from __future__ import annotations

from flask import current_app, url_for

from app.ages import ADULT, group_for
from app.content import KIDS
from app.notify import notify

CATEGORY = "kids_checkin"


def _responsible(household, exclude_user=None):
    """The adults in this household who should hear about a check-in.

    Excludes the account that performed it, when that account belongs to
    somebody in this household: a parent checking in their own child at the
    kiosk does not need to be told they did.
    """
    if household is None:
        return []

    skip = getattr(exclude_user, "person_id", None)
    return [
        person for person in household.members
        if group_for(person) == ADULT
        and not person.is_archived
        and person.id != skip
    ]


def _names(people) -> str:
    """First names, in the order they were checked in, as a readable list."""
    names = [p.first_name for p in people if p.first_name]
    if not names:
        return KIDS["notify_somebody"]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _rooms(checkins) -> str:
    """The rooms involved, deduplicated, or an empty string for none.

    Two children in two rooms is the normal case and both matter: it is the
    answer to "where do I go to get them".
    """
    seen = []
    for checkin in checkins:
        room = (checkin.room or "").strip()
        if room and room not in seen:
            seen.append(room)
    return ", ".join(seen)


def tell_checked_in(church, household, children, checkins, *, by_user=None) -> int:
    """Tell the household's adults who went in, and where. Caller commits.

    Returns how many people were told. Never raises: a notification that
    cannot be built must not take down the check-in screen with a printer
    queue and a family waiting at the desk.
    """
    adults = _responsible(household, exclude_user=by_user)
    if not adults or not children:
        return 0

    names = _names(children)
    rooms = _rooms(checkins)
    told = 0

    for person in adults:
        try:
            result = notify(
                person=person,
                church_id=church.id,
                category=CATEGORY,
                subject=KIDS["notify_in_subject"].format(names=names),
                body_text=KIDS["notify_in_body"].format(
                    name=person.first_name,
                    names=names,
                    where=KIDS["notify_in_rooms"].format(rooms=rooms) if rooms
                    else KIDS["notify_in_no_room"],
                    church=church.name,
                ),
                push_title=KIDS["notify_in_push_title"].format(church=church.name),
                push_body=(
                    KIDS["notify_in_push_rooms"].format(names=names, rooms=rooms)
                    if rooms else KIDS["notify_in_push"].format(names=names)
                ),
                url=url_for("member.home"),
                # One tag per household per session, so a second check-in half
                # an hour later replaces the first notification rather than
                # stacking beside it.
                tag=f"checkin:{household.id}",
                dedupe_key=(
                    f"checkin:{checkins[0].session_id}:{household.id}:"
                    f"person:{person.id}:"
                    + ",".join(str(c.person_id) for c in checkins)
                ),
            )
        except Exception:  # noqa: BLE001
            current_app.logger.exception(
                "Check-in notification failed for person %s", person.id
            )
            continue
        if result.emailed or result.pushed:
            told += 1

    return told


def tell_checked_out(church, checkins, *, collected_by=None, by_user=None) -> int:
    """Tell the household's adults who was collected, and by whom.

    The one notification in this module that somebody is actually waiting on.
    `collected_by` is free text a volunteer typed, so it goes in as written
    and the wording works without it: "collected" with no name is still the
    fact that matters.

    Caller commits. Takes already-checked-out rows, all from one household.
    """
    present = [c for c in checkins if c.person is not None]
    if not present:
        return 0

    household = present[0].person.household
    adults = _responsible(household, exclude_user=by_user)
    if not adults:
        return 0

    names = _names([c.person for c in present])
    who = (collected_by or "").strip()
    told = 0

    for person in adults:
        try:
            result = notify(
                person=person,
                church_id=church.id,
                category=CATEGORY,
                subject=KIDS["notify_out_subject"].format(names=names),
                body_text=KIDS["notify_out_body"].format(
                    name=person.first_name,
                    names=names,
                    by=KIDS["notify_out_by"].format(who=who) if who
                    else KIDS["notify_out_by_nobody"],
                    church=church.name,
                ),
                push_title=KIDS["notify_out_push_title"].format(church=church.name),
                push_body=(
                    KIDS["notify_out_push_by"].format(names=names, who=who) if who
                    else KIDS["notify_out_push"].format(names=names)
                ),
                url=url_for("member.home"),
                tag=f"checkout:{household.id}",
                dedupe_key=(
                    f"checkout:{present[0].session_id}:{household.id}:"
                    f"person:{person.id}:"
                    + ",".join(str(c.person_id) for c in present)
                ),
            )
        except Exception:  # noqa: BLE001
            current_app.logger.exception(
                "Check-out notification failed for person %s", person.id
            )
            continue
        if result.emailed or result.pushed:
            told += 1

    return told
