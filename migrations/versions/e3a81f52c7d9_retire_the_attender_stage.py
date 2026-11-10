"""Retire the Attender stage.

The second stage to go for the same reason as the first. Guest sat between
Visitor and Attender and nobody filed anybody into it; Attender sat between
Visitor and Member and did the same. "Here most Sundays" is something a church
knows about somebody months after it becomes true, so the column stayed at zero
while the people it described sat in Visitor.

Anybody standing on it moves to Visitor, not Member, and the direction matters.
Visitor claims less. Filing somebody as a Member means recording a public
commitment they may never have made, and a church reads that number when it
decides things. Moving them down to Visitor understates somebody who may well
be a regular, but Visitor is transitional with a 21 day expectation, so they
surface on the stuck list within three weeks and a human looks at them. One
error gets corrected by the system working normally; the other does not get
corrected at all.

`stage_since` is left alone, exactly as it was for Guest. Somebody who sat in
Attender for two months keeps that clock and flags immediately, which is the
right answer for somebody nobody has moved since June.

The check constraint is replaced by hand. Alembic does not diff check
constraints, so a rebuilt one is the only way the database stops accepting a
stage the application no longer has.

Revision ID: e3a81f52c7d9
Revises: c92b57ea31d4
"""

from alembic import op

revision = "e3a81f52c7d9"
down_revision = "c92b57ea31d4"
branch_labels = None
depends_on = None

# Written out rather than imported from app.stages: a migration has to keep
# meaning what it meant on the day it ran, and that module will change again.
# This is the same reason the Guest migration spelled its lists out, and the
# reason this one can be read years from now without archaeology.
AFTER = ("visitor", "member", "volunteer", "disciple", "leader")
BEFORE = ("visitor", "attender", "member", "volunteer", "disciple", "leader")


def _in_list(codes):
    return "stage IN (" + ", ".join(f"'{code}'" for code in codes) + ")"


def _swap_constraint(codes):
    """Drop by the name in the database, create by the name the model makes.

    The naming convention is ck_%(table_name)s_%(constraint_name)s applied to a
    constraint the model already calls "ck_person_stage", which is where the
    doubled ck_person_ck_person_stage comes from. Passing the name WITHOUT
    op.f() lets the convention double it again, so the new constraint lands on
    the same name as the old one. With op.f() it would be created as a plain
    ck_person_stage, and the next migration to touch it would go looking for a
    constraint that is not there. This is lifted verbatim from c7a3f1d94b52,
    which is the migration that discovered it.
    """
    with op.batch_alter_table("person", schema=None) as batch:
        # op.f on the drop: that name is final, and the convention would
        # otherwise double it a second time.
        batch.drop_constraint(op.f("ck_person_ck_person_stage"), type_="check")
        batch.create_check_constraint("ck_person_stage", _in_list(codes))


def upgrade():
    # Before the constraint, not after. A constraint applied while rows still
    # violate it fails the deploy, and the failure would be the whole release
    # rather than this one table.
    op.execute("UPDATE person SET stage = 'visitor' WHERE stage = 'attender'")
    _swap_constraint(AFTER)

    # The series that fired on Attender can no longer start, so its unfinished
    # enrollments are closed here rather than left for the worker. The worker
    # would close them too, one run at a time, as REASON_FINISHED; doing it in
    # one statement means the automation screen is honest the moment this
    # deploys instead of a few hours later.
    # `next_due_at` is cleared along with the rest, because the index the
    # worker scans is (church_id, status, next_due_at) and a completed row
    # with a date still on it sits in that index forever doing nothing.
    op.execute(
        "UPDATE sequence_enrollment"
        " SET status = 'completed', end_reason = 'finished',"
        "     ended_at = CURRENT_TIMESTAMP, next_due_at = NULL"
        " WHERE sequence_code = 'guest_follow_up' AND status = 'active'"
    )


def downgrade():
    # Nobody moves back. The people who were moved cannot be told apart from
    # people who were always Visitors, which was true of Guest and is true
    # here. The stage simply becomes legal again.
    _swap_constraint(BEFORE)

    # The enrollments are not reopened either. Reopening one would resume a
    # series at whatever step it had reached, which for somebody closed weeks
    # ago means an email that reads as the middle of a conversation nobody
    # remembers having.
