"""Who gets the metrics digest, per church.

One nullable text column, one address per line, same shape as the pastoral
and guest alert lists beside it. Empty means the digest is not sent, and that
is the default for every church: a report is something somebody asked for,
and mailing a church's numbers out because the platform assumed they wanted
them is a decision that is not the platform's to make.

**The one seeded value.** Journey's list is set to the address that asked for
it. Written here rather than in application code, because code that knows one
customer's email address sends every later customer's numbers to the same
place the day a second church is added. A church row that does not exist is
skipped, so this is a no-op on a fresh database and on every other
deployment.

Revision ID: a9b4e72f0d16
Revises: f2a8d61e5c93
"""

import sqlalchemy as sa
from alembic import op

revision = "a9b4e72f0d16"
down_revision = "f2a8d61e5c93"
branch_labels = None
depends_on = None

SEED = (("journey", "isaac@betweensundaysconsulting.com"),)


def upgrade():
    with op.batch_alter_table("church", schema=None) as batch_op:
        batch_op.add_column(sa.Column("digest_emails", sa.Text(), nullable=True))

    connection = op.get_bind()
    church = sa.table(
        "church",
        sa.column("slug", sa.String),
        sa.column("digest_emails", sa.Text),
    )
    for slug, address in SEED:
        connection.execute(
            church.update()
            .where(church.c.slug == slug)
            # Only when it is still empty, so re-running this against a
            # database where somebody has since edited the list does not
            # overwrite what they chose.
            .where(church.c.digest_emails.is_(None))
            .values(digest_emails=address)
        )


def downgrade():
    with op.batch_alter_table("church", schema=None) as batch_op:
        batch_op.drop_column("digest_emails")
