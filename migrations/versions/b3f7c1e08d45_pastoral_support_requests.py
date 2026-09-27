"""Pastoral support requests, and the category that alerts staff about one.

A member asks their church for help from the Home tab. The row holds what
they wrote; the email to staff holds only that a request exists and a link,
which is why the category is transactional (a church cannot opt out of being
told) and why nothing here is ever put in an email body.

The CHECK constraints on outbox_message.category and
notification_preference.category are replaced by hand: Alembic does not diff
CHECK constraints, and a missing value makes every send fail with an
IntegrityError (it did once, see revision 763dfd13feab).

Revision ID: b3f7c1e08d45
Revises: e5b82c40f117
"""

from alembic import op
import sqlalchemy as sa

revision = "b3f7c1e08d45"
down_revision = "e5b82c40f117"
branch_labels = None
depends_on = None

# Spelled out rather than imported: a migration keeps meaning what it meant
# on the day it ran.
WITH_PASTORAL = (
    "category IN ('account', 'kids_checkin', 'moderation', 'pastoral', "
    "'giving_receipt', 'welcome', 'next_step', 'group', 'chat', "
    "'announcement', 'digest')"
)
WITHOUT_PASTORAL = (
    "category IN ('account', 'kids_checkin', 'moderation', "
    "'giving_receipt', 'welcome', 'next_step', 'group', 'chat', "
    "'announcement', 'digest')"
)

KINDS = ("urgent", "visit", "talk", "prayer", "other")
CONTACTS = ("either", "phone", "email")
STATUSES = ("open", "answered")


def _in_list(column, values):
    return column + " IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _replace_categories(expression):
    for table in ("outbox_message", "notification_preference"):
        name = f"ck_{table}_ck_{table}_category"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(name), type_="check")
            batch_op.create_check_constraint(op.f(name), expression)


def upgrade():
    op.create_table(
        "support_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("contact_pref", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_by_user_id", sa.Integer(), nullable=True),
        sa.Column("answered_by_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"],
            name=op.f("fk_support_request_church_id_church"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"],
            name=op.f("fk_support_request_person_id_person"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["answered_by_user_id"], ["user.id"],
            name=op.f("fk_support_request_answered_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_request")),
        sa.CheckConstraint(_in_list("kind", KINDS), name="ck_support_request_kind"),
        sa.CheckConstraint(
            _in_list("contact_pref", CONTACTS), name="ck_support_request_contact"
        ),
        sa.CheckConstraint(
            _in_list("status", STATUSES), name="ck_support_request_status"
        ),
    )
    with op.batch_alter_table("support_request", schema=None) as batch_op:
        batch_op.create_index(
            "ix_support_church_status", ["church_id", "status", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_support_request_church_id"), ["church_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_support_request_person_id"), ["person_id"], unique=False
        )

    _replace_categories(WITH_PASTORAL)


def downgrade():
    op.execute("DELETE FROM outbox_message WHERE category = 'pastoral'")
    op.execute("DELETE FROM notification_preference WHERE category = 'pastoral'")
    _replace_categories(WITHOUT_PASTORAL)
    op.drop_table("support_request")
