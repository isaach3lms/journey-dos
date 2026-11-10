"""service published status

Services move between draft and published. The old "sent" value becomes
"published", because a plan that was emailed to the team was already, in
effect, published. The CHECK constraint is replaced by hand: Alembic does
not diff CHECK constraints (see tests/test_config.py).

Revision ID: c7e2a9f41d06
Revises: eaa113743e10
Create Date: 2026-09-19 20:15:00

"""
from alembic import op


revision = 'c7e2a9f41d06'
down_revision = 'eaa113743e10'
branch_labels = None
depends_on = None

NAME = "ck_service_ck_service_status"


def upgrade():
    with op.batch_alter_table("service", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(NAME), type_="check")
    op.execute("UPDATE service SET status = 'published' WHERE status = 'sent'")
    with op.batch_alter_table("service", schema=None) as batch_op:
        batch_op.create_check_constraint(op.f(NAME), "status IN ('draft', 'published')")


def downgrade():
    with op.batch_alter_table("service", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(NAME), type_="check")
    op.execute("UPDATE service SET status = 'sent' WHERE status = 'published'")
    with op.batch_alter_table("service", schema=None) as batch_op:
        batch_op.create_check_constraint(op.f(NAME), "status IN ('draft', 'sent')")
