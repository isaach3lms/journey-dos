"""Volunteer invites: when somebody was asked, and the token they answer with.

Two columns on service_assignment. Nothing is backfilled: an assignment made
before this existed was never *invited*, and stamping one would tell a leader
invites went out that never did.

Revision ID: a82e5fd13c74
Revises: d41f9c2b7e06
"""

from alembic import op
import sqlalchemy as sa


revision = "a82e5fd13c74"
down_revision = "d41f9c2b7e06"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("service_assignment", schema=None) as batch:
        batch.add_column(sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("respond_token", sa.String(length=64), nullable=True))
        batch.create_index(
            "ix_service_assignment_respond_token", ["respond_token"], unique=False
        )


def downgrade():
    with op.batch_alter_table("service_assignment", schema=None) as batch:
        batch.drop_index("ix_service_assignment_respond_token")
        batch.drop_column("respond_token")
        batch.drop_column("invited_at")
