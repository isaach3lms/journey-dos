"""Proving somebody controls the address they signed up with.

Same construction as `PasswordResetToken` and for the same reasons: the token
is hashed rather than stored, single use, and short lived. See that module for
the full argument.

**Why verification is not optional here.** Self-registration without it would
let anyone who knows a member's email address claim that member's record, and
a linked record includes their household check-in PIN, their giving history,
and their household. Controlling the inbox is the same bar the password reset
flow already sets, so linking on a verified address is consistent with the
security the system already has. Linking on an unverified one would not be.
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
# Longer than a password reset. Somebody joining a church app is not usually
# sitting at their inbox waiting, and an expired link on the very first thing
# they tried is a bad introduction.
LIFETIME_HOURS = 72
MAX_REQUESTS_PER_HOUR = 5


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EmailVerificationToken(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "email_verification_token"
    __table_args__ = (
        Index("ix_verify_token_hash", "token_hash"),
        Index("ix_verify_church_user", "church_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship()  # noqa: F821

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<EmailVerificationToken user={self.user_id} used={bool(self.consumed_at)}>"

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and utcnow() < self.expires_at

    def consume(self) -> None:
        self.consumed_at = utcnow()

    @classmethod
    def issue(cls, user) -> tuple["EmailVerificationToken", str]:
        cls.invalidate_all_for(user)

        raw = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls(
            church_id=user.church_id,
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=utcnow() + timedelta(hours=LIFETIME_HOURS),
        )
        db.session.add(token)
        return token, raw

    @classmethod
    def invalidate_all_for(cls, user) -> int:
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
    def redeem(cls, church_id: int, raw: str) -> "EmailVerificationToken | None":
        if not raw or len(raw) < 20:
            return None
        token = db.session.scalar(
            db.select(cls).where(
                cls.token_hash == hash_token(raw), cls.church_id == church_id
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
    def purge_expired(cls, older_than_days: int = 30) -> int:
        cutoff = utcnow() - timedelta(days=older_than_days)
        return db.session.execute(db.delete(cls).where(cls.created_at < cutoff)).rowcount
