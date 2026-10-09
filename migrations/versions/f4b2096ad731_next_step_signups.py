"""Next step sign-ups: Baptism and Volunteer, from a tile on the home screen.

One table and one notification category.

**The category is the part that fails silently if it is forgotten.** Both
`outbox_message.category` and `notification_preference.category` carry a CHECK
constraint listing every code, and Alembic does not diff check constraints, so
adding a category in Python without rebuilding them here leaves a database
that rejects every insert with the new code. SQLite does not enforce it the
same way, so the suite passes and production throws on the first baptism
sign-up anybody submits. This happened once already, when `guest_card` was
added; `tests/test_config.py` has guarded it ever since, and this migration
exists in the shape it does because of that.

Revision ID: f4b2096ad731
Revises: e3a81f52c7d9
"""

import sqlalchemy as sa
from alembic import op

revision = "f4b2096ad731"
down_revision = "e3a81f52c7d9"
branch_labels = None
depends_on = None

# Written out rather than imported from app.categories: a migration has to
# keep meaning what it meant on the day it ran, and that module will change
# again. Same reason the stage migrations spell their lists out.
BEFORE = (
    "account", "kids_checkin", "moderation", "pastoral", "guest_card",
    "giving_receipt", "welcome", "next_step", "group", "chat", "announcement",
    "digest",
)
AFTER = (
    "account", "kids_checkin", "moderation", "pastoral", "guest_card",
    "signup",
    "giving_receipt", "welcome", "next_step", "group", "chat", "announcement",
    "digest",
)


def _in_list(column, values):
    return column + " IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _replace_categories(expression):
    """Rebuild the CHECK on both tables that carry a category.

    The doubled name is the naming convention applied to a constraint the
    model already calls `ck_<table>_category`. Lifted from
    b3f7c1e08d45, which is the migration that worked it out.
    """
    for table in ("outbox_message", "notification_preference"):
        name = f"ck_{table}_ck_{table}_category"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(name), type_="check")
            batch_op.create_check_constraint(op.f(name), expression)


def upgrade():
    op.create_table(
        "next_step_signup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("offer", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"],
            name=op.f("fk_next_step_signup_church_id_church"),
        ),
        # CASCADE: this records that a member of this church asked for
        # something, and once there is no member there is no ask to answer.
        # A connect card is the opposite and uses SET NULL, because a guest
        # having visited stays true after their record goes.
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"],
            name=op.f("fk_next_step_signup_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_next_step_signup")),
    )
    with op.batch_alter_table("next_step_signup", schema=None) as batch_op:
        batch_op.create_index(
            "ix_signup_church_open",
            ["church_id", "handled_at", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_next_step_signup_church_id"), ["church_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_next_step_signup_person_id"), ["person_id"],
            unique=False,
        )

    _replace_categories(_in_list("category", AFTER))


def downgrade():
    # The rows go before the category does. A preference row carrying
    # 'signup' would violate the narrowed constraint the moment it is
    # applied, and the failure would be the whole downgrade rather than this
    # one table.
    op.execute("DELETE FROM notification_preference WHERE category = 'signup'")
    op.execute("DELETE FROM outbox_message WHERE category = 'signup'")
    _replace_categories(_in_list("category", BEFORE))

    with op.batch_alter_table("next_step_signup", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_next_step_signup_person_id"))
        batch_op.drop_index(batch_op.f("ix_next_step_signup_church_id"))
        batch_op.drop_index("ix_signup_church_open")
    op.drop_table("next_step_signup")
