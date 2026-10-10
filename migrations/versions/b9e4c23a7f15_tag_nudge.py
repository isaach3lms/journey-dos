"""Moving the printed name tag, in millimetres.

A Brother QL does not centre what it prints on the page it is handed, so a
tag can come out shifted with letters missing off one side. The layout change
that went with this leaves enough clear space to absorb the usual offset;
these two columns are for the printer that is worse than usual.

NOT NULL with a server default of zero, so every existing church gets a
centred tag without a data migration and without the property having to treat
null as a third state.

Revision ID: b9e4c23a7f15
Revises: a7d3f61c90b4
"""

import sqlalchemy as sa
from alembic import op

revision = "b9e4c23a7f15"
down_revision = "a7d3f61c90b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("church", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "kids_tag_nudge_x", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column(
            "kids_tag_nudge_y", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("church", schema=None) as batch_op:
        batch_op.drop_column("kids_tag_nudge_y")
        batch_op.drop_column("kids_tag_nudge_x")
