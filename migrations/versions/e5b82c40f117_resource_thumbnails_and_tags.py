"""Resource thumbnails, and the tags members filter by.

Two things a church asked for on the same screen: their own artwork on a
resource instead of a brand gradient, and filters so members can find what
is for them.

Tags are one table for both stages and themes because they are the same
shape: a label on a resource that somebody filters by. `kind` says which
list it belongs to.

Revision ID: e5b82c40f117
Revises: c7a3f1d94b52
"""

from alembic import op
import sqlalchemy as sa

revision = "e5b82c40f117"
down_revision = "c7a3f1d94b52"
branch_labels = None
depends_on = None

# Spelled out rather than imported: a migration keeps meaning what it meant
# on the day it ran.
TAG_KINDS = ("stage", "theme")


def upgrade():
    with op.batch_alter_table("resource", schema=None) as batch:
        batch.add_column(sa.Column("thumb_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("thumb_type", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column(
            "thumb_bytes", sa.Integer(), nullable=False, server_default="0"
        ))
        batch.add_column(sa.Column("thumb_set_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "resource_tag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"], name=op.f("fk_resource_tag_church_id_church")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"],
            name=op.f("fk_resource_tag_resource_id_resource"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_tag")),
        sa.UniqueConstraint(
            "resource_id", "kind", "value", name=op.f("uq_resource_tag_value")
        ),
        # Written by hand, because Alembic does not diff check constraints.
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in TAG_KINDS) + ")",
            name="ck_resource_tag_kind",
        ),
    )
    with op.batch_alter_table("resource_tag", schema=None) as batch:
        batch.create_index(
            "ix_resource_tag_church_kind", ["church_id", "kind", "value"], unique=False
        )
        batch.create_index(
            batch.f("ix_resource_tag_church_id"), ["church_id"], unique=False
        )
        batch.create_index(
            batch.f("ix_resource_tag_resource_id"), ["resource_id"], unique=False
        )


def downgrade():
    op.drop_table("resource_tag")
    with op.batch_alter_table("resource", schema=None) as batch:
        batch.drop_column("thumb_set_at")
        batch.drop_column("thumb_bytes")
        batch.drop_column("thumb_type")
        batch.drop_column("thumb_data")
