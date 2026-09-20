"""chat notification category

Adds the "chat" category so a room post can email the people in that room.
The CHECK constraints on outbox_message.category and
notification_preference.category are replaced by hand: Alembic does not diff
CHECK constraints, and a missing value here makes every send fail with an
IntegrityError (it did once, see revision 763dfd13feab).

Revision ID: d4c81b6f2a37
Revises: 600e3a135bae
Create Date: 2026-09-20 13:10:00

"""
from alembic import op


revision = 'd4c81b6f2a37'
down_revision = '600e3a135bae'
branch_labels = None
depends_on = None

WITH_CHAT = "category IN ('account', 'kids_checkin', 'moderation', 'giving_receipt', 'welcome', 'next_step', 'group', 'chat', 'announcement', 'digest')"
WITHOUT_CHAT = "category IN ('account', 'kids_checkin', 'moderation', 'giving_receipt', 'welcome', 'next_step', 'group', 'announcement', 'digest')"


def _replace(expression):
    for table in ("outbox_message", "notification_preference"):
        name = f"ck_{table}_ck_{table}_category"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(name), type_="check")
            batch_op.create_check_constraint(op.f(name), expression)


def upgrade():
    _replace(WITH_CHAT)


def downgrade():
    op.execute("DELETE FROM outbox_message WHERE category = 'chat'")
    op.execute("DELETE FROM notification_preference WHERE category = 'chat'")
    _replace(WITHOUT_CHAT)
