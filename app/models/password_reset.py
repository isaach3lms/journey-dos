"""Password reset tokens.

Four properties, each of which exists because its absence is a known way to
lose accounts.

**The token is never stored.** Only a SHA-256 of it. A reset table full of
usable links is a database read away from every account in the system, and
database reads leak in ways password hashes are designed to survive: backups,
log shipping, a read replica, a support query pasted into a chat. Hashing means
a stolen table yields nothing.

**Single use.** `consumed_at` is set the moment a token is redeemed. A reset
link sits in an inbox forever otherwise, and inboxes get compromised long after
the reset was legitimate.

**Short lived.** Sixty minutes. Long enough for someone to find the email,
short enough that a forwarded message is not a standing key.

**Requesting one invalidates the others.** Somebody who clicks the button three
times because nothing seemed to happen should not end up with three live keys.

Plain SHA-256 rather than a password hash is correct here and only here: the
token is 32 bytes of `secrets` output, so there is no dictionary to attack and
nothing for a slow KDF to buy. A user-chosen password is the opposite case,
which is why `User.set_password` uses scrypt.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from datetime import datetime, timedelta

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

TOKEN_BYTES = 32
LIFETIME_MINUTES = 60

# How many resets one address may request in the window below. High enough that
# a person retrying is never blocked, low enough that the form cannot be used
# to bombard somebody's inbox from a church they do not attend.
MAX_REQUESTS_PER_HOUR = 5


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PasswordResetToken(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "password_reset_token"
    __table_args__ = (
        Index("ix_reset_token_hash", "token_hash"),
        Index("ix_reset_church_user", "church_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship()  # noqa: F821

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # Recorded for the audit surface at increment 15. Not used for anything
    # security-critical, because a proxy header is trivially forged.
    requested_ip: Mapped[Optional[str]] = mapped_column(String(64))

    def __repr__(self) -> str:
        return f"<PasswordResetToken user={self.user_id} used={bool(self.consumed_at)}>"

    @property
    def is_expired(self) -> bool:
        return utcnow() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and not self.is_expired

    def consume(self) -> None:
        self.consumed_at = utcnow()

    # -- issuing and redeeming ---------------------------------------------

    @classmethod
    def issue(cls, user, requested_ip: str | None = None) -> tuple["PasswordResetToken", str]:
        """Mint a token. Returns the row and the raw value, which is never stored.

        The raw value is returned exactly once, to be put in the email. There
        is no way to recover it afterwards, by design.
        """
        cls.invalidate_all_for(user)

        raw = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls(
            church_id=user.church_id,
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=utcnow() + timedelta(minutes=LIFETIME_MINUTES),
            requested_ip=(requested_ip or "")[:64] or None,
        )
        db.session.add(token)
        return token, raw

    @classmethod
    def invalidate_all_for(cls, user) -> int:
        """Retire every outstanding token for this user."""
        outstanding = db.session.scalars(
            db.select(cls).where(
                cls.church_id == user.church_id,
                cls.user_id == user.id,
                cls.consumed_at.is_(None),
            )
        ).all()
        for token in outstanding:
            token.consume()
        return len(outstanding)

    @classmethod
    def redeem(cls, church_id: int, raw: str) -> "PasswordResetToken | None":
        """Find a usable token for this church, or None.

        Scoped to the church resolved from the host, so a token minted at one
        tenant is inert at another even though the value would match.
        """
        if not raw or len(raw) < 20:
            return None

        token = db.session.scalar(
            db.select(cls).where(
                cls.token_hash == hash_token(raw),
                cls.church_id == church_id,
            )
        )
        if token is None or not token.is_usable:
            return None
        return token

    @classmethod
    def recent_request_count(cls, user, minutes: int = 60) -> int:
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == user.church_id,
                cls.user_id == user.id,
                cls.created_at >= utcnow() - timedelta(minutes=minutes),
            )
        ) or 0

    @classmethod
    def purge_expired(cls, older_than_days: int = 7) -> int:
        cutoff = utcnow() - timedelta(days=older_than_days)
        result = db.session.execute(
            db.delete(cls).where(cls.created_at < cutoff)
        )
        return result.rowcount
