"""journey giving goes to tithely

Points The Journey Church's staff Giving tab at the Tithely sign-in page.
Data only, no schema change. Set on request from the church (September
2026), replacing whatever was there. Staff change it afterwards from
Settings, Giving setup and reports.

Revision ID: a1f3c9d2e7b4
Revises: 6dad82908131
Create Date: 2026-09-19 11:45:00

"""
from alembic import op


revision = 'a1f3c9d2e7b4'
down_revision = '6dad82908131'
branch_labels = None
depends_on = None

URL = "https://auth.tithely.com/login"


def upgrade():
    op.execute(
        "UPDATE church SET giving_admin_url = '" + URL + "', "
        "giving_provider = COALESCE(giving_provider, 'tithely') "
        "WHERE slug = 'journey'"
    )


def downgrade():
    op.execute(
        "UPDATE church SET giving_admin_url = NULL "
        "WHERE slug = 'journey' AND giving_admin_url = '" + URL + "'"
    )
