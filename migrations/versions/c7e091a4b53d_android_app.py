"""The Android app's package name and signing fingerprints, per church.

Both feed `/.well-known/assetlinks.json`. Nothing is seeded, including for
Journey: the fingerprint that matters is the one Google generates when the
bundle is uploaded, which does not exist until somebody uploads it. Writing
the upload key's fingerprint here would look configured and verify nothing on
a phone, which is worse than an empty field that plainly needs filling in.

Revision ID: c7e091a4b53d
Revises: b1c53a8e9f27
"""

import sqlalchemy as sa
from alembic import op

revision = "c7e091a4b53d"
down_revision = "b1c53a8e9f27"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("church", sa.Column("android_package", sa.String(255), nullable=True))
    op.add_column("church", sa.Column("android_fingerprints", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("church", "android_fingerprints")
    op.drop_column("church", "android_package")
