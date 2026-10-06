"""Two things staff mark on a person that the system cannot work out.

`is_regular_giver` is what staff know rather than what the gift rows add up
to. Giving is mirrored from an outside platform and misses anybody who gives
by bank transfer or in an envelope, so nothing derives this.

`background_check_ordered_on` is a date rather than a boolean, and is still
one checkbox on screen. A tick with no date cannot tell a check ordered on
Friday from one that has been sitting since March, which is the only thing
anybody wants to know about one that has not come back.

Revision ID: b6d04f93c71a
Revises: a4e71d28b093
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d04f93c71a"
down_revision = "a4e71d28b093"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("person") as batch:
        batch.add_column(sa.Column("is_regular_giver", sa.Boolean(),
                                   nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("background_check_ordered_on", sa.Date(),
                                   nullable=True))


def downgrade():
    with op.batch_alter_table("person") as batch:
        batch.drop_column("background_check_ordered_on")
        batch.drop_column("is_regular_giver")
