"""Services, songs, and teams.

Every id in this module goes through a `get_for_church` accessor. The plan
editor in particular takes four different ids in one screen, which is exactly
the shape of code where one unscoped lookup slips through unnoticed.
"""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from app.content import SERVICES
from app.extensions import db
from app.mail import NotQueued, queue
from app.models import (
    ITEM_KINDS,
    ITEM_HEADER,
    ServiceNeed,
    ServiceTemplateItem,
    ServiceType,
    ServiceTypeNeed,
    build_from_type,
    copy_plan,
    ACCEPTED,
    DECLINED,
    INVITED,
    ITEM_ELEMENT,
    ITEM_SONG,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    Song,
    Team,
    TeamMembership,
    TeamPosition,
)
from app.models.base import utcnow
from app.models.service import STATUS_SENT
from app.music import UnknownKey, key_choices, normalize_key
from app.security import min_role
from app.timeutil import format_local, from_local

bp = Blueprint("services", __name__, url_prefix="/services")


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

@bp.get("/")
@login_required
@min_role("leader")
def index():
    return render_template(
        "services/index.html",
        church=g.church,
        content=SERVICES,
        upcoming=db.session.scalars(Service.upcoming(g.church.id)).all(),
        types=db.session.scalars(ServiceType.for_church(g.church.id)).all(),
        past=db.session.scalars(Service.recent(g.church.id)).all(),
        active="services",
    )


@bp.post("/")
@login_required
@min_role("leader")
def create():
    raw = (request.form.get("starts_at") or "").strip()
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        flash(SERVICES["bad_time"], "error")
        return redirect(url_for("services.index"))

    type_id = request.form.get("service_type_id", type=int)
    service_type = (
        ServiceType.get_for_church(g.church.id, type_id) if type_id else None
    )

    # A new service arrives as a real plan rather than an empty page, which is
    # the single biggest thing this saves a worship leader every week.
    service = build_from_type(
        g.church.id,
        service_type,
        name=(request.form.get("name") or (service_type.name if service_type else "Sunday")).strip(),
        starts_at=from_local(naive, g.church),
    )
    db.session.commit()

    flash(SERVICES["created"].format(name=service.name), "notice")
    return redirect(url_for("services.plan", service_id=service.id))


@bp.get("/<int:service_id>/")
@login_required
@min_role("leader")
def plan(service_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    if service is None:
        abort(404)

    upcoming = db.session.scalars(Service.upcoming(g.church.id, limit=8)).all()
    if service not in upcoming:
        upcoming = sorted(upcoming + [service], key=lambda s: s.starts_at)

    return render_template(
        "services/plan.html",
        upcoming=upcoming,
        church=g.church,
        content=SERVICES,
        service=service,
        songs=db.session.scalars(Song.for_church(g.church.id)).all(),
        teams=db.session.scalars(Team.for_church(g.church.id)).all(),
        timed_items=service.timed_items,
        earlier=db.session.scalars(
            db.select(Service)
            .where(
                Service.church_id == g.church.id,
                Service.id != service.id,
                Service.starts_at < service.starts_at,
            )
            .order_by(Service.starts_at.desc())
            .limit(6)
        ).all(),
        people=db.session.scalars(Person.for_church(g.church.id)).all(),
        keys=key_choices(),
        active="services",
    )


@bp.post("/<int:service_id>/items/")
@login_required
@min_role("leader")
def add_item(service_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    if service is None:
        abort(404)

    kind = (request.form.get("kind") or ITEM_ELEMENT).strip()
    song = None
    title = (request.form.get("title") or "").strip()

    if kind == ITEM_HEADER:
        if not title:
            flash(SERVICES["item_title_required"], "error")
            return redirect(url_for("services.plan", service_id=service.id))
        db.session.add(
            ServiceItem(
                church_id=g.church.id,
                service_id=service.id,
                position=service.next_position(),
                kind=ITEM_HEADER,
                title=title[:200],
            )
        )
        db.session.commit()
        flash(SERVICES["item_added"], "notice")
        return redirect(url_for("services.plan", service_id=service.id))

    if kind == ITEM_SONG:
        song_id = request.form.get("song_id", type=int)
        song = Song.get_for_church(g.church.id, song_id) if song_id else None
        if song is None:
            flash(SERVICES["song_required"], "error")
            return redirect(url_for("services.plan", service_id=service.id))
        title = song.title
    elif not title:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.plan", service_id=service.id))

    try:
        key_override = normalize_key(request.form.get("key_override"))
    except UnknownKey:
        flash(
            SERVICES["key_bad"].format(key=request.form.get("key_override")), "error"
        )
        return redirect(url_for("services.plan", service_id=service.id))

    db.session.add(
        ServiceItem(
            church_id=g.church.id,
            service_id=service.id,
            # Computed rather than taken from the form, so two people adding an
            # item at once cannot collide on the unique constraint.
            position=service.next_position(),
            kind=ITEM_SONG if song else ITEM_ELEMENT,
            title=title[:200],
            song_id=song.id if song else None,
            key_override=key_override,
            minutes=request.form.get("minutes", type=int),
            notes=(request.form.get("notes") or "").strip() or None,
        )
    )
    db.session.commit()

    flash(SERVICES["item_added"], "notice")
    return redirect(url_for("services.plan", service_id=service.id))


@bp.post("/<int:service_id>/items/<int:item_id>/delete/")
@login_required
@min_role("leader")
def delete_item(service_id: int, item_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    item = ServiceItem.get_for_church(g.church.id, item_id)
    if service is None or item is None or item.service_id != service.id:
        abort(404)

    db.session.delete(item)
    db.session.flush()
    service.renumber()
    db.session.commit()

    flash(SERVICES["item_removed"], "notice")
    return redirect(url_for("services.plan", service_id=service.id))


@bp.post("/<int:service_id>/assignments/")
@login_required
@min_role("leader")
def assign(service_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    if service is None:
        abort(404)

    person_id = request.form.get("person_id", type=int)
    person = Person.get_for_church(g.church.id, person_id) if person_id else None
    if person is None:
        abort(400)

    position = None
    position_id = request.form.get("position_id", type=int)
    if position_id:
        position = db.session.scalar(
            db.select(TeamPosition).where(
                TeamPosition.id == position_id,
                TeamPosition.church_id == g.church.id,
            )
        )
        if position is None:
            abort(400)

    existing = db.session.scalar(
        db.select(ServiceAssignment).where(
            ServiceAssignment.service_id == service.id,
            ServiceAssignment.person_id == person.id,
            ServiceAssignment.position_id == (position.id if position else None),
        )
    )
    if existing is not None:
        flash(SERVICES["already_asked"].format(name=person.full_name), "error")
        return redirect(url_for("services.plan", service_id=service.id))

    db.session.add(
        ServiceAssignment(
            church_id=g.church.id,
            service_id=service.id,
            person_id=person.id,
            position_id=position.id if position else None,
            # Copied, not looked up later. A renamed or deleted position must
            # not turn "Drums, 12 March" into "None, 12 March".
            position_name=position.name if position else None,
            status=INVITED,
        )
    )
    db.session.commit()

    flash(
        SERVICES["assigned"].format(
            name=person.full_name, position=position.name if position else "serve"
        ),
        "notice",
    )
    return redirect(url_for("services.plan", service_id=service.id))


@bp.post("/<int:service_id>/assignments/<int:assignment_id>/delete/")
@login_required
@min_role("leader")
def unassign(service_id: int, assignment_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    assignment = ServiceAssignment.get_for_church(g.church.id, assignment_id)
    if service is None or assignment is None or assignment.service_id != service.id:
        abort(404)

    db.session.delete(assignment)
    db.session.commit()

    flash(SERVICES["unassigned"], "notice")
    return redirect(url_for("services.plan", service_id=service.id))


@bp.post("/<int:service_id>/send/")
@login_required
@min_role("leader")
def send_plan(service_id: int):
    """Email everyone on the plan. Queued, never sent inside this request."""
    service = Service.get_for_church(g.church.id, service_id)
    if service is None:
        abort(404)

    if not service.assignments:
        flash(SERVICES["send_nobody"], "error")
        return redirect(url_for("services.plan", service_id=service.id))

    when = format_local(service.starts_at, g.church, "%A %-d %B, %-I:%M%p")
    plan_lines = "\n".join(
        f"  {item.position}. {item.title}"
        + (f" ({item.key})" if item.key else "")
        + (f" [{item.minutes} min]" if item.minutes else "")
        for item in service.items
    ) or "  (nothing in the plan yet)"

    queued = 0
    for assignment in service.assignments:
        person = assignment.person
        if person is None or not person.email:
            continue
        try:
            message = queue(
                church_id=g.church.id,
                # Serving, not marketing. Somebody who left the newsletter
                # still needs to know they are on the plan on Sunday.
                category="group",
                subject=SERVICES["email_subject"].format(
                    service=service.name, date=when
                ),
                body_text=SERVICES["email_body"].format(
                    name=person.first_name,
                    service=service.name,
                    date=when,
                    position=assignment.role_name,
                    plan=plan_lines,
                    church=g.church.name,
                ),
                person=person,
                # One send per person per service per calendar day. A
                # double-clicked button must not email a volunteer twice, and
                # a genuine resend next week, after the plan changed, still
                # goes out. Keying on plan_sent_at would defeat the first
                # case, because the first send sets it before the second
                # request reads it.
                dedupe_key=f"service:{service.id}:person:{person.id}:"
                f"{utcnow().date().isoformat()}",
            )
        except NotQueued:
            continue
        if message is not None:
            person.ensure_unsubscribe_token()
            queued += 1

    service.status = STATUS_SENT
    service.plan_sent_at = utcnow()
    db.session.commit()

    flash(SERVICES["sent"].format(count=queued), "notice")
    return redirect(url_for("services.plan", service_id=service.id))


# ---------------------------------------------------------------------------
# Songs
# ---------------------------------------------------------------------------

@bp.get("/songs/")
@login_required
@min_role("leader")
def songs():
    return render_template(
        "services/songs.html",
        church=g.church,
        content=SERVICES,
        songs=db.session.scalars(Song.for_church(g.church.id)).all(),
        used=Song.times_used(g.church.id),
        keys=key_choices(),
        active="services",
    )


@bp.post("/songs/")
@login_required
@min_role("leader")
def add_song():
    title = (request.form.get("title") or "").strip()
    if not title:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.songs"))

    try:
        default_key = normalize_key(request.form.get("default_key"))
    except UnknownKey:
        flash(SERVICES["key_bad"].format(key=request.form.get("default_key")), "error")
        return redirect(url_for("services.songs"))

    db.session.add(
        Song(
            church_id=g.church.id,
            title=title[:200],
            author=(request.form.get("author") or "").strip() or None,
            ccli_number=(request.form.get("ccli_number") or "").strip() or None,
            default_key=default_key,
            tempo_bpm=request.form.get("tempo_bpm", type=int),
        )
    )
    db.session.commit()

    flash(SERVICES["song_added"].format(title=title), "notice")
    return redirect(url_for("services.songs"))


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

@bp.get("/teams/")
@login_required
@min_role("leader")
def teams():
    return render_template(
        "services/teams.html",
        church=g.church,
        content=SERVICES,
        teams=db.session.scalars(Team.for_church(g.church.id)).all(),
        people=db.session.scalars(Person.for_church(g.church.id)).all(),
        serving=Team.people_serving(g.church.id),
        active="services",
    )


@bp.post("/teams/")
@login_required
@min_role("leader")
def add_team():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.teams"))

    team = Team(church_id=g.church.id, name=name[:120])
    db.session.add(team)
    db.session.commit()

    flash(SERVICES["team_added"].format(name=team.name), "notice")
    return redirect(url_for("services.teams"))


@bp.post("/teams/<int:team_id>/positions/")
@login_required
@min_role("leader")
def add_position(team_id: int):
    team = Team.get_for_church(g.church.id, team_id)
    if team is None:
        abort(404)

    name = (request.form.get("name") or "").strip()
    if not name:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.teams"))

    db.session.add(
        TeamPosition(church_id=g.church.id, team_id=team.id, name=name[:120])
    )
    db.session.commit()

    flash(SERVICES["position_added"].format(name=name), "notice")
    return redirect(url_for("services.teams"))


@bp.post("/teams/<int:team_id>/members/")
@login_required
@min_role("leader")
def add_team_member(team_id: int):
    team = Team.get_for_church(g.church.id, team_id)
    if team is None:
        abort(404)

    person_id = request.form.get("person_id", type=int)
    person = Person.get_for_church(g.church.id, person_id) if person_id else None
    if person is None:
        abort(400)

    if any(m.person_id == person.id for m in team.members):
        flash(SERVICES["already_on"].format(name=person.full_name), "error")
        return redirect(url_for("services.teams"))

    db.session.add(
        TeamMembership(church_id=g.church.id, team_id=team.id, person_id=person.id)
    )
    db.session.commit()

    flash(
        SERVICES["member_added"].format(name=person.full_name, team=team.name), "notice"
    )
    return redirect(url_for("services.teams"))


@bp.post("/teams/<int:team_id>/members/<int:membership_id>/delete/")
@login_required
@min_role("leader")
def remove_team_member(team_id: int, membership_id: int):
    team = Team.get_for_church(g.church.id, team_id)
    if team is None:
        abort(404)

    membership = db.session.scalar(
        db.select(TeamMembership).where(
            TeamMembership.id == membership_id,
            TeamMembership.church_id == g.church.id,
            TeamMembership.team_id == team.id,
        )
    )
    if membership is None:
        abort(404)

    name = membership.person.full_name
    db.session.delete(membership)
    db.session.commit()

    flash(SERVICES["member_removed"].format(name=name), "notice")
    return redirect(url_for("services.teams"))


# ---------------------------------------------------------------------------
# Reordering, copying, and staffing
# ---------------------------------------------------------------------------

@bp.post("/<int:service_id>/items/<int:item_id>/move/")
@login_required
@min_role("leader")
def move_item(service_id: int, item_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    item = ServiceItem.get_for_church(g.church.id, item_id)
    if service is None or item is None or item.service_id != service.id:
        abort(404)

    direction = -1 if request.form.get("direction") == "up" else 1
    if service.move_item(item, direction):
        # Swapping preserves the pair of numbers but a plan that has been
        # edited for weeks accumulates gaps, and a gap makes the next insert
        # land somewhere surprising.
        service.renumber()
        db.session.commit()
        flash(SERVICES["moved"], "notice")

    return redirect(url_for("services.plan", service_id=service.id))


@bp.post("/<int:service_id>/copy/")
@login_required
@min_role("leader")
def copy_from(service_id: int):
    service = Service.get_for_church(g.church.id, service_id)
    if service is None:
        abort(404)

    source_id = request.form.get("source_id", type=int)
    source = Service.get_for_church(g.church.id, source_id) if source_id else None
    if source is None or source.id == service.id:
        abort(400)

    copied = copy_plan(source, service)
    db.session.commit()

    flash(
        SERVICES["copied"].format(
            count=copied,
            name=format_local(source.starts_at, g.church, "%B %-d"),
        ),
        "notice",
    )
    return redirect(url_for("services.plan", service_id=service.id))


# ---------------------------------------------------------------------------
# Service types
# ---------------------------------------------------------------------------

@bp.get("/types/")
@login_required
@min_role("leader")
def types():
    return render_template(
        "services/types.html",
        church=g.church,
        content=SERVICES,
        types=db.session.scalars(ServiceType.for_church(g.church.id)).all(),
        songs=db.session.scalars(Song.for_church(g.church.id)).all(),
        teams=db.session.scalars(Team.for_church(g.church.id)).all(),
        active="services",
    )


@bp.post("/types/")
@login_required
@min_role("leader")
def add_type():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.types"))

    service_type = ServiceType(church_id=g.church.id, name=name[:120])
    db.session.add(service_type)
    db.session.commit()

    flash(SERVICES["type_added"].format(name=service_type.name), "notice")
    return redirect(url_for("services.types"))


@bp.post("/types/<int:type_id>/items/")
@login_required
@min_role("leader")
def add_template_item(type_id: int):
    service_type = ServiceType.get_for_church(g.church.id, type_id)
    if service_type is None:
        abort(404)

    kind = (request.form.get("kind") or ITEM_ELEMENT).strip()
    if kind not in ITEM_KINDS:
        abort(400)

    title = (request.form.get("title") or "").strip()
    song = None
    if kind == ITEM_SONG:
        song_id = request.form.get("song_id", type=int)
        song = Song.get_for_church(g.church.id, song_id) if song_id else None
        title = song.title if song else title

    if not title:
        flash(SERVICES["item_title_required"], "error")
        return redirect(url_for("services.types"))

    db.session.add(
        ServiceTemplateItem(
            church_id=g.church.id,
            service_type_id=service_type.id,
            position=max((i.position for i in service_type.template_items), default=0) + 1,
            kind=kind,
            title=title[:200],
            minutes=request.form.get("minutes", type=int),
            song_id=song.id if song else None,
        )
    )
    db.session.commit()

    flash(SERVICES["item_added"], "notice")
    return redirect(url_for("services.types"))


@bp.post("/types/<int:type_id>/items/<int:item_id>/delete/")
@login_required
@min_role("leader")
def delete_template_item(type_id: int, item_id: int):
    service_type = ServiceType.get_for_church(g.church.id, type_id)
    item = db.session.scalar(
        db.select(ServiceTemplateItem).where(
            ServiceTemplateItem.id == item_id,
            ServiceTemplateItem.church_id == g.church.id,
        )
    )
    if service_type is None or item is None or item.service_type_id != service_type.id:
        abort(404)

    db.session.delete(item)
    db.session.commit()

    flash(SERVICES["item_removed"], "notice")
    return redirect(url_for("services.types"))


@bp.post("/types/<int:type_id>/needs/")
@login_required
@min_role("leader")
def add_type_need(type_id: int):
    service_type = ServiceType.get_for_church(g.church.id, type_id)
    if service_type is None:
        abort(404)

    position_id = request.form.get("position_id", type=int)
    position = db.session.scalar(
        db.select(TeamPosition).where(
            TeamPosition.id == position_id, TeamPosition.church_id == g.church.id
        )
    ) if position_id else None
    if position is None:
        abort(400)

    wanted = max(1, request.form.get("wanted", type=int) or 1)

    existing = next(
        (n for n in service_type.needs if n.position_id == position.id), None
    )
    if existing is not None:
        existing.wanted = wanted
    else:
        db.session.add(
            ServiceTypeNeed(
                church_id=g.church.id,
                service_type_id=service_type.id,
                position_id=position.id,
                wanted=wanted,
            )
        )
    db.session.commit()

    flash(SERVICES["needs_added"].format(name=position.name), "notice")
    return redirect(url_for("services.types"))


@bp.post("/<int:service_id>/items/<int:item_id>/key/")
@login_required
@min_role("leader")
def set_item_key(service_id: int, item_id: int):
    """Change the key of one song in one plan.

    Per plan, not per song: the same song does not sit in the same key every
    week, and changing it here is what the worship team will see.
    """
    service = Service.get_for_church(g.church.id, service_id)
    item = ServiceItem.get_for_church(g.church.id, item_id)
    if service is None or item is None or item.service_id != service.id:
        abort(404)

    raw = (request.form.get("key_override") or "").strip()
    try:
        item.key_override = normalize_key(raw)
    except UnknownKey:
        flash(SERVICES["key_bad"].format(key=raw), "error")
        return redirect(url_for("services.plan", service_id=service.id))

    db.session.commit()
    flash(SERVICES["key_set"].format(title=item.title, key=item.key or "the song default"), "notice")
    return redirect(url_for("services.plan", service_id=service.id))
