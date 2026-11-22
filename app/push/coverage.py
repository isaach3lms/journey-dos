"""Whether a login will actually receive what the church sends.

**The failure this exists to make visible.** Everything the DOS sends is
addressed to a roster record. `broadcast.resolve` builds its audience from
`Person` rows, `notify` takes a person, and `PushQueueItem.person_id` is a
non-null foreign key, so a login with no roster record attached cannot be
queued at all. It is not skipped, filtered or logged. There is nothing to
address, so nothing happens.

That is how a church ends up with staff accounts that quietly receive none of
the announcements the whole congregation gets. The account works. Sign-in
works. Notifications are switched on and the phone has granted permission.
The account simply is not in the audience, and no screen in the product said
so, because every screen was built around the roster rather than around the
logins.

**Why a reason rather than a boolean.** "This account will not get
notifications" sends somebody to check their phone, which is the one thing
that is fine. The useful sentence names the step that failed, because each one
has a different fix: link a record, approve it, add an address, or respect
that they opted out. A boolean would have them debugging the notification
system again, which is where the last two days went.

**What this does not check.** Whether a device is enrolled. That lives on the
phone and belongs to the member, and a church cannot fix it from a settings
screen. This answers the half the church controls: whether the system would
even try.
"""

from __future__ import annotations

from app.broadcast import CATEGORY

# Why a login is not in the audience. Ordered by what has to be fixed first,
# because the later checks cannot run until the earlier ones pass: there is no
# approval to read without a record, and no preference to honour without one.
NO_RECORD = "no_record"
ARCHIVED = "archived"
UNAPPROVED = "unapproved"
CHILD = "child"
NO_EMAIL = "no_email"
OPTED_OUT = "opted_out"

# In the audience. Not a promise anything arrives: the phone still has to have
# notifications switched on, which is the member's to decide and nobody
# else's to see from here.
REACHED = "reached"

# The ones a church fixes on the accounts screen, as opposed to the one it
# must not. Opting out is a decision the member made and the screen offers no
# button to undo it.
FIXABLE = (NO_RECORD, ARCHIVED, UNAPPROVED, NO_EMAIL)


def audience_state(user, category: str = CATEGORY) -> str:
    """Why this login is or is not in a church-wide send.

    Mirrors `broadcast._eligible` deliberately, in the same order. The two
    have to agree, and the way they come apart is somebody adding a condition
    to one of them: a screen that says an account is covered while the send
    skips it is worse than no screen, because it ends the investigation.

    Children are checked after approval rather than first, because a record
    flagged as a child attached to a staff login is a mis-linked record, and
    naming that is more use than naming the flag.
    """
    person = getattr(user, "person", None)
    if person is None:
        return NO_RECORD
    if person.is_archived:
        return ARCHIVED
    if not person.is_approved:
        return UNAPPROVED
    if person.is_child:
        return CHILD
    if not (person.email or "").strip():
        return NO_EMAIL
    if not person.allows(category):
        return OPTED_OUT
    return REACHED


def is_reached(user, category: str = CATEGORY) -> bool:
    """The boolean, for callers that only need to count."""
    return audience_state(user, category) == REACHED


def unreached(users, category: str = CATEGORY) -> list:
    """The accounts a church would want to know about, worst first.

    Opted-out accounts are included, because a staff member who cannot work
    out why they get nothing deserves to see that they turned it off
    themselves, but they sort last: nothing is broken there.
    """
    order = {state: index for index, state in enumerate(
        (NO_RECORD, ARCHIVED, UNAPPROVED, CHILD, NO_EMAIL, OPTED_OUT))}

    out = []
    for user in users:
        state = audience_state(user, category)
        if state != REACHED:
            out.append((order.get(state, 99), user, state))

    out.sort(key=lambda row: (row[0], (row[1].name or "").lower()))
    return [(user, state) for _, user, state in out]
