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
    # How long someone can sit here before it is worth a look. Increment 3's
    # stuck engine reads this; increment 2 only displays the elapsed time.
    expected_days: int


STAGES: tuple[Stage, ...] = (
    Stage("visitor", "Visitor", 0,
          "Has been here once. We may not know their name yet.", 21),
    Stage("guest", "Guest", 1,
          "Coming back. Has given us a way to contact them.", 42),
    Stage("attender", "Attender", 2,
          "Here most Sundays. Not yet committed to anything else.", 90),
    Stage("member", "Member", 3,
          "Has committed to this church publicly.", 365),
    Stage("volunteer", "Volunteer", 4,
          "Serving on a team.", 365),
    Stage("disciple", "Disciple", 5,
          "In a group and growing on purpose.", 365),
    Stage("leader", "Leader", 6,
          "Leading others. Reproducing what they were given.", 730),
)

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
