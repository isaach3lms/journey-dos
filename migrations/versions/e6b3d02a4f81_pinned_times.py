"""A running-order line pinned to the clock.

Almost every line starts when the line above it ends. A pinned one starts when
it says, and the lines after it follow from there.

Stored as minutes from the service start rather than a time of day. Moving a
Sunday from 9:00 to 9:30 then moves a sermon pinned at 9:45 to 10:15, which is
what somebody shifting the whole morning meant, and a pin carries no timezone
so one entered in March cannot land an hour out on a Sunday in November.
Negative in the pre-service block: arriving at 8:00 before a 9:00 service
is -60.

Nullable, and null is what every existing line gets, which is exactly what
they meant before this column existed.

Revision ID: e6b3d02a4f81
Revises: d2a86f41c907
"""

import sqlalchemy as sa
from alembic import op

revision = "e6b3d02a4f81"
down_revision = "d2a86f41c907"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("service_item") as batch:
        batch.add_column(sa.Column("offset_minutes", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("service_item") as batch:
        batch.drop_column("offset_minutes")
