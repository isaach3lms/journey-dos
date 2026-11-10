"""A saved template is an email or a chat message.

Direct messages became a real feature in the same change, and the first thing
anybody wants after sending the same message to the fourth new visitor is to
stop typing it. The row shape was already right, so this is one column rather
than a second table: a template is words somebody writes often, and whether
those words leave as an email or land in a thread is a property of the row.

Existing rows are emails, which is what every one of them is.

The table keeps its name. Renaming `email_template` on a church that is
already running to make the word read better is not a trade worth making.

Revision ID: c71e8b35d9a4
Revises: b8d41f7a2093
"""

import sqlalchemy as sa
from alembic import op

revision = "c71e8b35d9a4"
down_revision = "b8d41f7a2093"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("email_template") as batch:
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(length=20),
                nullable=False,
                server_default="email",
            )
        )
    # The picker reads one kind at a time, so the kind leads. Church first,
    # because every query here is already scoped to one.
    op.create_index(
        "ix_template_church_kind", "email_template", ["church_id", "kind"]
    )


def downgrade():
    op.drop_index("ix_template_church_kind", table_name="email_template")
    with op.batch_alter_table("email_template") as batch:
        batch.drop_column("kind")
