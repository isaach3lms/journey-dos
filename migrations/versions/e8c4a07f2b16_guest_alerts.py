"""Who hears about a connect card, and the guest asking for a login.

Three columns, all nullable, all with a sensible meaning when empty:

- `guest_card.wants_account`: a guest asked to be set up with a login.
- `church.guest_alert_emails`: who to tell. Empty means every active staff
  account, the same fallback the pastoral list already uses.
- `church.account_request_email`: where an account request goes. Empty falls
  back to the platform address in `app/models/church.py`, because creating a
  login is administration rather than something church staff do.

Revision ID: e8c4a07f2b16
Revises: d7b2e41c8a93
"""

import sqlalchemy as sa
from alembic import op

revision = "e8c4a07f2b16"
down_revision = "d7b2e41c8a93"
branch_labels = None
depends_on = None


# Spelled out rather than imported, like every other migration that has
# touched this list: a migration keeps meaning what it meant on the day it
# ran, and importing the constant would rewrite history every time somebody
# adds a category.
#
# This is here because a test caught it, not because anybody remembered. The
# alert category is new, the models' CHECK constraint grew to include it, and
# the migrated database's did not. Tests build the schema from the models and
# would have passed; production builds it from these files, and every staff
# alert would have been refused by the database on insert.
WITH_GUEST_CARD = (
    "category IN ('account', 'kids_checkin', 'moderation', 'pastoral', "
    "'guest_card', 'giving_receipt', 'welcome', 'next_step', 'group', "
    "'chat', 'announcement', 'digest')"
)
WITHOUT_GUEST_CARD = (
    "category IN ('account', 'kids_checkin', 'moderation', 'pastoral', "
    "'giving_receipt', 'welcome', 'next_step', 'group', 'chat', "
    "'announcement', 'digest')"
)


def _replace_categories(expression):
    for table in ("outbox_message", "notification_preference"):
        name = f"ck_{table}_ck_{table}_category"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(name), type_="check")
            batch_op.create_check_constraint(op.f(name), expression)


def upgrade():
    _replace_categories(WITH_GUEST_CARD)

    with op.batch_alter_table("guest_card") as batch:
        batch.add_column(sa.Column("wants_account", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))

    with op.batch_alter_table("church") as batch:
        batch.add_column(sa.Column("guest_alert_emails", sa.Text(), nullable=True))
        batch.add_column(sa.Column("account_request_email",
                                   sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("church") as batch:
        batch.drop_column("account_request_email")
        batch.drop_column("guest_alert_emails")

    with op.batch_alter_table("guest_card") as batch:
        batch.drop_column("wants_account")

    _replace_categories(WITHOUT_GUEST_CARD)
