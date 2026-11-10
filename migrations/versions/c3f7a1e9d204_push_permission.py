"""What each phone answered when iOS asked about notifications.

With a provider holding the devices there are no subscription rows to count,
so without this a church has no way to tell "nobody has notifications on"
from "everybody does and none are arriving". Nullable and unbackfilled: null
is the honest value for everybody who has not been asked yet, which on the
day this ships is everybody.

Revision ID: c3f7a1e9d204
Revises: b58d1e73f2a9
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f7a1e9d204"
down_revision = "b58d1e73f2a9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("push_permission", sa.String(length=10),
                                   nullable=True))
        batch.add_column(sa.Column("push_asked_at", sa.DateTime(timezone=True),
                                   nullable=True))
        batch.create_index("ix_user_push_permission", ["push_permission"])


def downgrade():
    with op.batch_alter_table("user") as batch:
        batch.drop_index("ix_user_push_permission")
        batch.drop_column("push_asked_at")
        batch.drop_column("push_permission")
