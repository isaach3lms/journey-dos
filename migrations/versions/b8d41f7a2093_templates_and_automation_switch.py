"""Saved email templates, and a switch for the automatic ones.

Two changes that arrived together because they are two halves of one
decision: a church that stops the welcome series sending itself is going to
write those emails by hand, and writing the same email by hand forty times
is how writing to new people quietly stops in week three.

`church.automations_on` pauses the sequence engine. On by default, because a
church that set up a welcome series wants it; existing churches get True,
which is what they have been doing.

`email_template` is one saved email per row. Placeholders in it are filled
when it is loaded into the form, not when it is sent, so staff edit the same
words the person will read.

Revision ID: b8d41f7a2093
Revises: e6b3d02a4f81
"""

import sqlalchemy as sa
from alembic import op

revision = "b8d41f7a2093"
down_revision = "e6b3d02a4f81"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("church") as batch:
        batch.add_column(
            sa.Column(
                "automations_on",
                sa.Boolean(),
                nullable=False,
                # `sa.true()`, not `sa.text("1")`: Postgres refuses an
                # integer default on a boolean column, and local development
                # is SQLite, so that one would only fail on Render.
                server_default=sa.true(),
            )
        )

    op.create_table(
        "email_template",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["church_id"], ["church.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: a template outlives the staff member who
        # wrote it, and a church losing its welcome email because somebody
        # left is the worst possible reading of "delete this account".
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_template_church_name", "email_template",
                    ["church_id", "name"])
    op.create_index(op.f("ix_email_template_church_id"), "email_template",
                    ["church_id"])


def downgrade():
    op.drop_index(op.f("ix_email_template_church_id"), table_name="email_template")
    op.drop_index("ix_template_church_name", table_name="email_template")
    op.drop_table("email_template")
    with op.batch_alter_table("church") as batch:
        batch.drop_column("automations_on")
