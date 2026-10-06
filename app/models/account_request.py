"""Somebody asking to be set up with a login.

The sign-in page used to offer "Create an account", which handed out a login
to anybody who typed an address. This is the replacement: the same person
says who they are and who is in their household, and a human decides.

**It is stored, not just emailed.** The request goes out as an email because
that is how it reaches whoever acts on it, but an email is where a request
goes to die: the one person who received it is on holiday, or it lands under
a hundred others, and the family who asked hears nothing and concludes the
church is not interested. A row means anybody with roster access can see what
is outstanding.

**It is answered, not deleted.** Marking one done records who did it. A
request that vanishes when it is dealt with cannot answer "did anybody ever
get back to the Alvarez family", which is the question that gets asked three
months later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

OPEN = ACCOUNT_OPEN = "open"
DONE = ACCOUNT_DONE = "done"
DECLINED = ACCOUNT_DECLINED = "declined"
STATUSES = (OPEN, DONE, DECLINED)

STATUS_LABELS = {
    OPEN: "Waiting",
    DONE: "Set up",
    DECLINED: "Declined",
}


class AccountRequest(TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "account_request"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_account_request_status",
        ),
        # The only query this table has: one church's requests, waiting ones
        # first, newest first within that.
        Index("ix_account_request_church_status", "church_id", "status",
              "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(40))

    # Free text, both of them, and deliberately so. A household is "my wife
    # Carla and two boys, 7 and 4" far more often than it is a set of fields,
    # and a form that insists on structure gets abandoned or filled with
    # nonsense. A human reads this and types the real records.
    address: Mapped[Optional[str]] = mapped_column(Text)
    household: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OPEN, server_default=OPEN
    )
    handled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    handled_by_name: Mapped[Optional[str]] = mapped_column(String(120))

    # Set if a login was made from this request, so the list can say so and a
    # second click cannot make a second account.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    def resolve(self, status: str, actor=None) -> bool:
        """Mark it dealt with. Caller commits."""
        if status not in (DONE, DECLINED) or self.status == status:
            return False
        self.status = status
        self.handled_at = utcnow()
        self.handled_by_name = getattr(actor, "name", None)
        return True

    def reopen(self) -> bool:
        """Resolved by mistake. Caller commits."""
        if self.status == OPEN:
            return False
        self.status = OPEN
        self.handled_at = None
        self.handled_by_name = None
        return True

    @classmethod
    def for_church(cls, church_id: int, *, include_done: bool = False):
        query = db.select(cls).where(cls.church_id == church_id)
        if not include_done:
            query = query.where(cls.status == OPEN)
        # Waiting first whatever else is shown: the list exists to be cleared.
        return query.order_by(
            (cls.status != OPEN), cls.created_at.desc(), cls.id.desc()
        )

    @classmethod
    def get_for_church(cls, church_id: int, request_id: int):
        return db.session.scalar(
            db.select(cls).where(cls.id == request_id,
                                 cls.church_id == church_id)
        )

    @classmethod
    def open_count(cls, church_id: int) -> int:
        from sqlalchemy import func

        return db.session.scalar(
            db.select(func.count(cls.id)).where(
                cls.church_id == church_id, cls.status == OPEN)
        ) or 0

    def __repr__(self) -> str:
        return f"<AccountRequest {self.full_name!r} {self.status}>"
