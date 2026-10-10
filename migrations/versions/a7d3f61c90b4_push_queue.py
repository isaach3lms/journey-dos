"""The push queue.

Push used to be sent inside the web request, one HTTPS call per device with a
ten second timeout, while the person who pressed the button waited. This is
the table that stops that: a row goes in, the worker sends it.

No CHECK on `category`, unlike `outbox_message`. See the model's docstring:
adding a notification category already means rebuilding a constraint on two
tables by hand, and a third would buy very little for rows that are drained
and purged within days.

Revision ID: a7d3f61c90b4
Revises: f4b2096ad731
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d3f61c90b4"
down_revision = "f4b2096ad731"
branch_labels = None
depends_on = None


PUSH_STATUSES = ("queued", "sent", "failed", "suppressed", "expired")
_STATUS_LIST = ", ".join(f"'{s}'" for s in PUSH_STATUSES)


def upgrade():
    op.create_table(
        "push_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=300), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("devices", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_STATUS_LIST})", name=op.f("ck_push_queue_status")
        ),
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"],
            name=op.f("fk_push_queue_church_id_church"),
            ondelete="CASCADE",
        ),
        # CASCADE: a notification is to a person, and with no person there is
        # nobody to notify. The outbox uses SET NULL because an email that
        # was sent stays a fact about the church after the record goes; this
        # table holds work, not history.
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"],
            name=op.f("fk_push_queue_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_queue")),
        sa.UniqueConstraint(
            "church_id", "dedupe_key", name=op.f("uq_push_queue_dedupe_key")
        ),
    )
    with op.batch_alter_table("push_queue", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_push_queue_church_id"), ["church_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_push_queue_person_id"), ["person_id"], unique=False
        )
        batch_op.create_index(
            "ix_push_queue_status_queued", ["status", "queued_at"], unique=False
        )
        batch_op.create_index(
            "ix_push_queue_church_status",
            ["church_id", "status", "queued_at"],
            unique=False,
        )
        batch_op.create_index("ix_push_queue_claim", ["claim_token"], unique=False)


def downgrade():
    with op.batch_alter_table("push_queue", schema=None) as batch_op:
        batch_op.drop_index("ix_push_queue_claim")
        batch_op.drop_index("ix_push_queue_church_status")
        batch_op.drop_index("ix_push_queue_status_queued")
        batch_op.drop_index(batch_op.f("ix_push_queue_person_id"))
        batch_op.drop_index(batch_op.f("ix_push_queue_church_id"))
    op.drop_table("push_queue")
