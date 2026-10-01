"""Who hears about a pastoral request.

A church setting rather than a hardcoded list, for the same reason every other
church detail here is a row: adding or changing one must not need a deploy.

Seeded with the three addresses The Journey Church asked for. Churches added
later get an empty list, which falls back to every active staff account, and
that is the right default for a church that has not thought about it.

Revision ID: c93d6b81ae40
Revises: f10c7a94db28
"""

from alembic import op
import sqlalchemy as sa


revision = "c93d6b81ae40"
down_revision = "f10c7a94db28"
branch_labels = None
depends_on = None


# Exactly as given, including the spelling of the third domain. A migration is
# not the place to guess at somebody's address: the setting is one box in the
# browser and whoever owns it can correct it in a few seconds.
JOURNEY_CARE_TEAM = "\n".join([
    "isaac@betweensundaysconsulting.com",
    "brandon@thejourneychurchsemo.com",
    "whitney@thejourneychruchsemo.com",
])


def upgrade():
    with op.batch_alter_table("church", schema=None) as batch:
        batch.add_column(sa.Column("pastoral_alert_emails", sa.Text(), nullable=True))

    op.execute(
        sa.text("UPDATE church SET pastoral_alert_emails = :who WHERE slug = 'journey'")
        .bindparams(who=JOURNEY_CARE_TEAM)
    )


def downgrade():
    with op.batch_alter_table("church", schema=None) as batch:
        batch.drop_column("pastoral_alert_emails")
