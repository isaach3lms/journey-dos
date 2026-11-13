"""A person can be marked a regular volunteer.

One boolean, beside `is_regular_giver`, and the pair of them is what
`app/stage_rules.py` reads to move somebody to Disciple.

NOT NULL with a server default of false, which is what makes this safe to run
against a live table: existing rows get false without a backfill pass, and a
row written by code deployed a moment before this migration still satisfies
the constraint.

Revision ID: e7c1a4f30b28
Revises: d5e3b7a91c42
"""

import sqlalchemy as sa
from alembic import op

revision = "e7c1a4f30b28"
down_revision = "d5e3b7a91c42"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_regular_volunteer",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_column("is_regular_volunteer")
