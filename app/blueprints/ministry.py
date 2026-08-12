from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from ..emails import send_email
from ..extensions import db, utcnow
from ..ministry import (
    AUDIENCES,
    DAYS,
    ELEMENT_TYPES,
    Announcement,
    CheckIn,
    Child,
    Group,
    GroupMembership,
    Household,
    Service,
    ServiceAssignment,
    ServiceElement,
    Team,
    TeamMembership,
    audience_query,
)
from ..models import Church, Person, Stage, congregation
from .staff import staff_only

bp = Blueprint("ministry", __name__, url_prefix="/staff")


def church() -> Church:
    return db.session.get(Church, current_user.church_id)


def tzname() -> str:
    return church().timezone or "America/Chicago"


def to_utc(date_string: str, time_string: str):
    """Wall clock in the church's timezone to aware UTC. Never store local."""
    naive = datetime.strptime(f"{date_string} {time_string}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=ZoneInfo(tzname())).astimezone(ZoneInfo("UTC"))


def move_to_serving(person: Person) -> None:
    """Joining a team is discipleship progress, so the journey reflects it."""
    stage = Stage.query.filter_by(church_id=person.church_id, name="Serving").first()
    if stage and person.stage and person.stage.position < stage.position:
        person.move_to_stage(stage, actor=current_user, note="Joined a serving team")


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


@bp.route("/services", methods=["GET", "POST"])
@staff_only
def services():
    church_id = current_user.church_id

    if request.method == "POST":
        try:
            starts_at = to_utc(request.form["date"], request.form["time"])
        except (KeyError, ValueError):
            flash("Pick a valid date and time.", "error")
            return redirect(url_for("ministry.services"))

        service = Service(
            church_id=church_id,
            name=(request.form.get("name") or "Sunday Gathering").strip(),
            starts_at=starts_at,
        )
        db.session.add(service)
        db.session.flush()

        # A blank run sheet is useless on a Saturday night. Start from the
        # shape of a normal gathering and let them edit it down.
        default_plan = [
            ("Welcome", "Countdown and welcome", 5),
            ("Worship", "Worship set", 20),
            ("Announcements", "Announcements", 4),
            ("Giving", "Giving moment", 3),
            ("Message", "Message", 30),
            ("Response", "Response and prayer", 6),
            ("Dismissal", "Dismissal", 2),
        ]
        for position, (kind, title, minutes) in enumerate(default_plan):
            db.session.add(
                ServiceElement(
                    church_id=church_id,
                    service_id=service.id,
                    position=position,
                    kind=kind,
                    title=title,
                    minutes=minutes,
                )
            )
        db.session.commit()
        return redirect(url_for("ministry.service_detail", service_id=service.id))

    upcoming = (
        Service.query.filter(Service.church_id == church_id, Service.starts_at >= utcnow())
        .order_by(Service.starts_at)
        .all()
    )
    past = (
        Service.query.filter(Service.church_id == church_id, Service.starts_at < utcnow())
        .order_by(Service.starts_at.desc())
        .limit(10)
        .all()
    )
    next_sunday = utcnow().date()
    while next_sunday.weekday() != 6:
        next_sunday += timedelta(days=1)
    return render_template(
        "staff/services.html",
        upcoming=upcoming,
        past=past,
        tzname=tzname(),
        suggested_date=next_sunday.isoformat(),
    )


@bp.route("/services/<int:service_id>", methods=["GET", "POST"])
@staff_only
def service_detail(service_id: int):
    service = Service.query.filter_by(
        id=service_id, church_id=current_user.church_id
    ).first_or_404()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_element":
            last = max([e.position for e in service.elements], default=-1)
            db.session.add(
                ServiceElement(
                    church_id=service.church_id,
                    service_id=service.id,
                    position=last + 1,
                    kind=request.form.get("kind", "Worship"),
                    title=(request.form.get("title") or "Untitled").strip(),
                    minutes=request.form.get("minutes", type=int) or 5,
                    details=(request.form.get("details") or "").strip() or None,
                )
            )

        elif action == "delete_element":
            element = db.session.get(ServiceElement, request.form.get("element_id", type=int))
            if element and element.service_id == service.id:
                db.session.delete(element)

        elif action == "move_element":
            element = db.session.get(ServiceElement, request.form.get("element_id", type=int))
            direction = -1 if request.form.get("direction") == "up" else 1
            if element and element.service_id == service.id:
                ordered = list(service.elements)
                index = ordered.index(element)
                target = index + direction
                if 0 <= target < len(ordered):
                    ordered[index], ordered[target] = ordered[target], ordered[index]
                    for position, item in enumerate(ordered):
                        item.position = position

        elif action == "assign":
            person = Person.query.filter_by(
                id=request.form.get("person_id", type=int), church_id=service.church_id
            ).first_or_404()
            team_id = request.form.get("team_id", type=int)
            role = (request.form.get("role") or "Volunteer").strip()
            exists = ServiceAssignment.query.filter_by(
                service_id=service.id, person_id=person.id, role=role
            ).first()
            if not exists:
                db.session.add(
                    ServiceAssignment(
                        church_id=service.church_id,
                        service_id=service.id,
                        person_id=person.id,
                        team_id=team_id,
                        role=role,
                    )
                )
                move_to_serving(person)

        elif action == "unassign":
            assignment = db.session.get(
                ServiceAssignment, request.form.get("assignment_id", type=int)
            )
            if assignment and assignment.service_id == service.id:
                db.session.delete(assignment)

        elif action == "counts":
            service.headcount = request.form.get("headcount", type=int)
            service.kids_count = request.form.get("kids_count", type=int)
            service.notes = (request.form.get("notes") or "").strip() or None

        elif action == "notify":
            sent = 0
            for assignment in service.assignments:
                if assignment.status != "invited":
                    continue
                local = service.local_start(tzname())
                ok = send_email(
                    to=assignment.person.email,
                    subject=f"You are scheduled for {local:%A, %B %-d}",
                    html=(
                        f"<p>{assignment.person.first_name},</p>"
                        f"<p>You are on the schedule for {service.name} on "
                        f"{local:%A, %B %-d} at {local:%-I:%M %p}, serving as "
                        f"{assignment.role}.</p>"
                        f"<p>Reply to this email if you cannot make it.</p>"
                    ),
                )
                if ok:
                    sent += 1
            flash(f"Notified {sent} volunteers.", "success")

        db.session.commit()
        return redirect(url_for("ministry.service_detail", service_id=service.id))

    teams = Team.query.filter_by(church_id=service.church_id).order_by(Team.name).all()
    people = (
        congregation(service.church_id).order_by(Person.first_name).all()
    )
    return render_template(
        "staff/service_detail.html",
        s=service,
        rows=service.running_times(tzname()),
        tzname=tzname(),
        element_types=ELEMENT_TYPES,
        teams=teams,
        people=people,
    )


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------


@bp.route("/teams", methods=["GET", "POST"])
@staff_only
def teams():
    church_id = current_user.church_id

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = (request.form.get("name") or "").strip()
            if name and not Team.query.filter_by(church_id=church_id, name=name).first():
                db.session.add(
                    Team(
                        church_id=church_id,
                        name=name,
                        description=(request.form.get("description") or "").strip() or None,
                        requires_clearance=bool(request.form.get("requires_clearance")),
                    )
                )
        elif action == "add_member":
            team = Team.query.filter_by(
                id=request.form.get("team_id", type=int), church_id=church_id
            ).first_or_404()
            person = Person.query.filter_by(
                id=request.form.get("person_id", type=int), church_id=church_id
            ).first_or_404()
            if not TeamMembership.query.filter_by(team_id=team.id, person_id=person.id).first():
                db.session.add(
                    TeamMembership(
                        church_id=church_id,
                        team_id=team.id,
                        person_id=person.id,
                        role=(request.form.get("role") or "Volunteer").strip(),
                        is_leader=bool(request.form.get("is_leader")),
                    )
                )
                move_to_serving(person)
        elif action == "clear":
            membership = db.session.get(
                TeamMembership, request.form.get("membership_id", type=int)
            )
            if membership and membership.church_id == church_id:
                membership.cleared_at = utcnow()
        elif action == "remove_member":
            membership = db.session.get(
                TeamMembership, request.form.get("membership_id", type=int)
            )
            if membership and membership.church_id == church_id:
                db.session.delete(membership)
        db.session.commit()
        return redirect(url_for("ministry.teams"))

    return render_template(
        "staff/teams.html",
        teams=Team.query.filter_by(church_id=church_id).order_by(Team.name).all(),
        people=congregation(church_id).order_by(Person.first_name).all(),
    )


# --------------------------------------------------------------------------
# Kids
# --------------------------------------------------------------------------


@bp.route("/kids", methods=["GET", "POST"])
@staff_only
def kids():
    church_id = current_user.church_id

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_household":
            household = Household(
                church_id=church_id,
                name=(request.form.get("household") or "").strip() or "Household",
                phone=(request.form.get("phone") or "").strip(),
            )
            db.session.add(household)
            db.session.flush()
            first = (request.form.get("first_name") or "").strip()
            if first:
                birthdate = request.form.get("birthdate") or ""
                db.session.add(
                    Child(
                        church_id=church_id,
                        household_id=household.id,
                        first_name=first,
                        last_name=(request.form.get("last_name") or "").strip(),
                        birthdate=datetime.strptime(birthdate, "%Y-%m-%d").date()
                        if birthdate
                        else None,
                        room=(request.form.get("room") or "Kids").strip(),
                        allergies=(request.form.get("allergies") or "").strip() or None,
                    )
                )
        elif action == "add_child":
            household = Household.query.filter_by(
                id=request.form.get("household_id", type=int), church_id=church_id
            ).first_or_404()
            birthdate = request.form.get("birthdate") or ""
            db.session.add(
                Child(
                    church_id=church_id,
                    household_id=household.id,
                    first_name=(request.form.get("first_name") or "Child").strip(),
                    last_name=(request.form.get("last_name") or "").strip(),
                    birthdate=datetime.strptime(birthdate, "%Y-%m-%d").date()
                    if birthdate
                    else None,
                    room=(request.form.get("room") or "Kids").strip(),
                    allergies=(request.form.get("allergies") or "").strip() or None,
                )
            )
        elif action == "checkout":
            record = CheckIn.query.filter_by(
                id=request.form.get("checkin_id", type=int), church_id=church_id
            ).first_or_404()
            record.checked_out_at = utcnow()
            record.checked_out_by = f"staff: {current_user.full_name}"
        db.session.commit()
        return redirect(url_for("ministry.kids"))

    households = (
        Household.query.filter_by(church_id=church_id).order_by(Household.name).all()
    )
    open_checkins = (
        CheckIn.query.filter_by(church_id=church_id, checked_out_at=None)
        .order_by(CheckIn.checked_in_at.desc())
        .all()
    )
    recent = (
        CheckIn.query.filter(
            CheckIn.church_id == church_id, CheckIn.checked_out_at.isnot(None)
        )
        .order_by(CheckIn.checked_out_at.desc())
        .limit(15)
        .all()
    )
    kids_team = Team.query.filter_by(church_id=church_id, requires_clearance=True).all()
    uncleared = [m for team in kids_team for m in team.memberships if m.needs_clearance]
    return render_template(
        "staff/kids.html",
        households=households,
        open_checkins=open_checkins,
        recent=recent,
        uncleared=uncleared,
    )


# --------------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------------


@bp.route("/groups", methods=["GET", "POST"])
@staff_only
def groups():
    church_id = current_user.church_id

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            db.session.add(
                Group(
                    church_id=church_id,
                    name=(request.form.get("name") or "Group").strip(),
                    description=(request.form.get("description") or "").strip() or None,
                    day_of_week=request.form.get("day_of_week", "Wednesday"),
                    meeting_time=(request.form.get("meeting_time") or "6:30 pm").strip(),
                    location=(request.form.get("location") or "").strip() or None,
                    leader_id=request.form.get("leader_id", type=int),
                    capacity=request.form.get("capacity", type=int) or 12,
                )
            )
        elif action == "toggle":
            group = Group.query.filter_by(
                id=request.form.get("group_id", type=int), church_id=church_id
            ).first_or_404()
            group.is_open = not group.is_open
        elif action == "remove_member":
            membership = db.session.get(
                GroupMembership, request.form.get("membership_id", type=int)
            )
            if membership and membership.church_id == church_id:
                membership.status = "left"
        db.session.commit()
        return redirect(url_for("ministry.groups"))

    return render_template(
        "staff/groups.html",
        groups=Group.query.filter_by(church_id=church_id).order_by(Group.name).all(),
        people=congregation(church_id).order_by(Person.first_name).all(),
        days=DAYS,
    )


# --------------------------------------------------------------------------
# Announcements
# --------------------------------------------------------------------------


@bp.route("/messages", methods=["GET", "POST"])
@staff_only
def messages():
    church_id = current_user.church_id

    if request.method == "POST":
        action = request.form.get("action")
        if action == "post":
            announcement = Announcement(
                church_id=church_id,
                title=(request.form.get("title") or "").strip() or "Update",
                body=(request.form.get("body") or "").strip(),
                audience=request.form.get("audience", "everyone"),
                author_id=current_user.id,
                is_pinned=bool(request.form.get("is_pinned")),
            )
            db.session.add(announcement)
            db.session.flush()

            if request.form.get("send_email"):
                recipients = audience_query(church_id, announcement.audience)
                paragraphs = "".join(
                    f"<p>{line}</p>" for line in announcement.body.split("\n") if line.strip()
                )
                sent = 0
                for person in recipients:
                    ok = send_email(
                        to=person.email,
                        subject=announcement.title,
                        html=f"<div style='font-family:Inter,Arial,sans-serif'>"
                        f"<p>{person.first_name},</p>{paragraphs}</div>",
                    )
                    if ok:
                        sent += 1
                announcement.emailed_at = utcnow()
                announcement.email_count = sent
                flash(f"Posted and emailed to {sent} people.", "success")
            else:
                flash("Posted to the app.", "success")

        elif action == "delete":
            announcement = Announcement.query.filter_by(
                id=request.form.get("announcement_id", type=int), church_id=church_id
            ).first_or_404()
            db.session.delete(announcement)

        db.session.commit()
        return redirect(url_for("ministry.messages"))

    counts = {key: len(audience_query(church_id, key)) for key in AUDIENCES}
    return render_template(
        "staff/messages.html",
        announcements=Announcement.query.filter_by(church_id=church_id)
        .order_by(Announcement.published_at.desc())
        .all(),
        audiences=AUDIENCES,
        counts=counts,
    )
