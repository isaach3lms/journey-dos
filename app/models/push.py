"""Web Push.

Chosen over a native-only push service on purpose. Web Push works in the
installed PWA on both platforms today, and it keeps working unchanged inside a
Capacitor wrapper later, so the store path does not mean rewriting how
notifications are sent. One transport, two delivery targets.

**A subscription belongs to a device, not a person.** One member has a phone, a
tablet, and a laptop, and each is a separate endpoint with its own keys.
Deleting one must not silence the others.

**Endpoints expire and get revoked constantly.** A browser reinstall, a cleared
site, a phone reset: all of these leave a subscription that returns 404 or 410
forever. Those are not failures to retry, they are dead rows to delete, and a
push system that retries them accumulates garbage until the worker spends its
time talking to endpoints nobody is behind.

**The keys are the church's, not per church.** VAPID identifies the sending
application to the push service, which is this platform, so one key pair is
correct. It lives in config, not in a column, because rotating it invalidates
every subscription everywhere and that should be a deploy rather than a form.
"""

from __future__ import annotations

from typing import Optional

from datetime import datetime, timedelta

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# A subscription that has failed this many times in a row is almost certainly
# behind a browser that is never coming back.
MAX_FAILURES = 5

# Push services expire endpoints. This is the age at which one that has never
# succeeded is assumed dead.
STALE_DAYS = 120


class PushSubscription(TenantScoped, TimestampMixin, db.Model):
    """One browser on one device, subscribed to notifications."""

    __tablename__ = "push_subscription"
    __table_args__ = (
        # The endpoint is the identity. Re-subscribing the same browser gives
        # back the same endpoint, so this makes registration idempotent.
        UniqueConstraint("endpoint_hash", name="uq_push_subscription_endpoint"),
        Index("ix_push_church_person", "church_id", "person_id"),
        Index("ix_push_church_user", "church_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship()  # noqa: F821

    # Copied so a push can be addressed to a person without loading the user.
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), index=True
    )

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # Endpoints are long and vary by push service, so the unique constraint is
    # on a hash rather than the column itself.
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # The browser's own public key and auth secret. Without both, a payload
    # cannot be encrypted for this device.
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)

    # Free text from the browser, purely so a person can recognise which
    # device they are looking at on the You tab.
    label: Mapped[Optional[str]] = mapped_column(String(120))

    last_success_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        # No endpoint or keys: a repr with them ends up in a log.
        return f"<PushSubscription user={self.user_id} device={self.label!r}>"

    @staticmethod
    def hash_endpoint(endpoint: str) -> str:
        import hashlib

        return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()

    def as_payload(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }

    def record_success(self) -> None:
        self.last_success_at = utcnow()
        self.failure_count = 0
        self.last_error = None

    def record_failure(self, error: str) -> None:
        self.failure_count += 1
        self.last_error = (error or "")[:500]

    @property
    def is_dead(self) -> bool:
        if self.failure_count >= MAX_FAILURES:
            return True
        if self.last_success_at is None:
            return self.created_at < utcnow() - timedelta(days=STALE_DAYS)
        return False

    # -- lookups ------------------------------------------------------------

    @classmethod
    def register(cls, church_id: int, user, endpoint: str, p256dh: str,
                 auth: str, label: str | None = None) -> "PushSubscription":
        """Idempotent. The same browser re-subscribing updates its row.

        A browser can hand back the same endpoint with rotated keys, so both
        are refreshed rather than only inserted.
        """
        digest = cls.hash_endpoint(endpoint)
        existing = db.session.scalar(
            db.select(cls).where(cls.endpoint_hash == digest)
        )
        if existing is not None:
            existing.church_id = church_id
            existing.user_id = user.id
            existing.person_id = user.person_id
            existing.p256dh = p256dh
            existing.auth = auth
            existing.label = label or existing.label
            existing.failure_count = 0
            existing.last_error = None
            return existing

        subscription = cls(
            church_id=church_id,
            user_id=user.id,
            person_id=user.person_id,
            endpoint=endpoint,
            endpoint_hash=digest,
            p256dh=p256dh,
            auth=auth,
            label=label,
        )
        db.session.add(subscription)
        return subscription

    @classmethod
    def for_person(cls, church_id: int, person_id: int) -> list["PushSubscription"]:
        return list(
            db.session.scalars(
                db.select(cls).where(
                    cls.church_id == church_id, cls.person_id == person_id
                )
            )
        )

    @classmethod
    def for_user(cls, church_id: int, user_id: int) -> list["PushSubscription"]:
        return list(
            db.session.scalars(
                db.select(cls).where(cls.church_id == church_id, cls.user_id == user_id)
            )
        )

    @classmethod
    def device_count(cls, church_id: int, user_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.user_id == user_id
            )
        ) or 0

    @classmethod
    def purge_dead(cls) -> int:
        """Delete subscriptions nobody is behind.

        A push system that retries revoked endpoints forever accumulates
        garbage until the worker spends its time talking to nothing.
        """
        removed = 0
        for subscription in db.session.scalars(db.select(cls)):
            if subscription.is_dead:
                db.session.delete(subscription)
                removed += 1
        return removed
