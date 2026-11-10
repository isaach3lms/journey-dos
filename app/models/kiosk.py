"""Signing a tablet in once, without anybody typing a password into it.

A password on a kiosk is a password on a sticky note. The volunteer who sets
up the tablet is not the volunteer who uses it, the account gets shared because
sharing it is the job, and within a month the password is written on the back
of the iPad case. Rotating it means walking round every tablet.

So the kiosk account has a password that is never meant to be typed, and
setup happens through a link instead. Staff generate it on a phone, open it on
the tablet once, and that tablet holds a year-long session. Nothing is typed
and nothing is written down.

The same four properties as a password reset token, for the same reasons, and
they are not re-argued here. See `app/models/password_reset.py`. Two
differences, both deliberate:

**Twenty-four hours rather than sixty minutes.** Somebody sets the tablets up
on Saturday evening for Sunday morning, and a link that died overnight would
send them back to a screen to generate another one with a church arriving.

**Opening it is a POST, not a GET.** A link pasted into a group chat gets
fetched by every preview bot that sees it, and a GET that signs somebody in
would be burned before a human touched it. The link opens a page with a button
on it, and the button is what signs the tablet in.
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
LIFETIME_HOURS = 24


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class KioskSetupToken(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "kiosk_setup_token"
    __table_args__ = (
        Index("ix_kiosk_token_hash", "token_hash"),
        Index("ix_kiosk_church_user", "church_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship()  # noqa: F821

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # What the tablet was called when it was set up, so the list in Settings
    # reads "Lobby iPad" rather than six identical rows.
    label: Mapped[Optional[str]] = mapped_column(String(60))

    def __repr__(self) -> str:
        return f"<KioskSetupToken user={self.user_id} used={bool(self.consumed_at)}>"

    @property
    def is_expired(self) -> bool:
        return utcnow() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and not self.is_expired

    def consume(self) -> None:
        self.consumed_at = utcnow()

    @classmethod
    def issue(cls, user, label: str | None = None) -> tuple["KioskSetupToken", str]:
        """Mint one. Returns the row and the raw value, which is never stored.

        Outstanding tokens are NOT invalidated, unlike a password reset. A
        church setting up three tablets generates three links and opens them
        one after another, and retiring each as the next is made would mean
        only the last one worked.
        """
        raw = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls(
            church_id=user.church_id,
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=utcnow() + timedelta(hours=LIFETIME_HOURS),
            label=(label or "").strip()[:60] or None,
        )
        db.session.add(token)
        return token, raw

    @classmethod
    def redeem(cls, church_id: int, raw: str) -> "KioskSetupToken | None":
        """A usable token for this church, or None.

        Scoped to the church resolved from the host, so a link minted at one
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
    def invalidate_all_for(cls, user) -> int:
        """Retire every outstanding link. What "revoke" means in Settings."""
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
    def purge_expired(cls, older_than_days: int = 7) -> int:
        cutoff = utcnow() - timedelta(days=older_than_days)
        result = db.session.execute(db.delete(cls).where(cls.created_at < cutoff))
        return result.rowcount
