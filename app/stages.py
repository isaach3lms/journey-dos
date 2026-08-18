"""The discipleship stages, in order.

Stages are a Python data structure, not rows, for the same reason automated
sequences are: they are shape, not content. A stage's meaning is defined by the
code that reads it, so storing the list in the database would put half the
definition in one place and half in another without buying anything.

`Person.stage` holds the code. A database check constraint rejects anything not
in `STAGE_CODES`, so a typo in an import file fails at write time rather than
producing a person who belongs to a stage that does not exist.

**When this becomes a table.** Customizable stages are part of the pitch
against Planning Center, and the moment one church wants six stages with
different names, this moves to a per-church table with `Church` holding the
default set. That change is contained: every read already goes through
`stages_for(church)`, which today ignores its argument. Nothing else in the
codebase indexes into `STAGES` directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    code: str
    label: str
    order: int
    # What a person at this stage is, in one line a pastor would actually say.
    meaning: str

    # Is this a stage people are supposed to pass through, or one they are
    # supposed to arrive at?
    #
    # This distinction is the whole difference between a useful flag and an
    # ignored one. Running the first version of the stuck engine against
    # Journey's roster flagged 39 of 54 people, because a Member of three
    # years read as "overdue" against a 365 day expectation. But a Member of
    # three years is not stuck. They are exactly where the church wants them.
    # Only Visitor, Guest, and Attender are places someone should be moving
    # out of, so only those three can produce a stage flag.
    is_transitional: bool

    # How long someone can sit in a transitional stage before it is worth a
    # look. Meaningless, and therefore None, on a stage people arrive at.
    expected_days: int | None


STAGES: tuple[Stage, ...] = (
    # Transitional. People should be moving out of these.
    Stage("visitor", "Visitor", 0,
          "Has been here once. We may not know their name yet.",
          is_transitional=True, expected_days=21),
    Stage("guest", "Guest", 1,
          "Coming back. Has given us a way to contact them.",
          is_transitional=True, expected_days=42),
    Stage("attender", "Attender", 2,
          "Here most Sundays. Not yet committed to anything else.",
          is_transitional=True, expected_days=90),

    # Destinations. Staying here for years is the point, not a problem.
    Stage("member", "Member", 3,
          "Has committed to this church publicly.",
          is_transitional=False, expected_days=None),
    Stage("volunteer", "Volunteer", 4,
          "Serving on a team.",
          is_transitional=False, expected_days=None),
    Stage("disciple", "Disciple", 5,
          "In a group and growing on purpose.",
          is_transitional=False, expected_days=None),
    Stage("leader", "Leader", 6,
          "Leading others. Reproducing what they were given.",
          is_transitional=False, expected_days=None),
)

TRANSITIONAL_STAGES: tuple[Stage, ...] = tuple(s for s in STAGES if s.is_transitional)

STAGE_CODES: tuple[str, ...] = tuple(stage.code for stage in STAGES)
STAGE_BY_CODE: dict[str, Stage] = {stage.code: stage for stage in STAGES}

FIRST_STAGE = STAGES[0].code


def stages_for(church=None) -> tuple[Stage, ...]:
    """Every read goes through here.

    The argument is unused today and deliberately present: it is the seam
    where per-church stages arrive without touching a single call site.
    """
    return STAGES


def stage_label(code: str) -> str:
    stage = STAGE_BY_CODE.get(code)
    return stage.label if stage else code.title()


def stage_order(code: str) -> int:
    stage = STAGE_BY_CODE.get(code)
    return stage.order if stage else -1


def next_stage(code: str) -> Stage | None:
    """The stage after this one, or None at the end of the rail."""
    order = stage_order(code)
    if order < 0 or order >= len(STAGES) - 1:
        return None
    return STAGES[order + 1]


def is_forward(from_code: str, to_code: str) -> bool:
    """True when a move goes up the rail rather than down or sideways."""
    return stage_order(to_code) > stage_order(from_code)


# ---------------------------------------------------------------------------
# Increment 3: what "stuck" means
# ---------------------------------------------------------------------------

# How long a church can go without talking to someone before that itself is
# the problem, regardless of stage. Separate from `expected_days` on purpose:
# a Member who has been a Member for two years is not stuck, but a Member
# nobody has spoken to since March might be.
CONTACT_WINDOW_DAYS = 21

# The step that usually comes next, by stage. A recommendation, not a rule:
# staff assign whatever actually fits, and this is what the screen offers
# first so the common case is one click instead of a blank field.
NEXT_STEP_BY_STAGE = {
    "visitor": "Send a personal thank you for visiting",
    "guest": "Invite to the next Next Steps lunch",
    "attender": "Invite into a group",
    "member": "Ask them to serve on a team",
    "volunteer": "Invite into a group, or to lead one",
    "disciple": "Ask them to disciple someone else",
    "leader": "Check in on who they are raising up",
}


def recommended_next_step(stage_code: str) -> str | None:
    return NEXT_STEP_BY_STAGE.get(stage_code)


def expected_days(stage_code: str) -> int | None:
    stage = STAGE_BY_CODE.get(stage_code)
    return stage.expected_days if stage else None
