"""Phone based kids check-in and pickup codes.

Adds the church setting and the indexed phone digits a kiosk looks a family up
by. Nothing existing is removed: generated PINs and pickup codes keep being
issued and stored, so turning the setting off restores the old behaviour with
nothing lost.

The setting defaults to off for any church created later, because a church
that has not weighed what a permanent pickup code costs should not inherit
it. It is switched on here for the churches already in the database, which is
what the one church running this asked for.

Revision ID: d41f9c2b7e06
Revises: b3f7c1e08d45
"""

from alembic import op
import sqlalchemy as sa


revision = "d41f9c2b7e06"
down_revision = "b3f7c1e08d45"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("church", schema=None) as batch:
        batch.add_column(sa.Column(
            "phone_checkin", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))

    with op.batch_alter_table("person", schema=None) as batch:
        batch.add_column(sa.Column("phone_last4", sa.String(length=4), nullable=True))
        batch.create_index("ix_person_phone_last4", ["phone_last4"], unique=False)

    # Backfill from the numbers already on file. Done in SQL rather than by
    # loading every person, because this runs on deploy against a live
    # database and the model's validator only fires on a Python assignment.
    #
    # Only digits count: numbers are stored as people typed them, so
    # "(573) 555-0142" and "5735550142" are the same number and have to give
    # the same four digits. Nested replaces rather than a regex, because
    # SQLite and Postgres do not agree on regex syntax and this has to run on
    # both.
    stripped = "phone"
    for junk in (" ", "-", "(", ")", ".", "+"):
        stripped = f"REPLACE({stripped}, '{junk}', '')"

    op.execute(f"""
        UPDATE person
           SET phone_last4 = SUBSTR({stripped}, LENGTH({stripped}) - 3, 4)
         WHERE phone IS NOT NULL
           AND LENGTH({stripped}) >= 4
    """)

    # The churches already here are the ones that asked for this.
    op.execute("UPDATE church SET phone_checkin = true")


def downgrade():
    with op.batch_alter_table("person", schema=None) as batch:
        batch.drop_index("ix_person_phone_last4")
        batch.drop_column("phone_last4")

    with op.batch_alter_table("church", schema=None) as batch:
        batch.drop_column("phone_checkin")
