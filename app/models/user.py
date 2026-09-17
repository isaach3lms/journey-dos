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
from sqlalchemy import (
    false,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
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

    # The pastoral record, when one exists. Nullable because most staff
    # logins are also people on the roster but some are not, and most
    # people on the roster will never have a login.
    # `use_alter` breaks a genuine cycle: user.person_id points at person, and
    # person.owner_user_id points back at user. Without it SQLAlchemy cannot
    # order CREATE or DROP for either table, and SQLite, which has no ALTER,
    # gives up entirely. Marking one side as "add this constraint afterwards"
    # is the standard resolution and costs nothing at run time.
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL", use_alter=True,
                   name="fk_user_person_id_person"),
        index=True,
    )

    is_active_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Bumped whenever the password changes. It is part of the session cookie,
    # so every cookie minted under the old password stops resolving. Without
    # it, someone who reset their password because a device was stolen would
    # find the thief still signed in on that device.
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    # Null means nobody has proved they control this address. Distinct from
    # `is_active_account`, which means a staff member switched them off: those
    # are different states and the messages a person sees differ too.
    #
    # Defaults to verified, and the direction of that default is the decision.
    # Every path that creates a user except self-registration involves a staff
    # member vouching for the address, which is a stronger signal than a click
    # in an inbox. So the one route that cannot vouch, `auth.join`, clears this
    # explicitly, rather than every other path having to remember to set it.
    #
    # Null by default, which is fail-closed. Defaulting to verified would mean
    # any future code path that creates a user and forgets to clear the flag
    # hands out a confirmed account, and that failure is silent. The cost is
    # that every place which legitimately vouches for an address has to say so:
    # `flask create-user`, the tenant seeds, and the test fixtures all call
    # `mark_verified`. A staff member typing an address is a stronger signal
    # than a click in an inbox, so that is the right place for it.
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # Set when staff hand out a temporary password. Until the person chooses
    # their own, every page redirects them to the change screen. A temporary
    # password that stays in use is just a shared password with a nicer name.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
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

        The session version is here for the same reason: it is the only thing
        that lets a password change invalidate sessions that already exist.
        """
        return f"{self.church_id}:{self.id}:{self.session_version}"

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
        # Every existing session is now invalid. This is what makes a reset
        # useful to somebody whose device was taken.
        self.session_version = (self.session_version or 1) + 1

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


    # -- the link to the pastoral record ------------------------------------

    @property
    def person(self):
        """This user's Person row, or None.

        Deliberately a lookup rather than a relationship. A `person_id` from
        another church would be a data error, and a relationship would happily
        load it; this goes through the tenant-scoped accessor so it cannot.
        """
        if not self.person_id:
            return None
        from app.models.person import Person

        return Person.get_for_church(self.church_id, self.person_id)

    def link_person_by_email(self) -> bool:
        """Attach the roster record with the same address, if there is one.

        Matching on email is imperfect and deliberately not automatic anywhere
        a mistake would be costly. Here the worst case is that a member sees an
        empty Home screen until staff link them by hand, which is recoverable.
        An address held by two people is refused rather than resolved, because
        the failure there is showing one spouse the other's record.
        """
        from app.models.person import Person

        if self.person_id:
            return False

        matches = list(
            db.session.scalars(
                db.select(Person).where(
                    Person.church_id == self.church_id,
                    Person.email == self.email,
                    Person.is_archived.is_(False),
                )
            )
        )
        # Households share addresses, so this is not an identifier. Linking a
        # login to the wrong spouse would show one person the other's record.
        if len(matches) != 1:
            return False
        self.person_id = matches[0].id
        return True


    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    def mark_verified(self) -> None:
        if self.email_verified_at is None:
            self.email_verified_at = utcnow()


    @property
    def first_name(self) -> str:
        """Best effort from the single name field a signup form collects."""
        return (self.name or "").strip().split(" ")[0] or "Friend"

    @property
    def last_name(self) -> str:
        parts = (self.name or "").strip().split()
        return parts[-1] if len(parts) > 1 else ""


    # -- managing accounts --------------------------------------------------

    @classmethod
    def for_church(cls, church_id: int, include_inactive: bool = True):
        query = db.select(cls).where(cls.church_id == church_id)
        if not include_inactive:
            query = query.where(cls.is_active_account.is_(True))
        return query.order_by(cls.role.desc(), cls.name)

    @classmethod
    def get_for_church(cls, church_id: int, user_id: int) -> "User | None":
        return db.session.scalar(
            db.select(cls).where(cls.id == user_id, cls.church_id == church_id)
        )

    @classmethod
    def active_staff_count(cls, church_id: int, excluding: int | None = None) -> int:
        """How many working staff logins remain.

        The number that decides whether a demotion or a deactivation would
        lock the church out of its own data with nobody able to undo it.
        """
        from sqlalchemy import func as sa_func

        query = db.select(sa_func.count(cls.id)).where(
            cls.church_id == church_id,
            cls.role == "staff",
            cls.is_active_account.is_(True),
        )
        if excluding is not None:
            query = query.where(cls.id != excluding)
        return db.session.scalar(query) or 0

    def set_unusable_password(self) -> None:
        """No password anybody knows, including staff.

        Staff invite somebody and the person sets their own password from an
        emailed link. A staff member typing a password means telling it to
        somebody over text, and it is then a password two people know.
        """
        import secrets

        self.set_password(secrets.token_urlsafe(48))


    def issue_temporary_password(self) -> str:
        """Generate a password staff can read aloud, and force a change.

        Staff never choose it. A password a staff member picks becomes a
        password two people know, is usually sent over text, and is often one
        the person already uses elsewhere. This one is random, said out loud
        once, and replaced before they can do anything with it.

        Signs them out everywhere first, for the same reason a reset does: a
        session opened with the old password must not survive.
        """
        import secrets

        # No characters that get misheard or misread down a phone line: no
        # O/0, I/l/1, S/5, Z/2. Staff will be reading this to somebody.
        alphabet = "ABCDEFGHJKMNPQRTUVWXY" "abcdefghjkmnpqrtuvwxy" "346789"
        raw = "-".join(
            "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)
        )

        self.set_password(raw)
        self.must_change_password = True
        self.failed_login_count = 0
        self.locked_until = None
        self.session_version = (self.session_version or 1) + 1
        return raw

    def clear_password_change_requirement(self) -> None:
        self.must_change_password = False
