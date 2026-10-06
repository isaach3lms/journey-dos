"""Connect cards, as guests filled them in.

A table rather than a note on the person, because most of a card has nowhere
to live on a roster record: how somebody heard about the church, whether they
asked to be contacted, and whatever they wrote in the box.

`person_id` is SET NULL on purpose. Deleting somebody from the roster should
not take the record of their visit with them, or the dashboard number for
that week would change retrospectively.

Revision ID: d7b2e41c8a93
Revises: c3f7a1e9d204
"""

import sqlalchemy as sa
from alembic import op

revision = "d7b2e41c8a93"
down_revision = "c3f7a1e9d204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "guest_card",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("last_name", sa.String(length=80), nullable=False,
                  server_default=""),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("heard", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("wants_contact", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("is_new_person", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_by_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["church_id"], ["church.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guest_card_church_id", "guest_card", ["church_id"])
    op.create_index("ix_guest_card_person_id", "guest_card", ["person_id"])
    op.create_index("ix_guest_church_time", "guest_card",
                    ["church_id", "discarded_at", "created_at"])


def downgrade():
    op.drop_index("ix_guest_church_time", table_name="guest_card")
    op.drop_index("ix_guest_card_person_id", table_name="guest_card")
    op.drop_index("ix_guest_card_church_id", table_name="guest_card")
    op.drop_table("guest_card")
