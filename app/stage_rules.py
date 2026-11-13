"""When a fact about somebody is enough to move them along the rail.

One rule today: a person their church has marked as both a regular volunteer
and a regular giver is a Disciple, and the system says so rather than waiting
for somebody to notice and press a button.

**Why automatic rather than a suggestion.** Those two boxes are ticked on the
day the thing becomes true, by the person who knows it. A banner asking "shall
we move them?" adds a second decision to a judgement that has already been
made, and the stage then lags the facts by however long it takes somebody to
come back to that profile. The move is written on the timeline with its reason
attached, so it is visible and reversible, which is what makes doing it
automatically safe rather than presumptuous.

**Forward only, and never past Disciple.** Three rules, and all three are
about never surprising a church with a demotion:

  - Somebody already at Disciple or Leader is left alone. A Leader who starts
    giving regularly has not stopped leading.
  - Unticking a box never moves anybody back. The flags say what is true now;
    the stage is a record of how far somebody has come, and a church that
    pauses giving for three months has not undone the last two years.
  - A Visitor goes straight to Disciple, skipping Member and Volunteer. The
    stages between are descriptions of where somebody is, not gates they have
    to pass through, and a Visitor who serves every week and gives every month
    is only a Visitor because nobody updated the record.

**Why its own module.** The rule reads a Person, writes a stage, writes the
timeline and runs the automation hook. Put in the route that happens to set
the flags it would be invisible from the model, invisible from the stage code,
and impossible to call from the next place that learns one of these facts, an
import or a giving sync. Here it is one function with one name.
"""

from __future__ import annotations

from app.content import PEOPLE
from app.models.base import utcnow
from app.models.person_event import KIND_STAGE_CHANGE, PersonEvent
from app.stages import stage_label, stage_order

# The stage the rule moves people to. A code rather than the last stage in
# the list: Leader is above it and must never be the target.
DISCIPLE = "disciple"


def should_advance(person) -> bool:
    """Whether this person's flags say Disciple and their stage does not yet.

    Separate from the move so a screen can ask the question without causing
    anything, and so the condition is testable without a database write.
    """
    if not (person.is_regular_volunteer and person.is_regular_giver):
        return False
    return stage_order(person.stage) < stage_order(DISCIPLE)


def apply(person, *, actor=None) -> str | None:
    """Move them if the flags say so. Returns the stage they came from.

    None when nothing happened, which is the common case: most saves of this
    form change a background check date and nothing else.

    The caller commits. This function is called from inside a request that is
    already building a transaction, and committing here would split one save
    into two, leaving a timeline entry that survives a later rollback.
    """
    from app.automation import on_stage_changed

    if not should_advance(person):
        return None

    previous = person.stage
    person.stage = DISCIPLE
    # The stuck engine measures time in the current stage. Without this a
    # person moved by the rule would read as stuck the moment they arrived.
    person.stage_since = utcnow()

    PersonEvent.record(
        person,
        KIND_STAGE_CHANGE,
        PEOPLE["stage_moved"].format(
            frm=stage_label(previous), to=stage_label(DISCIPLE)
        ),
        # Why, not just what. A stage change with no reason on it is the one
        # somebody finds in six months and cannot account for, and this one
        # was made by a rule rather than by the person whose name is on it.
        detail=PEOPLE["stage_rule_detail"],
        actor=actor,
    )

    # The same hook a manual move runs. A person who arrives at Disciple by
    # rule should enter the same sequences as one moved by hand, or the two
    # routes into a stage mean different things.
    on_stage_changed(person, previous, actor=actor)
    return previous
