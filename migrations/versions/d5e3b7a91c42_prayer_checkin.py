"""Weekly prayer check-in: one row per person per week.

One table. **No new notification category**, and that is deliberate rather
than an oversight. A routine weekly check-in does not alert anybody: sixty
members checking in on a Sunday would be sixty notifications, and a channel
that fires sixty times a week is a channel people turn off, taking the
pastoral alerts with it. Staff read check-ins on the list. The one case that
does reach somebody is a member ticking "please have someone reach out",
which writes an ordinary support request and rides the existing `pastoral`
category. So the CHECK constraints on `outbox_message.category` and
`notification_preference.category` are untouched here, which is the usual
trap this project has hit before (see f4b2096ad731) and is not hitting now.

Revision ID: d5e3b7a91c42
Revises: c71e8b35d9a4
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e3b7a91c42"
down_revision = "c71e8b35d9a4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prayer_checkin",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        # The Sunday the week starts, on the church's clock. This column is
        # the reset: there is no expiry job, and next week is a different
        # key.
        sa.Column("week_of", sa.Date(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "wants_contact", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("prayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prayed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("prayed_by_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # CASCADE, which is what `TenantScoped` declares. Several older
        # tables in this database were created without it and `flask db
        # check` has reported them as drift ever since; this one matches its
        # model, so it is not on that list.
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"],
            name=op.f("fk_prayer_checkin_church_id_church"),
            ondelete="CASCADE",
        ),
        # CASCADE: a person's own words about their own week. Once there is
        # no person there is nothing here that should outlive them.
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"],
            name=op.f("fk_prayer_checkin_person_id_person"),
            ondelete="CASCADE",
        ),
        # SET NULL, like a support request's: the row keeps the name of
        # whoever prayed for it after their account is removed.
        sa.ForeignKeyConstraint(
            ["prayed_by_user_id"], ["user.id"],
            name=op.f("fk_prayer_checkin_prayed_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prayer_checkin")),
    )
    with op.batch_alter_table("prayer_checkin", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_prayer_checkin_week", ["church_id", "person_id", "week_of"]
        )
        batch_op.create_index(
            "ix_prayer_checkin_church_week",
            ["church_id", "week_of", "prayed_at", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_prayer_checkin_church_id"), ["church_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_prayer_checkin_person_id"), ["person_id"], unique=False
        )


def downgrade():
    with op.batch_alter_table("prayer_checkin", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_prayer_checkin_person_id"))
        batch_op.drop_index(batch_op.f("ix_prayer_checkin_church_id"))
        batch_op.drop_index("ix_prayer_checkin_church_week")
        batch_op.drop_constraint("uq_prayer_checkin_week", type_="unique")
    op.drop_table("prayer_checkin")
