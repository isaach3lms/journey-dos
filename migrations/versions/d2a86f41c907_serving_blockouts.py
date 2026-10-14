"""Dates a volunteer is not available to serve.

A leader asked six people and two were away, and the only way anybody found
out was the two of them answering no, days later, after the invitation had
gone. This is where somebody says it up front.

Dates rather than datetimes, inclusive at both ends. See the note at the top
of app/models/blockout.py for why.

Revision ID: d2a86f41c907
Revises: c5f17a3e9b24
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a86f41c907"
down_revision = "c5f17a3e9b24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "serving_blockout",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=140), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["church_id"], ["church.id"], ondelete="CASCADE"),
        # The row belongs to the person. Deleting somebody takes their
        # blockouts with them rather than leaving dates against a gap.
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        # A backwards range is not a short blockout, it is a typo, and one
        # that would silently cover nothing at all.
        sa.CheckConstraint("ends_on >= starts_on", name="ck_blockout_range"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_blockout_person", "serving_blockout",
        ["church_id", "person_id", "ends_on"],
    )
    op.create_index(
        op.f("ix_serving_blockout_person_id"), "serving_blockout", ["person_id"],
    )
    op.create_index(
        op.f("ix_serving_blockout_church_id"), "serving_blockout", ["church_id"],
    )


def downgrade():
    op.drop_index(op.f("ix_serving_blockout_church_id"), table_name="serving_blockout")
    op.drop_index(op.f("ix_serving_blockout_person_id"), table_name="serving_blockout")
    op.drop_index("ix_blockout_person", table_name="serving_blockout")
    op.drop_table("serving_blockout")
