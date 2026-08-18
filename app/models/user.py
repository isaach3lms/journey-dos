"""Authentication identity.

A `User` is a login. It is not a person in the discipleship sense. Increment 2
introduces `Person`, which holds the pastoral record: stage, timeline, next
step, household. The two are linked by a nullable `person_id` added in that
increment, not here.

Keeping them apart matters. A church secretary who logs in every day may never
be someone the stuck engine should flag, and a guest who has never logged in
still needs a full pastoral record from the moment they fill out a connect
card. One table forced to be both would make every query about either one
carry conditions about the other.

Email is unique per church, not globally. A person can attend two churches, and
a Between Sundays staff member may hold an account at several. Login already
happens inside a resolved tenant, so scoping the constraint that way is both
correct and invisible to the user.
"""

from __future__ import annotations

import hashlib

# SQLAlchemy evaluates the annotation inside `Mapped[...]` at class-definition
# time, so `from __future__ import annotations` does not defer it the way it
# defers ordinary function annotations. `str | None` therefore needs a Python
# that can evaluate PEP 604 unions at runtime, which means 3.10 or newer.
# `Optional[...]` resolves on every version, so the models do not depend on
# which interpreter happens to be on the machine.
from typing import Optional

from datetime import datetime, timedelta

from flask_login import UserMixin
from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import TenantScoped, TimestampMixin, UTCDateTime, utcnow

# Ordered from least to most authority. Position in this tuple is the
# hierarchy, so `ROLES.index` answers "does this role reach that one".
ROLES = ("member", "leader", "staff")

ROLE_LABELS = {
    "member": "Member",
    "leader": "Leader",
    "staff": "Staff",
}

# Credential stuffing protection. Ten attempts is well past a typo and well
# short of locking out a volunteer who genuinely forgot which password they
# used. The window is short because there is no self-serve reset until the
# outbox ships at increment 4.
MAX_FAILED_LOGINS = 10
LOCKOUT_MINUTES = 15

# Hashing a throwaway password when no user matches keeps the response time
# for "no such account" indistinguishable from "wrong password", so the login
# form cannot be used to discover who attends a church.
def _strongest_available_hash() -> str:
    """Pick the best password hash this interpreter can actually perform.

    Werkzeug defaults to scrypt, but `hashlib.scrypt` only exists when Python
    was linked against an OpenSSL that provides it. Several macOS Python
    builds ship against LibreSSL, where the attribute is simply absent and
    every call to `generate_password_hash` raises AttributeError. Asking
    hashlib what it can do, rather than assuming, means the app runs on the
    machine it is on instead of the machine it was written on.

    The fallback iteration count follows current OWASP guidance for
    PBKDF2-HMAC-SHA256. It is slower to compute than scrypt is to attack, but
    it is a real hash, not a downgrade to something weak.

    Hashes carry their own method, so a password hashed with PBKDF2 here still
    verifies on a machine that has scrypt, and the reverse. Nothing is locked
    to the environment that created it.
    """
    if hasattr(hashlib, "scrypt"):
        return "scrypt"
    return "pbkdf2:sha256:600000"


def _hash_method() -> str:
    """Resolve the hash method for this request.

    The cost is a config value so the strong default is never weakened by
    accident: only TestingConfig lowers it, because a suite that creates a
    dozen users per test should not spend its time in a KDF.
    """
    try:
        from flask import current_app

        configured = current_app.config.get("PASSWORD_HASH_METHOD")
        if configured:
            return configured
    except RuntimeError:
        pass
    return _strongest_available_hash()


_TIMING_DECOY = generate_password_hash("timing-attack-decoy", method="pbkdf2:sha256:1")


class User(UserMixin, TenantScoped, TimestampMixin, db.Model):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("church_id", "email", name="uq_user_church_email"),
        CheckConstraint(
            "role IN ('member', 'leader', 'staff')", name="ck_user_role"
        ),
        Index("ix_user_church_role", "church_id", "role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")

    is_active_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    def __repr__(self) -> str:
        return f"<User {self.email} {self.role} church={self.church_id}>"

    # -- Flask-Login --------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Flask-Login refuses to log in a user for whom this is False."""
        return self.is_active_account and not self.is_locked

    def get_id(self) -> str:
        """Bind the session to the tenant as well as the user.

        Flask-Login stores whatever this returns in the cookie and hands it
        straight back to the user loader. Returning the bare id would let a
        session minted on one church's host be replayed on another's, because
        the loader would have no way to tell the difference. Carrying the
        church id makes that mismatch detectable, and `load_user` rejects it.
        """
        return f"{self.church_id}:{self.id}"

    # -- passwords ----------------------------------------------------------

    def set_password(self, raw: str) -> None:
        if not raw or len(raw) < 12:
            raise ValueError(
                "Passwords must be at least 12 characters. Length is the only "
                "requirement that reliably helps; composition rules mostly "
                "produce predictable substitutions."
            )
        self.password_hash = generate_password_hash(raw, method=_hash_method())
        self.failed_login_count = 0
        self.locked_until = None

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw or "")

    @staticmethod
    def burn_timing_budget(raw: str) -> None:
        """Spend comparable time on a miss as on a hit."""
        check_password_hash(_TIMING_DECOY, raw or "")

    # -- lockout ------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > utcnow()

    def register_failed_login(self) -> None:
        self.failed_login_count += 1
        if self.failed_login_count >= MAX_FAILED_LOGINS:
            self.locked_until = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)

    def register_successful_login(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = utcnow()

    # -- roles --------------------------------------------------------------

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def at_least(self, role: str) -> bool:
        """True when this user's role reaches `role` or exceeds it."""
        return ROLES.index(self.role) >= ROLES.index(role)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role.title())

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"

    # -- lookups ------------------------------------------------------------

    @classmethod
    def by_email(cls, church_id: int, email: str) -> "User | None":
        if not email:
            return None
        return db.session.scalar(
            db.select(cls).where(
                cls.church_id == church_id,
                cls.email == email.strip().lower(),
            )
        )
