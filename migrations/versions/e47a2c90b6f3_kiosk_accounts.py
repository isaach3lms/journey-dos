"""A login for the tablet in the lobby, and the label it prints on.

Three changes, one reason. Check-in was being run from a staff account left
signed in on a tablet in a hallway, because that was the only kind of account
there was. `user.is_kiosk` marks an account that can reach the check-in screens
and nothing else, `kiosk_setup_token` is how a tablet is signed in without
anybody typing a password into it, and `church.kids_label_size` is the roll in
the printer, which the tags have to be rendered at to come out right.

Nothing is backfilled. A church gets a kiosk account when somebody creates one
in Settings, and until then check-in works exactly as it did.

Revision ID: e47a2c90b6f3
Revises: c93d6b81ae40
"""

from alembic import op
import sqlalchemy as sa


revision = "e47a2c90b6f3"
down_revision = "c93d6b81ae40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "is_kiosk",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("church", schema=None) as batch:
        batch.add_column(sa.Column("kids_label_size", sa.String(length=20), nullable=True))

    op.create_table(
        "kiosk_setup_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("church_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["church_id"], ["church.id"],
            name="fk_kiosk_setup_token_church_id_church", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"],
            name="fk_kiosk_setup_token_user_id_user", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("kiosk_setup_token", schema=None) as batch:
        batch.create_index("ix_kiosk_token_hash", ["token_hash"], unique=False)
        batch.create_index(
            "ix_kiosk_church_user", ["church_id", "user_id", "created_at"], unique=False
        )
        batch.create_index(
            batch.f("ix_kiosk_setup_token_user_id"), ["user_id"], unique=False
        )
        batch.create_index(
            batch.f("ix_kiosk_setup_token_church_id"), ["church_id"], unique=False
        )


def downgrade():
    with op.batch_alter_table("kiosk_setup_token", schema=None) as batch:
        batch.drop_index(batch.f("ix_kiosk_setup_token_church_id"))
        batch.drop_index(batch.f("ix_kiosk_setup_token_user_id"))
        batch.drop_index("ix_kiosk_church_user")
        batch.drop_index("ix_kiosk_token_hash")
    op.drop_table("kiosk_setup_token")

    with op.batch_alter_table("church", schema=None) as batch:
        batch.drop_column("kids_label_size")

    # Any kiosk account left behind becomes an ordinary leader login, which is
    # why this is a downgrade worth reading twice: it hands the lobby tablet
    # the roster. Deactivate the account before downgrading.
    with op.batch_alter_table("user", schema=None) as batch:
        batch.drop_column("is_kiosk")
