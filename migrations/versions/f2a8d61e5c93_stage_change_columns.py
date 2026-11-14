"""A stage change records where it went from and to, as codes.

Until now the only record was `summary`, an English sentence. That is the
right thing for a pastor to read on a timeline and the wrong thing to count:
"how many moved Visitor to Member last month" would mean matching a sentence,
and the first edit to that wording zeroes the number with nothing failing
anywhere.

**The backfill is best effort, on purpose.** Existing rows are parsed out of
"Moved from X to Y" using the stage labels as they are today, which is exactly
the fragile thing these columns exist to replace. Done once, now, while that
wording is still the wording every existing row was written with, it is safe.
A row that does not match is left null rather than guessed at, and the metrics
treat null as "not counted" rather than as a move to nowhere.

Labels are spelled out here rather than imported from app.stages. A migration
that imports application code is a migration that stops running the day that
code changes shape, and this one has to keep working on a database restored
from a backup years from now.

Revision ID: f2a8d61e5c93
Revises: e7c1a4f30b28
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a8d61e5c93"
down_revision = "e7c1a4f30b28"
branch_labels = None
depends_on = None

# The rail as it stands on the day this migration is written.
LABELS = {
    "Visitor": "visitor",
    "Member": "member",
    "Volunteer": "volunteer",
    "Disciple": "disciple",
    "Leader": "leader",
}


def upgrade():
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.add_column(sa.Column("from_stage", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("to_stage", sa.String(length=32), nullable=True))
        # The metrics' only query against this table: one church, one kind,
        # a date range. Without it, counting a month of stage moves is a
        # scan of every event the church has ever recorded.
        batch_op.create_index(
            "ix_event_church_stage_time",
            ["church_id", "kind", "occurred_at", "to_stage"],
            unique=False,
        )

    connection = op.get_bind()
    events = sa.table(
        "person_event",
        sa.column("id", sa.Integer),
        sa.column("kind", sa.String),
        sa.column("summary", sa.String),
        sa.column("from_stage", sa.String),
        sa.column("to_stage", sa.String),
    )

    # One UPDATE per pair rather than a row-by-row pass in Python. Twenty
    # five statements against an indexed column, whatever the table's size.
    for from_label, from_code in LABELS.items():
        for to_label, to_code in LABELS.items():
            if from_code == to_code:
                continue
            connection.execute(
                events.update()
                .where(events.c.kind == "stage_change")
                .where(events.c.to_stage.is_(None))
                .where(events.c.summary == f"Moved from {from_label} to {to_label}")
                .values(from_stage=from_code, to_stage=to_code)
            )


def downgrade():
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.drop_index("ix_event_church_stage_time")
        batch_op.drop_column("to_stage")
        batch_op.drop_column("from_stage")
