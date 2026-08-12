"""
Data model.

Every table carries church_id from day one. This instance serves one church,
but the schema is multi-tenant so church number two is a row, not a migration.
"""

import hashlib
import secrets
from datetime import timedelta

from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import UTCDateTime, db, utcnow


class Church(db.Model):
    __tablename__ = "churches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    timezone = db.Column(db.String(60), default="America/Chicago", nullable=False)
    tithely_form_id = db.Column(db.String(120))
    tithely_give_url = db.Column(db.String(300))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    stages = db.relationship(
        "Stage", back_populates="church", order_by="Stage.position", lazy="selectin"
    )
    people = db.relationship("Person", back_populates="church", lazy="dynamic")


class Stage(db.Model):
    """A step on the discipleship journey. Configurable per church."""

    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("church_id", "position", name="uq_stage_position"),)

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    # Days a person can sit in this stage before the system flags them.
    stuck_after_days = db.Column(db.Integer, default=30, nullable=False)
    description = db.Column(db.String(300))

    church = db.relationship("Church", back_populates="stages")


class Person(db.Model, UserMixin):
    """One record per human. Staff, member, guest, and launch team are all
    people. Role controls access. password_hash is null until they claim an
    account, so a connect card can create a person with no login."""

    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("church_id", "email", name="uq_person_email"),
        Index("ix_person_stage", "church_id", "stage_id"),
    )

    # "support" is the vendor account: full staff access, invisible to every
    # congregation report and audience. A consultant who administers the system
    # is not a person this church is trying to disciple, and letting them sit
    # in the people count quietly corrupts every number on the dashboard.
    ROLES = ("member", "leader", "staff", "admin", "support")
    STAFF_ROLES = ("staff", "admin", "support")
    HIDDEN_ROLES = ("support",)

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), default="", nullable=False)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(40))
    birthday = db.Column(db.Date)

    role = db.Column(db.String(20), default="member", nullable=False)
    password_hash = db.Column(db.String(255))
    is_active_record = db.Column(db.Boolean, default=True, nullable=False)

    stage_id = db.Column(db.Integer, db.ForeignKey("stages.id"), index=True)
    stage_since = db.Column(UTCDateTime, default=utcnow, nullable=False)

    source = db.Column(db.String(60), default="manual", nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    last_contact_at = db.Column(UTCDateTime)

    church = db.relationship("Church", back_populates="people")
    stage = db.relationship("Stage")
    stage_events = db.relationship(
        "StageEvent",
        back_populates="person",
        foreign_keys="StageEvent.person_id",
        order_by="StageEvent.occurred_at.desc()",
        cascade="all, delete-orphan",
    )
    interactions = db.relationship(
        "Interaction",
        back_populates="person",
        foreign_keys="Interaction.person_id",
        order_by="Interaction.occurred_at.desc()",
        cascade="all, delete-orphan",
    )

    # --- identity -------------------------------------------------------
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return "".join(p[0].upper() for p in parts)[:2]

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)

    @property
    def is_staff(self) -> bool:
        return self.role in self.STAFF_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "support")

    @property
    def is_hidden(self) -> bool:
        return self.role in self.HIDDEN_ROLES

    # --- journey --------------------------------------------------------
    @property
    def days_in_stage(self) -> int:
        return (utcnow() - self.stage_since).days

    @property
    def days_since_contact(self):
        if not self.last_contact_at:
            return None
        return (utcnow() - self.last_contact_at).days

    @property
    def is_stuck(self) -> bool:
        """The core promise of the product: nobody sits unnoticed."""
        if not self.stage:
            return False
        return self.days_in_stage > self.stage.stuck_after_days

    def move_to_stage(self, stage: "Stage", actor=None, note: str = "") -> None:
        if self.stage_id == stage.id:
            return
        self.stage_events.append(
            StageEvent(
                church_id=self.church_id,
                from_stage_id=self.stage_id,
                to_stage_id=stage.id,
                actor_id=getattr(actor, "id", None),
                note=note,
            )
        )
        self.stage_id = stage.id
        self.stage_since = utcnow()

    def log_contact(self, kind: str, summary: str, actor=None) -> None:
        self.interactions.append(
            Interaction(
                church_id=self.church_id,
                kind=kind,
                summary=summary,
                actor_id=getattr(actor, "id", None),
            )
        )
        self.last_contact_at = utcnow()


class StageEvent(db.Model):
    """Immutable log of movement. This is what makes the journey auditable and
    what the stuck report is calculated against."""

    __tablename__ = "stage_events"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    from_stage_id = db.Column(db.Integer, db.ForeignKey("stages.id"))
    to_stage_id = db.Column(db.Integer, db.ForeignKey("stages.id"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("people.id"))
    note = db.Column(db.String(400))
    occurred_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    person = db.relationship("Person", foreign_keys=[person_id], back_populates="stage_events")
    from_stage = db.relationship("Stage", foreign_keys=[from_stage_id])
    to_stage = db.relationship("Stage", foreign_keys=[to_stage_id])


class Interaction(db.Model):
    """Every touch: call, text, email, conversation, automated sequence."""

    __tablename__ = "interactions"

    KINDS = ("call", "text", "email", "in person", "automated")

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("people.id"))
    kind = db.Column(db.String(30), default="call", nullable=False)
    summary = db.Column(db.String(600), nullable=False)
    occurred_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    person = db.relationship("Person", foreign_keys=[person_id], back_populates="interactions")
    actor = db.relationship("Person", foreign_keys=[actor_id])


class GivingRecord(db.Model):
    """Placeholder for Tithely reconciliation. Populated by CSV import now and
    by the Tithely API once keys are issued. external_id is the Tithely
    transaction id and is unique per church so re-imports do not duplicate."""

    __tablename__ = "giving_records"
    __table_args__ = (
        UniqueConstraint("church_id", "external_id", name="uq_gift_external"),
    )

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), index=True)
    external_id = db.Column(db.String(120))
    donor_name = db.Column(db.String(160))
    donor_email = db.Column(db.String(200))
    amount_cents = db.Column(db.Integer, nullable=False)
    fund = db.Column(db.String(80), default="General", nullable=False)
    method = db.Column(db.String(40), default="online", nullable=False)
    is_recurring = db.Column(db.Boolean, default=False, nullable=False)
    given_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    imported_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    @property
    def amount(self) -> float:
        return self.amount_cents / 100.0


class Enrollment(db.Model):
    """A person's position inside an automated sequence. Sequences themselves
    live in app/sequences.py as data. Only state lives here."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("church_id", "person_id", "sequence_key", name="uq_enrollment"),
    )

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    sequence_key = db.Column(db.String(60), nullable=False)

    enrolled_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    last_step_sent = db.Column(db.Integer, default=-1, nullable=False)
    last_sent_at = db.Column(UTCDateTime)
    completed_at = db.Column(UTCDateTime)
    stopped_at = db.Column(UTCDateTime)
    stop_reason = db.Column(db.String(160))

    person = db.relationship("Person", backref=db.backref("enrollments", lazy="selectin"))

    @property
    def status(self) -> str:
        if self.stopped_at:
            return "stopped"
        if self.completed_at:
            return "finished"
        return "running"


def giving_totals(church_id: int, since=None):
    """Totals for the staff giving view. Cents in, cents out."""
    query = GivingRecord.query.filter_by(church_id=church_id)
    if since:
        query = query.filter(GivingRecord.given_at >= since)
    records = query.all()
    by_fund = {}
    for record in records:
        by_fund[record.fund] = by_fund.get(record.fund, 0) + record.amount_cents
    return {
        "total_cents": sum(r.amount_cents for r in records),
        "gift_count": len(records),
        "donor_count": len({r.person_id or r.donor_email or r.donor_name for r in records}),
        "recurring_cents": sum(r.amount_cents for r in records if r.is_recurring),
        "by_fund": sorted(by_fund.items(), key=lambda item: item[1], reverse=True),
        "records": sorted(records, key=lambda r: r.given_at, reverse=True),
    }


def congregation(church_id: int):
    """Everyone this church is actually trying to know and disciple. Use this
    for counts, reports, audiences, and pickers. Never use a bare Person query
    for those, or vendor accounts leak into the numbers."""
    return Person.query.filter(
        Person.church_id == church_id,
        Person.is_active_record.is_(True),
        Person.role.notin_(Person.HIDDEN_ROLES),
    )


def stage_summary(church_id: int):
    """Counts and stuck counts per stage. Drives the journey rail."""
    stages = (
        Stage.query.filter_by(church_id=church_id).order_by(Stage.position).all()
    )
    out = []
    for stage in stages:
        people = congregation(church_id).filter(Person.stage_id == stage.id).all()
        cutoff = utcnow() - timedelta(days=stage.stuck_after_days)
        stuck = [p for p in people if p.stage_since < cutoff]
        out.append({"stage": stage, "count": len(people), "stuck": len(stuck)})
    return out


class AccessToken(db.Model):
    """One time links for claiming an account and resetting a password.

    Only the hash is stored. If the database leaks, the links in it are dead.
    """

    __tablename__ = "access_tokens"

    PURPOSES = ("claim", "reset")

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    purpose = db.Column(db.String(20), default="claim", nullable=False)
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)
    expires_at = db.Column(UTCDateTime, nullable=False)
    used_at = db.Column(UTCDateTime)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    person = db.relationship("Person")

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > utcnow()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token(person, purpose: str = "claim", hours: int = 48):
    """Create a single use link token. Returns (raw_token, record). The raw
    value is never stored and never logged, so it exists only in the email."""
    raw = secrets.token_urlsafe(32)
    # Any older unused token for the same purpose is retired, so a forwarded
    # email from last week cannot be used after a new link is requested.
    for stale in AccessToken.query.filter_by(
        church_id=person.church_id, person_id=person.id, purpose=purpose, used_at=None
    ).all():
        stale.used_at = utcnow()

    record = AccessToken(
        church_id=person.church_id,
        person_id=person.id,
        purpose=purpose,
        token_hash=hash_token(raw),
        expires_at=utcnow() + timedelta(hours=hours),
    )
    db.session.add(record)
    return raw, record


def consume_token(raw: str, purpose: str):
    """Return the person a valid token belongs to, or None. The token is spent
    on success, so a link works exactly once."""
    if not raw:
        return None
    record = AccessToken.query.filter_by(token_hash=hash_token(raw), purpose=purpose).first()
    if not record or not record.is_valid:
        return None
    record.used_at = utcnow()
    return record.person
