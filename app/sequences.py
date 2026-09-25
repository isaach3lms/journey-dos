"""Automated sequences.

Sequences are Python, enrollments are rows. That split is deliberate and it is
the same reasoning as stages and notification categories: a sequence is shape,
and the shape is the same for every church until one asks otherwise. Putting it
in the database would mean a migration to change a sentence, a UI to edit
something nobody edits, and no way to review a change in a diff.

**Two hard stops, and they are the whole design.**

1. A human logs real contact.
2. The person reaches the target stage.

Either one ends the sequence immediately. The first matters most. The entire
promise of this product is that the system notices people and then gets out of
the way when a human steps in; a welcome series that keeps emailing somebody
the pastor already phoned is worse than no automation, because it tells the
person nobody is actually paying attention.

Both stops are checked **at send time**, not only at enrollment. Somebody can
be phoned in the hour between a step becoming due and the worker running, and
the answer that matters is the one at the moment of sending.

**Steps are offsets, not dates.** Day 0, day 2, day 7 from enrollment. A church
that pauses the worker for a week should not get five emails at once when it
starts again, which is why `MAX_CATCHUP_DAYS` exists.
"""

from __future__ import annotations

from dataclasses import dataclass

# A step more overdue than this is skipped rather than sent. If a church turned
# the worker off for a month, the person on day 2 of a welcome series does not
# want the day 2 email in September; they want to be left alone.
MAX_CATCHUP_DAYS = 5


@dataclass(frozen=True)
class Step:
    day: int          # days after enrollment
    category: str     # a notification category from app/categories.py
    subject: str
    body: str


@dataclass(frozen=True)
class Sequence:
    code: str
    name: str
    description: str
    # The stage whose arrival enrols somebody.
    trigger_stage: str
    # Reaching this stage is the second hard stop. Getting where the sequence
    # was pushing you is the definition of done.
    target_stage: str
    steps: tuple[Step, ...]

    @property
    def length_days(self) -> int:
        return max((step.day for step in self.steps), default=0)

    def step_at(self, index: int | None) -> Step | None:
        # None is tolerated rather than raising: an enrollment read before it
        # is flushed has no index yet, and a sequence engine that throws on
        # that would take down a cron job for a reason nobody could see.
        if index is None:
            return None
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None


WELCOME = Sequence(
    code="first_visit_welcome",
    name="First visit welcome",
    description=(
        "Three short notes over a week after somebody's first Sunday. Stops "
        "the moment anyone actually talks to them."
    ),
    trigger_stage="visitor",
    target_stage="attender",
    steps=(
        Step(
            day=0,
            category="welcome",
            subject="Glad you were with us, {first_name}",
            body=(
                "Hello {first_name},\n\n"
                "Thanks for being at {church} on Sunday. We are glad you came.\n\n"
                "No next step needed right now. If you have a question about "
                "anything, just reply to this and a real person will read it.\n\n"
                "{church}"
            ),
        ),
        Step(
            day=3,
            category="welcome",
            subject="What a Sunday looks like at {church}",
            body=(
                "Hello {first_name},\n\n"
                "In case it is useful: services run about an hour, kids check "
                "in at the desk in the lobby, and there is coffee before and "
                "after.\n\n"
                "Nothing to sign up for. We just did not want you guessing.\n\n"
                "{church}"
            ),
        ),
        Step(
            day=7,
            category="next_step",
            subject="Would you like to meet a few people?",
            body=(
                "Hello {first_name},\n\n"
                "If you are thinking about coming back, the easiest way in is "
                "our Next Steps lunch. It is short, there is food, and nobody "
                "asks you to do anything.\n\n"
                "If now is not the time, that is genuinely fine.\n\n"
                "{church}"
            ),
        ),
    ),
)

# The code is the one in-flight rows already carry, so it stays what it is.
# The stage it fires on moved when Guest was folded into Visitor and Attender.
GUEST_FOLLOW_UP = Sequence(
    code="guest_follow_up",
    name="Attender follow up",
    description=(
        "Two notes for somebody who is here most Sundays but has not taken a "
        "next step. Stops as soon as they do, or as soon as a person calls."
    ),
    trigger_stage="attender",
    target_stage="member",
    steps=(
        Step(
            day=5,
            category="next_step",
            subject="Good to see you again, {first_name}",
            body=(
                "Hello {first_name},\n\n"
                "You have been around a few times now and we are glad.\n\n"
                "When you are ready, the next thing most people do is join a "
                "group. No pressure and no deadline.\n\n"
                "{church}"
            ),
        ),
        Step(
            day=21,
            category="next_step",
            subject="Anything we can help with?",
            body=(
                "Hello {first_name},\n\n"
                "This is the last note like this you will get from us.\n\n"
                "If there is something you are carrying, or you just want to "
                "know how to get connected, reply and somebody will get back "
                "to you.\n\n"
                "{church}"
            ),
        ),
    ),
)

SEQUENCES: tuple[Sequence, ...] = (WELCOME, GUEST_FOLLOW_UP)

SEQUENCE_BY_CODE: dict[str, Sequence] = {s.code: s for s in SEQUENCES}
SEQUENCE_CODES: tuple[str, ...] = tuple(s.code for s in SEQUENCES)


def sequences_for(church=None) -> tuple[Sequence, ...]:
    """Every read goes through here.

    The argument is unused today and deliberately present: it is the seam where
    per-church sequences arrive without touching a single call site, exactly as
    `stages_for` is for stages.
    """
    return SEQUENCES


def triggered_by(stage_code: str) -> list[Sequence]:
    return [s for s in SEQUENCES if s.trigger_stage == stage_code]


def get(code: str) -> Sequence | None:
    return SEQUENCE_BY_CODE.get(code)


def render(template: str, person, church) -> str:
    """Fill a step template.

    Only the fields a step is allowed to use. A template is written by us and
    checked into the repo, so this is about keeping the surface small rather
    than defending against the author.
    """
    return template.format(
        first_name=person.first_name,
        last_name=person.last_name,
        full_name=person.full_name,
        church=church.name,
    )
