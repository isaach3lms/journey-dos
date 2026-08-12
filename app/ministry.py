"""
Phase 3: what a church needs the week it starts holding services.

Everything here carries church_id like the rest of the schema. Times are stored
as aware UTC and rendered in the church's timezone at the edge, never the other
way around.
"""

import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import UniqueConstraint

from .extensions import UTCDateTime, db, utcnow

# --------------------------------------------------------------------------
# Serving teams
# --------------------------------------------------------------------------


class Team(db.Model):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("church_id", "name", name="uq_team_name"),)

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(300))
    # Kids teams require a background check before anyone is scheduled.
    requires_clearance = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    memberships = db.relationship(
        "TeamMembership", back_populates="team", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def member_count(self) -> int:
        return len(self.memberships)


class TeamMembership(db.Model):
    __tablename__ = "team_memberships"
    __table_args__ = (UniqueConstraint("team_id", "person_id", name="uq_team_member"),)

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    role = db.Column(db.String(60), default="Volunteer", nullable=False)
    is_leader = db.Column(db.Boolean, default=False, nullable=False)
    cleared_at = db.Column(UTCDateTime)  # background check date, kids teams only
    joined_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    team = db.relationship("Team", back_populates="memberships")
    person = db.relationship("Person", backref=db.backref("team_memberships", lazy="selectin"))

    @property
    def needs_clearance(self) -> bool:
        return self.team.requires_clearance and self.cleared_at is None


# --------------------------------------------------------------------------
# Services and the run sheet
# --------------------------------------------------------------------------

ELEMENT_TYPES = [
    "Welcome",
    "Worship",
    "Announcements",
    "Giving",
    "Message",
    "Response",
    "Prayer",
    "Video",
    "Transition",
    "Dismissal",
]


class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    name = db.Column(db.String(120), default="Sunday Gathering", nullable=False)
    starts_at = db.Column(UTCDateTime, nullable=False, index=True)
    notes = db.Column(db.Text)
    headcount = db.Column(db.Integer)
    kids_count = db.Column(db.Integer)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    elements = db.relationship(
        "ServiceElement",
        back_populates="service",
        order_by="ServiceElement.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    assignments = db.relationship(
        "ServiceAssignment",
        back_populates="service",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def local_start(self, tzname: str):
        return self.starts_at.astimezone(ZoneInfo(tzname))

    @property
    def total_minutes(self) -> int:
        return sum(element.minutes for element in self.elements)

    def running_times(self, tzname: str):
        """Return [(element, clock_time_string)] so the run sheet shows real
        times, not just durations. Recomputed on every render, never stored,
        so moving one element re-times the whole service."""
        clock = self.local_start(tzname)
        rows = []
        for element in self.elements:
            rows.append((element, clock.strftime("%-I:%M %p").lower()))
            clock = clock + timedelta(minutes=element.minutes)
        return rows

    @property
    def is_past(self) -> bool:
        return self.starts_at < utcnow()


class ServiceElement(db.Model):
    __tablename__ = "service_elements"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    position = db.Column(db.Integer, default=0, nullable=False)
    kind = db.Column(db.String(40), default="Worship", nullable=False)
    title = db.Column(db.String(160), nullable=False)
    minutes = db.Column(db.Integer, default=5, nullable=False)
    details = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey("people.id"))

    service = db.relationship("Service", back_populates="elements")
    owner = db.relationship("Person")


class ServiceAssignment(db.Model):
    """Who is serving, in which role, and whether they have confirmed."""

    __tablename__ = "service_assignments"
    __table_args__ = (
        UniqueConstraint("service_id", "person_id", "role", name="uq_service_assignment"),
    )

    STATUSES = ("invited", "confirmed", "declined")

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"))
    role = db.Column(db.String(60), default="Volunteer", nullable=False)
    status = db.Column(db.String(20), default="invited", nullable=False)
    responded_at = db.Column(UTCDateTime)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    service = db.relationship("Service", back_populates="assignments")
    person = db.relationship("Person", backref=db.backref("assignments", lazy="selectin"))
    team = db.relationship("Team")


# --------------------------------------------------------------------------
# Kids check in
# --------------------------------------------------------------------------


class Household(db.Model):
    """Kids belong to a household, not to a Person record, because the adult
    who drops off is not always the adult who picks up."""

    __tablename__ = "households"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=False)  # lookup key at the kiosk
    guardian_id = db.Column(db.Integer, db.ForeignKey("people.id"))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    children = db.relationship(
        "Child", back_populates="household", cascade="all, delete-orphan", lazy="selectin"
    )
    guardian = db.relationship("Person")

    @property
    def phone_last4(self) -> str:
        digits = "".join(c for c in (self.phone or "") if c.isdigit())
        return digits[-4:]


class Child(db.Model):
    __tablename__ = "children"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id"), nullable=False, index=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), default="", nullable=False)
    birthdate = db.Column(db.Date)
    room = db.Column(db.String(60), default="Kids", nullable=False)
    allergies = db.Column(db.String(300))
    notes = db.Column(db.String(300))
    is_active_record = db.Column(db.Boolean, default=True, nullable=False)

    household = db.relationship("Household", back_populates="children")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        if not self.birthdate:
            return None
        today = utcnow().date()
        return (
            today.year
            - self.birthdate.year
            - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        )


class CheckIn(db.Model):
    """One row per child per service. The security code is the whole point:
    nobody leaves with a child unless the code on their tag matches."""

    __tablename__ = "check_ins"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), index=True)
    child_id = db.Column(db.Integer, db.ForeignKey("children.id"), nullable=False, index=True)
    code = db.Column(db.String(4), nullable=False)
    room = db.Column(db.String(60), default="Kids", nullable=False)
    checked_in_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    checked_in_by = db.Column(db.String(120))
    checked_out_at = db.Column(UTCDateTime)
    checked_out_by = db.Column(db.String(120))

    child = db.relationship("Child")
    service = db.relationship("Service")

    @property
    def is_open(self) -> bool:
        return self.checked_out_at is None


def new_security_code() -> str:
    return f"{random.randint(0, 999):03d}"


# --------------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------------

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    day_of_week = db.Column(db.String(20), default="Wednesday", nullable=False)
    meeting_time = db.Column(db.String(30), default="6:30 pm", nullable=False)
    location = db.Column(db.String(160))
    leader_id = db.Column(db.Integer, db.ForeignKey("people.id"))
    capacity = db.Column(db.Integer, default=12, nullable=False)
    is_open = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    leader = db.relationship("Person", foreign_keys=[leader_id])
    memberships = db.relationship(
        "GroupMembership", back_populates="group", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def member_count(self) -> int:
        return len([m for m in self.memberships if m.status == "joined"])

    @property
    def spots_left(self) -> int:
        return max(self.capacity - self.member_count, 0)

    @property
    def has_room(self) -> bool:
        return self.is_open and self.spots_left > 0


class GroupMembership(db.Model):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "person_id", name="uq_group_member"),)

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("people.id"), nullable=False, index=True)
    status = db.Column(db.String(20), default="joined", nullable=False)  # joined, left
    joined_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    group = db.relationship("Group", back_populates="memberships")
    person = db.relationship("Person", backref=db.backref("group_memberships", lazy="selectin"))


# --------------------------------------------------------------------------
# Announcements
# --------------------------------------------------------------------------

AUDIENCES = {
    "everyone": "Everyone in the system",
    "launch_team": "Launch team and beyond",
    "serving": "People on a serving team",
    "groups": "People in a group",
}


class Announcement(db.Model):
    """Staff to congregation. Posts to the member app immediately and can also
    go out by email. Not a chat system, on purpose."""

    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(40), default="everyone", nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("people.id"))
    is_pinned = db.Column(db.Boolean, default=False, nullable=False)
    published_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    emailed_at = db.Column(UTCDateTime)
    email_count = db.Column(db.Integer, default=0, nullable=False)

    author = db.relationship("Person")


def audience_query(church_id: int, audience: str):
    """Resolve an audience key to a list of Person records."""
    from .models import Person, Stage, congregation

    base = congregation(church_id)

    if audience == "launch_team":
        stage = Stage.query.filter_by(church_id=church_id, name="Launch team").first()
        if not stage:
            return base.all()
        allowed = [
            s.id
            for s in Stage.query.filter(
                Stage.church_id == church_id, Stage.position >= stage.position
            ).all()
        ]
        return base.filter(Person.stage_id.in_(allowed)).all()

    if audience == "serving":
        ids = [m.person_id for m in TeamMembership.query.filter_by(church_id=church_id).all()]
        return base.filter(Person.id.in_(ids or [-1])).all()

    if audience == "groups":
        ids = [
            m.person_id
            for m in GroupMembership.query.filter_by(church_id=church_id, status="joined").all()
        ]
        return base.filter(Person.id.in_(ids or [-1])).all()

    return base.all()


def announcements_for(person):
    """Every announcement whose audience includes this person."""
    everything = (
        Announcement.query.filter_by(church_id=person.church_id)
        .order_by(Announcement.is_pinned.desc(), Announcement.published_at.desc())
        .all()
    )
    visible = []
    cache = {}
    for item in everything:
        if item.audience not in cache:
            cache[item.audience] = {p.id for p in audience_query(person.church_id, item.audience)}
        if person.id in cache[item.audience]:
            visible.append(item)
    return visible
