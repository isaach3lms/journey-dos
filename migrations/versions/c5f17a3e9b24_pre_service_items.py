"""Pre-service lines on a running order.

A church publishes a service at 9:00 and the team is there at 8:00. The only
way to get that hour into the plan was to enter the service as 8:00, which
then said 8:00 on the member app, in every invitation and on every report.

A flagged line is clocked backwards from the service start instead, so the
pre-service block adds up to the time the team arrives and the published start
time stops moving. See the note on ServiceItem.pre_service.

Existing rows are all False, which is exactly what they meant before this
column existed, so no Sunday already in the system changes.

Revision ID: c5f17a3e9b24
Revises: b9e4c23a7f15
"""

import sqlalchemy as sa
from alembic import op

revision = "c5f17a3e9b24"
down_revision = "b9e4c23a7f15"
branch_labels = None
depends_on = None


# Both tables get the same column: the template is what a church fills in once
# so the block is not rebuilt by hand every week.
TABLES = ("service_item", "service_template_item")


def upgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "pre_service",
                    sa.Boolean(),
                    nullable=False,
                    # `sa.false()`, not `sa.text("0")`: Postgres
                    # refuses an integer default on a boolean
                    # column, and local development is SQLite,
                    # so that one would only fail on Render.
                    server_default=sa.false(),
                )
            )


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("pre_service")
