"""The `metrics` notification category, for the digest email.

One category, and nothing else. It is a migration of its own rather than a
few lines on the digest migration because the two CHECK constraints below are
the part of adding a category that fails in production and nowhere else:
Alembic does not diff CHECK constraints, SQLite does not enforce the old one
the same way, so a category added in Python alone leaves a suite that passes
and a database that refuses every digest with an IntegrityError. That has
happened twice on this project, with `guest_card` and with section headers in
service plans. `tests/test_config.py` now runs these migrations and compares
every constraint against the models, which is what makes forgetting this file
a failing test rather than a Monday morning with no email.

Revision ID: b1c53a8e9f27
Revises: a9b4e72f0d16
"""

from alembic import op

revision = "b1c53a8e9f27"
down_revision = "a9b4e72f0d16"
branch_labels = None
depends_on = None

# Spelled out rather than imported from app.categories, like every migration
# that has touched this list: a migration has to keep meaning what it meant on
# the day it ran, and that module will change again.
BEFORE = (
    "account", "kids_checkin", "moderation", "pastoral", "guest_card",
    "signup",
    "giving_receipt", "welcome", "next_step", "group", "chat", "announcement",
    "digest",
)
AFTER = (
    "account", "kids_checkin", "moderation", "pastoral", "guest_card",
    "signup", "metrics",
    "giving_receipt", "welcome", "next_step", "group", "chat", "announcement",
    "digest",
)


def _in_list(values) -> str:
    return "category IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _replace_categories(expression: str) -> None:
    """Rebuild the CHECK on both tables that carry a category.

    The doubled name is the naming convention applied to a constraint the
    model already calls `ck_<table>_category`. Worked out in b3f7c1e08d45.
    """
    for table in ("outbox_message", "notification_preference"):
        name = f"ck_{table}_ck_{table}_category"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(name), type_="check")
            batch_op.create_check_constraint(op.f(name), expression)


def upgrade():
    _replace_categories(_in_list(AFTER))


def downgrade():
    # Rows carrying the category go first. Narrowing the constraint while a
    # single digest row still says 'metrics' fails the migration on Postgres,
    # and a downgrade that cannot run is a downgrade nobody has.
    op.execute("DELETE FROM outbox_message WHERE category = 'metrics'")
    op.execute("DELETE FROM notification_preference WHERE category = 'metrics'")
    _replace_categories(_in_list(BEFORE))
