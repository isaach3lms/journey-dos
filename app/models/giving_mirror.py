"""The giving mirror.

**Read only, in both directions of that phrase.** Nothing here writes to
Tithely, and nothing here is the source of truth. Tithely owns the ledger; this
is a copy kept so the system can answer a question Tithely cannot: not "did
revenue drop" but "has Chris Vaughn stopped giving". The first is a budget
report. The second is usually a discipleship signal weeks before it is a budget
problem, and it is the sharpest thing this product does.

Because it is a mirror, three properties matter more than the schema:

**Idempotent on `provider_txn_id`.** A sync that runs twice, or a CSV imported
twice, must not double a church's giving totals. The unique constraint makes
that structural rather than careful.

**A gift may have no person.** Tithely donor identity is not a DOS person, and
inventing a link is worse than admitting there isn't one. Unmatched gifts land
in a review queue a human clears.

**A gift is never edited to correct the ledger.** If Tithely changes, the sync
overwrites from the provider. Staff correct the *match*, not the amount.
"""

from __future__ import annotations

from typing import Optional

from datetime import date, datetime, timedelta

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

PROVIDER_TITHELY = "tithely"
PROVIDERS = (PROVIDER_TITHELY,)

STATUS_PENDING = "pending_setup"
STATUS_ACTIVE = "active"
STATUS_ERROR = "error"
CREDENTIAL_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_ERROR)

MATCH_UNMATCHED = "unmatched"
MATCH_MATCHED = "matched"
MATCH_IGNORED = "ignored"
MATCH_STATUSES = (MATCH_UNMATCHED, MATCH_MATCHED, MATCH_IGNORED)

MATCH_LABELS = {
    MATCH_UNMATCHED: "Not matched to a person",
    MATCH_MATCHED: "Matched",
    MATCH_IGNORED: "Set aside",
}

RECURRING_ACTIVE = "active"
RECURRING_PAUSED = "paused"
RECURRING_CANCELLED = "cancelled"
RECURRING_STATUSES = (RECURRING_ACTIVE, RECURRING_PAUSED, RECURRING_CANCELLED)

# How long past the expected next charge before a recurring gift counts as
# stopped. Card failures and bank holidays account for a few days; three weeks
# of silence is a person, not a payment system.
LAPSE_GRACE_DAYS = 21

FREQUENCY_DAYS = {
    "weekly": 7,
    "fortnightly": 14,
    "biweekly": 14,
    "monthly": 31,
    "quarterly": 92,
    "yearly": 366,
}

_MATCH_LIST = ", ".join(f"'{s}'" for s in MATCH_STATUSES)
_CRED_LIST = ", ".join(f"'{s}'" for s in CREDENTIAL_STATUSES)


class IntegrationCredential(TenantScoped, TimestampMixin, db.Model):
    """One provider's keys for one church.

    The private key is encrypted at rest and never logged, never rendered, and
    never returned by any route. `masked_private_key` is what a screen shows.
    """

    __tablename__ = "integration_credential"
    __table_args__ = (
        UniqueConstraint("church_id", "provider", name="uq_integration_credential_provider"),
        CheckConstraint(f"status IN ({_CRED_LIST})", name="ck_integration_credential_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    public_key: Mapped[Optional[str]] = mapped_column(String(200))
    private_key_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    organization_ref: Mapped[Optional[str]] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=STATUS_PENDING
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        # Deliberately no key material, because reprs end up in logs.
        return f"<IntegrationCredential {self.provider} {self.status}>"

    def set_private_key(self, raw: str | None, secret_key: str) -> None:
        from app.crypto import encrypt

        self.private_key_encrypted = encrypt(raw, secret_key)

    def private_key(self, secret_key: str) -> str | None:
        from app.crypto import decrypt

        return decrypt(self.private_key_encrypted, secret_key)

    @property
    def masked_private_key(self) -> str:
        """Enough to recognise which key is stored, never enough to use it."""
        return "••••••••" if self.private_key_encrypted else ""

    @property
    def is_usable(self) -> bool:
        return bool(self.public_key and self.private_key_encrypted)

    @classmethod
    def for_provider(cls, church_id: int, provider: str = PROVIDER_TITHELY):
        return db.session.scalar(
            db.select(cls).where(cls.church_id == church_id, cls.provider == provider)
        )


class ExternalGift(TenantScoped, TimestampMixin, db.Model):
    """One recorded gift, copied from the provider."""

    __tablename__ = "external_gift"
    __table_args__ = (
        # Idempotency. A sync that runs twice must not double the totals.
        UniqueConstraint(
            "church_id", "provider", "provider_txn_id", name="uq_external_gift_txn"
        ),
        CheckConstraint(f"match_status IN ({_MATCH_LIST})", name="ck_external_gift_match_status"),
        Index("ix_gift_church_person_date", "church_id", "person_id", "received_on"),
        Index("ix_gift_church_date", "church_id", "received_on"),
        Index("ix_gift_church_unmatched", "church_id", "match_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    person: Mapped[Optional["Person"]] = relationship()  # noqa: F821

    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PROVIDER_TITHELY
    )
    provider_txn_id: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_donor_ref: Mapped[Optional[str]] = mapped_column(String(120))

    # Whatever the donor gave us at the provider, kept so a human clearing the
    # review queue can recognise the person without opening Tithely.
    donor_name: Mapped[Optional[str]] = mapped_column(String(160))
    donor_email: Mapped[Optional[str]] = mapped_column(String(255))

    # Integer cents. Floats and money do not belong in the same sentence.
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # The provider's fund string, not a foreign key. We do not own funds and
    # should not pretend a church's fund list is ours to model.
    fund_name: Mapped[Optional[str]] = mapped_column(String(160))
    method: Mapped[Optional[str]] = mapped_column(String(40))
    received_on: Mapped[date] = mapped_column(Date, nullable=False)

    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MATCH_UNMATCHED
    )
    matched_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )

    synced_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    raw: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ExternalGift {self.amount_display} {self.received_on} {self.match_status}>"

    @property
    def amount_display(self) -> str:
        return f"${self.amount_cents / 100:,.2f}"

    @property
    def match_label(self) -> str:
        return MATCH_LABELS.get(self.match_status, self.match_status.title())

    def attach(self, person, user=None) -> None:
        self.person_id = person.id
        self.match_status = MATCH_MATCHED
        self.matched_by_user_id = getattr(user, "id", None)

    def set_aside(self, user=None) -> None:
        """Not a person at this church: a business, an anonymous gift, a test."""
        self.person_id = None
        self.match_status = MATCH_IGNORED
        self.matched_by_user_id = getattr(user, "id", None)

    # -- lookups ------------------------------------------------------------

    @classmethod
    def get_for_church(cls, church_id: int, gift_id: int) -> "ExternalGift | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == gift_id, cls.church_id == church_id)
        )

    @classmethod
    def unmatched(cls, church_id: int, limit: int = 100):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.match_status == MATCH_UNMATCHED)
            .order_by(cls.received_on.desc(), cls.id.desc())
            .limit(limit)
        )

    @classmethod
    def unmatched_count(cls, church_id: int) -> int:
        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.match_status == MATCH_UNMATCHED
            )
        ) or 0

    @classmethod
    def for_person(cls, church_id: int, person_id: int, limit: int = 24):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.received_on.desc(), cls.id.desc())
            .limit(limit)
        )

    @classmethod
    def person_summary(cls, church_id: int, person_id: int) -> dict:
        row = db.session.execute(
            db.select(
                func.count(cls.id),
                func.coalesce(func.sum(cls.amount_cents), 0),
                func.min(cls.received_on),
                func.max(cls.received_on),
            ).where(cls.church_id == church_id, cls.person_id == person_id)
        ).one()
        count, total, first, last = row
        return {
            "count": count or 0,
            "total_cents": int(total or 0),
            "first_on": first,
            "last_on": last,
        }

    @classmethod
    def month_to_date_cents(cls, church_id: int, today: date | None = None) -> int:
        today = today or utcnow().date()
        start = today.replace(day=1)
        return int(
            db.session.scalar(
                db.select(func.coalesce(func.sum(cls.amount_cents), 0)).where(
                    cls.church_id == church_id, cls.received_on >= start
                )
            )
            or 0
        )


class ExternalRecurringGift(TenantScoped, TimestampMixin, db.Model):
    """A standing arrangement at the provider, mirrored.

    This is the table behind "Giving that stopped". The index on
    `(church_id, status, last_success_on)` is what makes that card one query
    rather than a scan.
    """

    __tablename__ = "external_recurring_gift"
    __table_args__ = (
        UniqueConstraint(
            "church_id", "provider", "provider_ref",
            name="uq_external_recurring_gift_ref",
        ),
        Index("ix_recurring_church_status_seen", "church_id", "status", "last_success_on"),
        Index("ix_recurring_church_person", "church_id", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    person: Mapped[Optional["Person"]] = relationship()  # noqa: F821

    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PROVIDER_TITHELY
    )
    provider_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_donor_ref: Mapped[Optional[str]] = mapped_column(String(120))

    donor_name: Mapped[Optional[str]] = mapped_column(String(160))
    donor_email: Mapped[Optional[str]] = mapped_column(String(255))

    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RECURRING_ACTIVE)

    next_charge_on: Mapped[Optional[date]] = mapped_column(Date)
    last_success_on: Mapped[Optional[date]] = mapped_column(Date)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    synced_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    raw: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ExternalRecurringGift {self.amount_display} {self.frequency} {self.status}>"

    @property
    def amount_display(self) -> str:
        return f"${self.amount_cents / 100:,.2f}"

    @property
    def expected_interval_days(self) -> int:
        return FREQUENCY_DAYS.get(self.frequency, 31)

    @property
    def days_since_last_gift(self) -> int | None:
        if self.last_success_on is None:
            return None
        return (utcnow().date() - self.last_success_on).days

    @property
    def has_stopped(self) -> bool:
        """Past the expected interval plus a grace period.

        Grace matters. A card that failed on the 1st and succeeded on the 4th
        is a payment system doing its job, not a person leaving. Three weeks of
        silence past a monthly rhythm is a person.
        """
        if self.status != RECURRING_ACTIVE:
            return False
        days = self.days_since_last_gift
        if days is None:
            return False
        return days > self.expected_interval_days + LAPSE_GRACE_DAYS

    @property
    def stopped_reason(self) -> str | None:
        if not self.has_stopped:
            return None
        return (
            f"{self.amount_display} {self.frequency}, nothing since "
            f"{self.last_success_on:%B %-d} ({self.days_since_last_gift} days)"
        )

    @classmethod
    def get_for_church(cls, church_id: int, recurring_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == recurring_id, cls.church_id == church_id)
        )

    @classmethod
    def stopped(cls, church_id: int, limit: int | None = None):
        """Active arrangements that have gone quiet.

        Expressed in SQL rather than by loading everything and filtering, so
        the dashboard card stays one indexed query at any church size. The
        window is per frequency, which is why this is an OR rather than a
        single comparison.
        """
        today = utcnow().date()
        clauses = [
            db.and_(
                cls.frequency == frequency,
                cls.last_success_on
                < today - timedelta(days=days + LAPSE_GRACE_DAYS),
            )
            for frequency, days in FREQUENCY_DAYS.items()
        ]
        query = (
            db.select(cls)
            .where(
                cls.church_id == church_id,
                cls.status == RECURRING_ACTIVE,
                cls.last_success_on.is_not(None),
                db.or_(*clauses),
            )
            .order_by(cls.last_success_on)
        )
        return query.limit(limit) if limit else query

    @classmethod
    def stopped_count(cls, church_id: int) -> int:
        return len(list(db.session.scalars(cls.stopped(church_id))))

    @classmethod
    def for_person(cls, church_id: int, person_id: int):
        return (
            db.select(cls)
            .where(cls.church_id == church_id, cls.person_id == person_id)
            .order_by(cls.status, cls.id)
        )
