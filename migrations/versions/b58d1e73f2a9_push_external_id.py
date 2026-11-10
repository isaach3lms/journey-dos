"""Who a login is, to the push provider.

OneSignal addresses people by an external id the app sets when somebody signs
in, so the server stores no devices at all. This is that id.

Deliberately not backfilled. It is minted the first time a signed-in person
loads a page, which covers everybody who was already signed in when this
shipped without a forced sign-out, and leaves the column empty for the several
hundred roster records that have no login and never will.

Revision ID: b58d1e73f2a9
Revises: e47a2c90b6f3
"""

from alembic import op
import sqlalchemy as sa


revision = "b58d1e73f2a9"
down_revision = "e47a2c90b6f3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch:
        batch.add_column(sa.Column("push_external_id", sa.String(length=64), nullable=True))
        batch.create_index(
            batch.f("ix_user_push_external_id"), ["push_external_id"], unique=False
        )


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch:
        batch.drop_index(batch.f("ix_user_push_external_id"))
        batch.drop_column("push_external_id")
