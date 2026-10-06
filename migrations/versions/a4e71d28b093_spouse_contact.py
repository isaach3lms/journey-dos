"""A spouse's own email and phone on an account request.

Fields rather than more free text, because this is the one part of a
household that usually needs a second login: an account is an email address,
and a phone number is what kids check-in looks a family up by. Pulling either
out of a sentence by hand is how one of them gets typed wrong.

All three nullable. Most requests will not have a spouse, and a request made
before this shipped cannot have one.

Revision ID: a4e71d28b093
Revises: f1a93c6e5d28
"""

import sqlalchemy as sa
from alembic import op

revision = "a4e71d28b093"
down_revision = "f1a93c6e5d28"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("account_request") as batch:
        batch.add_column(sa.Column("spouse_name", sa.String(length=160),
                                   nullable=True))
        batch.add_column(sa.Column("spouse_email", sa.String(length=255),
                                   nullable=True))
        batch.add_column(sa.Column("spouse_phone", sa.String(length=40),
                                   nullable=True))


def downgrade():
    with op.batch_alter_table("account_request") as batch:
        batch.drop_column("spouse_phone")
        batch.drop_column("spouse_email")
        batch.drop_column("spouse_name")
