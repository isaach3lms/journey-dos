"""Splitting youth out of kids.

One nullable column, because the rule is an age and the age is already on the
record. Null means "work it out from the birthday", so nobody has to touch
the existing rows and a child becomes a youth on their thirteenth birthday
without anybody doing anything.

Set, it wins, which is what makes the rule workable on a real roster: a
birthday is optional here and plenty of children have none, and a church will
sometimes want a twelve year old with the youth or a thirteen year old kept
with the kids.

Revision ID: c92b57ea31d4
Revises: b6d04f93c71a
"""

import sqlalchemy as sa
from alembic import op

revision = "c92b57ea31d4"
down_revision = "b6d04f93c71a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("person") as batch:
        batch.add_column(sa.Column("youth_override", sa.Boolean(), nullable=True))

    # No backfill, deliberately. Every child who is already thirteen becomes a
    # youth the moment this deploys, because the rule reads their birthday.
    # Writing a value into the column instead would freeze each of them where
    # they are today and stop the next birthday from moving anybody.


def downgrade():
    with op.batch_alter_table("person") as batch:
        batch.drop_column("youth_override")
