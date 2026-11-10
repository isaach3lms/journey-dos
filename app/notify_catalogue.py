"""Every notification this system sends, written down where staff can read it.

The reason this exists is the way the bug was found. Somebody noticed that
"some come through but not all", which is the only symptom available: a
notification that does not arrive looks exactly like one nobody sent, and
there was no screen anywhere that said what was supposed to happen. Finding
the four broken paths meant reading the source.

So this is the list, rendered in Settings. It is not a decorative page. It is
what lets the next gap be reported as "scheduled to serve says email and push
but I only got the email", which is a bug report somebody can act on, instead
of "notifications are flaky", which is not.

**It is checked against the code, not trusted.** A hand-kept list drifts from
reality within a month and then actively misleads, which is worse than no
list. `tests/test_notify_catalogue.py` asserts every category named here
exists, that every non-transactional category a member can switch off appears
here at least once so the preferences screen and this page cannot disagree,
and that nothing claims to push without a push title in the copy behind it.
"""

from __future__ import annotations

from dataclasses import dataclass

EMAIL = "email"
PUSH = "push"
BOTH = "both"

CHANNEL_LABELS = {
    EMAIL: "Email only",
    PUSH: "Notification only",
    BOTH: "Email and notification",
}


@dataclass(frozen=True)
class Event:
    """One thing that happens, and who hears about it."""

    what: str
    who: str
    category: str
    channels: str
    note: str = ""

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS[self.channels]

    @property
    def pushes(self) -> bool:
        return self.channels in (PUSH, BOTH)


@dataclass(frozen=True)
class Group:
    """A heading on the screen, and the events under it."""

    title: str
    events: tuple


# ---------------------------------------------------------------------------
# The catalogue.
#
# Ordered the way somebody auditing it would ask: the things members do that
# staff need to know about first, because those are the ones with a person
# waiting at the other end.
# ---------------------------------------------------------------------------

GROUPS = (
    Group("Someone needs a person", (
        Event(
            "Somebody fills in the pastoral support form",
            "The care team addresses in Settings, or every staff account",
            "pastoral", BOTH,
            "What they wrote is never in the email or the notification, only "
            "a link to their record.",
        ),
        Event(
            "A guest fills in a connect card",
            "The connect card addresses in Settings, or every staff account",
            "guest_card", BOTH,
        ),
        Event(
            "Somebody reports or blocks a message in chat",
            "Every staff account",
            "moderation", BOTH,
            "The words of the reported message are not in it. They are on the "
            "reports screen, where removing one removes it everywhere.",
        ),
        Event(
            "Somebody asks about baptism, volunteering or another next step",
            "Every staff account",
            "signup", BOTH,
            "What they wrote is behind the link rather than in the message, "
            "the same way a pastoral request works.",
        ),
        Event(
            "Somebody asks for an account",
            "Whoever sets accounts up, from Settings",
            "guest_card", EMAIL,
            "Email only because this goes to whoever administers the app, who "
            "is usually not on the church staff and has no account here to "
            "notify.",
        ),
    )),
    Group("Kids and youth", (
        Event(
            "A child or youth is checked in",
            "The adults in their household",
            "kids_checkin", BOTH,
            "The pickup code is never in it. That stays on the printed tag.",
        ),
        Event(
            "A child or youth is collected",
            "The adults in their household",
            "kids_checkin", BOTH,
            "Carries who collected them, as the volunteer wrote it down.",
        ),
        Event(
            "A family asks for their check-in code",
            "The person who asked",
            "kids_checkin", EMAIL,
            "They are standing at the kiosk waiting for an email, which is "
            "the whole point of the screen.",
        ),
    )),
    Group("Serving", (
        Event(
            "Somebody is put on a published plan",
            "The person added",
            "group", BOTH,
            "A draft sends nothing. Publishing is what releases the asks.",
        ),
        Event(
            "A plan is published",
            "Everybody on it who has not answered yet",
            "group", BOTH,
        ),
        Event(
            "Ask the rest again",
            "Everybody on the plan who has not answered yet",
            "group", BOTH,
        ),
        Event(
            "Send the plan",
            "Everybody on the plan",
            "group", BOTH,
            "The running order is in the email. The notification says the "
            "plan is out and which slot they are in.",
        ),
        Event(
            "Somebody is taken off a plan",
            "The person removed, if they had already been asked",
            "group", BOTH,
            "Nobody who was never asked hears this, so moving people around a "
            "draft stays silent.",
        ),
    )),
    Group("Groups and chat", (
        Event(
            "Somebody posts in a room",
            "Everybody else in that room",
            "chat", BOTH,
            "One notification per room per half hour, so a back and forth of "
            "nine messages is one badge rather than nine.",
        ),
        Event(
            "A church-wide announcement is posted",
            "Everybody",
            "announcement", PUSH,
            "The notification always goes. The email goes only when the "
            "person posting ticks “also email”.",
        ),
        Event(
            "An announcement is posted with “also email” ticked",
            "Everybody",
            "announcement", BOTH,
        ),
        Event(
            "Send email, from the Messages screen",
            "The audience chosen on that screen",
            "announcement", BOTH,
            "Staff can send it as email only, for a letter too long to make a "
            "sensible notification.",
        ),
    )),
    Group("Accounts and the rest", (
        Event(
            "Password reset, email confirmation, a new account invitation",
            "The person signing in",
            "account", EMAIL,
            "Nobody has the app yet, which is the entire situation these are "
            "sent in.",
        ),
        Event(
            "A self-registered person is approved",
            "The person approved",
            "account", EMAIL,
        ),
        Event(
            "A welcome or next-step sequence step",
            "The person enrolled",
            "welcome", EMAIL,
            "These are letters, a paragraph or more each. A welcome series "
            "that also pushed four times in a fortnight is why people turn "
            "notifications off.",
        ),
        Event(
            "A staff member emails one person from their record",
            "That person",
            "next_step", EMAIL,
            "A staff member composing an email, which is an email.",
        ),
    )),
)


def every_event() -> list:
    """Flat, for the tests and for counting."""
    return [event for group in GROUPS for event in group.events]


def categories_used() -> set:
    return {event.category for event in every_event()}
