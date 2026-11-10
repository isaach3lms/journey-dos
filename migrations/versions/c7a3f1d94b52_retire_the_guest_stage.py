"""Retire the Guest stage.

Journey asked for it: seven stages with Guest sitting between Visitor and
Attender was a distinction staff were not making in practice, and the column
sat at zero.

Anybody standing on it moves to Visitor rather than Attender. Visitor claims
less: "has been here once" is recoverable by a phone call, while filing
somebody as "here most Sundays" when nobody knows that is the kind of wrong
number a church makes decisions on. Their stage_since is left alone, so a
person who sat in Guest for two months keeps that clock and may flag as
stuck, which is the correct answer for somebody nobody has called since June.

The check constraint is replaced by hand. Alembic does not diff check
constraints, so a rebuilt one is the only way the database stops accepting a
stage the application no longer has.

Revision ID: c7a3f1d94b52
Revises: 1eb45d68a46c
"""

from alembic import op
import sqlalchemy as sa

revision = "c7a3f1d94b52"
down_revision = "1eb45d68a46c"
branch_labels = None
depends_on = None

# Written out rather than imported from app.stages: a migration has to keep
# meaning what it meant on the day it ran, and that module will change again.
AFTER = ("visitor", "attender", "member", "volunteer", "disciple", "leader")
BEFORE = ("visitor", "guest", "attender", "member", "volunteer", "disciple", "leader")


def _in_list(codes):
    return "stage IN (" + ", ".join(f"'{code}'" for code in codes) + ")"


def _swap_constraint(codes):
    """Drop by the name in the database, create by the name the model makes.

    The convention is ck_%(table_name)s_%(constraint_name)s applied to a
    constraint the model already calls "ck_person_stage", which is where the
    doubled ck_person_ck_person_stage comes from. Passing the name WITHOUT
    op.f() lets the convention double it again, so the new constraint lands
    on the same name as the old one. With op.f() it would be created as a
    plain ck_person_stage, and the next migration to touch it would go
    looking for a constraint that is not there.
    """
    with op.batch_alter_table("person", schema=None) as batch:
        # op.f on the drop: that name is final, and the convention would
        # otherwise double it a second time.
        batch.drop_constraint(op.f("ck_person_ck_person_stage"), type_="check")
        batch.create_check_constraint("ck_person_stage", _in_list(codes))


def upgrade():
    op.execute("UPDATE person SET stage = 'visitor' WHERE stage = 'guest'")
    _swap_constraint(AFTER)


def downgrade():
    # The people who were moved cannot be told apart from people who were
    # always Visitors, so nobody moves back. The stage becomes legal again.
    _swap_constraint(BEFORE)
