"""Somebody asking to be set up with a login.

Replaces "Create an account" on the sign-in page, which handed a login to
anybody who typed an address. A row rather than an email alone, because an
email is where a request goes to die: the person who received it is away, and
the family who asked hears nothing.

Revision ID: f1a93c6e5d28
Revises: e8c4a07f2b16
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a93c6e5d28"
down_revision = "e8c4a07f2b16"
branch_labels = None
depends_on = None

# Spelled out rather than imported, like every constraint in this folder: a
# migration keeps meaning what it meant on the day it ran.
STATUSES = "status IN ('open', 'done', 'declined')"


def upgrade():
    op.create_table(
        "account_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("last_name", sa.String(length=80), nullable=False,
                  server_default=""),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("household", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="open"),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by_name", sa.String(length=120), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(STATUSES, name="ck_account_request_status"),
        sa.ForeignKeyConstraint(["church_id"], ["church.id"],
                                ondelete="CASCADE"),
        # SET NULL rather than CASCADE: deleting a login should not erase the
        # record that somebody asked for one and was set up.
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_request_church_id", "account_request",
                    ["church_id"])
    op.create_index("ix_account_request_user_id", "account_request", ["user_id"])
    op.create_index("ix_account_request_church_status", "account_request",
                    ["church_id", "status", "created_at"])


def downgrade():
    op.drop_index("ix_account_request_church_status", table_name="account_request")
    op.drop_index("ix_account_request_user_id", table_name="account_request")
    op.drop_index("ix_account_request_church_id", table_name="account_request")
    op.drop_table("account_request")
