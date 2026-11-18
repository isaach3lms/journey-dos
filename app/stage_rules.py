"""When a fact about somebody is enough to move them along the rail.

Two rules, read strongest first:

  - A person marked as both a regular volunteer and a regular giver is a
    Disciple.
  - A person marked as a regular volunteer is a Volunteer.

The system says so rather than waiting for somebody to notice and press a
button.

**Strongest first, and it has to be.** Somebody with both boxes ticked
satisfies the volunteer rule too. Checking that one first would park them at
Volunteer and leave the Disciple rule to fire on some later save that happens
to touch the record, or never. `target_for` returns one stage, the furthest
the facts justify, so the order of the checks inside it is the whole rule.

**Why automatic rather than a suggestion.** Those two boxes are ticked on the
day the thing becomes true, by the person who knows it. A banner asking "shall
we move them?" adds a second decision to a judgement that has already been
made, and the stage then lags the facts by however long it takes somebody to
come back to that profile. The move is written on the timeline with its reason
attached, so it is visible and reversible, which is what makes doing it
automatically safe rather than presumptuous.

**Forward only, and never past Disciple.** Three rules, and all three are
about never surprising a church with a demotion:

  - Somebody already at or beyond the stage a rule names is left alone. A
    Leader who starts giving regularly has not stopped leading, and a
    Disciple who is ticked as a regular volunteer has not gone backwards to
    Volunteer. This is the one that makes the two rules safe to compose:
    the weaker rule can never undo the stronger one's work.
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

# The stages the rules move people to. Codes rather than positions in the
# list: Leader is above both and must never be a target.
DISCIPLE = "disciple"
VOLUNTEER = "volunteer"


def target_for(person) -> str | None:
    """The furthest stage this person's flags justify, or None.

    Strongest first. Somebody with both boxes ticked also satisfies the
    volunteer rule, so checking that one first would park them at Volunteer
    and leave Disciple to some later save that happens to touch the record.

    This says nothing about where they are now. `should_advance` is what
    compares the two, because "what do the facts say" and "is that a move"
    are different questions and the hint on the profile screen asks the
    first one on its own.
    """
    if person.is_regular_volunteer and person.is_regular_giver:
        return DISCIPLE
    if person.is_regular_volunteer:
        return VOLUNTEER
    return None


def could_still_move(person) -> bool:
    """Whether ticking a box on this form could move them from here.

    What the hint above the boxes is asking. Not the same question as
    `should_advance`: that one is about the flags as they stand, this one is
    about what a tick would do next, so it is true for somebody with nothing
    ticked and false for somebody already at Disciple or beyond.

    Written as "is the strongest rule's stage still ahead of them", which
    stays correct when a third rule is added as long as DISCIPLE remains the
    furthest any rule reaches.
    """
    return stage_order(person.stage) < stage_order(DISCIPLE)


def should_advance(person) -> bool:
    """Whether the flags name a stage further along than the current one.

    Separate from the move so a screen can ask the question without causing
    anything, and so the condition is testable without a database write.
    """
    target = target_for(person)
    if target is None:
        return False
    return stage_order(person.stage) < stage_order(target)


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

    target = target_for(person)
    previous = person.stage
    person.stage = target
    # The stuck engine measures time in the current stage. Without this a
    # person moved by the rule would read as stuck the moment they arrived.
    person.stage_since = utcnow()

    PersonEvent.record(
        person,
        KIND_STAGE_CHANGE,
        PEOPLE["stage_moved"].format(
            frm=stage_label(previous), to=stage_label(target)
        ),
        # Why, not just what. A stage change with no reason on it is the one
        # somebody finds in six months and cannot account for, and this one
        # was made by a rule rather than by the person whose name is on it.
        #
        # Which rule, too. Two rules write this event now, and "moved
        # automatically" without saying which box did it is the note that
        # sends somebody hunting through a changelog.
        detail=(
            PEOPLE["stage_rule_detail"] if target == DISCIPLE
            else PEOPLE["stage_rule_detail_volunteer"]
        ),
        actor=actor,
        # Counted the same as a move somebody made by hand. A rule-driven
        # move that did not appear in the totals would make the metrics
        # disagree with the rail they are measuring.
        from_stage=previous,
        to_stage=target,
    )

    # The same hook a manual move runs. A person who arrives at a stage by
    # rule should enter the same sequences as one moved by hand, or the two
    # routes into a stage mean different things.
    on_stage_changed(person, previous, actor=actor)
    return previous
