"""The roster.

Every query in this module is scoped to `g.church.id`. There is no exception
and there is no code path that loads a person by primary key alone. The
model-side helpers are what enforce it; this module never builds its own
`select(Person)`.
"""

from __future__ import annotations

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
from datetime import date

from flask_login import current_user, login_required

from app.content import PEOPLE, STUCK
from app.extensions import db
from app.models import (
    CONTACT_METHODS,
    KIND_CONTACT,
    KIND_NEXT_STEP,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_OPEN,
    ContactLog,
    KIND_NOTE,
    KIND_STAGE_CHANGE,
    NextStep,
    Person,
    PersonEvent,
    User,
)
from app.models.base import utcnow
from app.security import min_role
from app.stages import (
    CONTACT_WINDOW_DAYS,
    STAGE_BY_CODE,
    is_forward,
    next_stage,
    recommended_next_step,
    stage_label,
    stages_for,
)

bp = Blueprint("people", __name__, url_prefix="/people")

PAGE_SIZE = 25


@bp.get("/")
@login_required
@min_role("leader")
def index():
    term = (request.args.get("q") or "").strip()
    stage = (request.args.get("stage") or "").strip() or None
    page = max(1, request.args.get("page", type=int) or 1)

    if stage and stage not in STAGE_BY_CODE:
        # An unknown stage in the query string is a typo or a probe. Showing
        # everyone would silently misreport the filter, so refuse instead.
        abort(404)

    query = Person.search(g.church.id, term=term, stage=stage)
    pagination = db.paginate(query, page=page, per_page=PAGE_SIZE, error_out=False)

    return render_template(
        "people/index.html",
        church=g.church,
        content=PEOPLE,
        people=pagination.items,
        pagination=pagination,
        stages=stages_for(g.church),
        counts=Person.stage_counts(g.church.id),
        total=Person.total_for_church(g.church.id),
        active_stage=stage,
        term=term,
        active="people",
    )


@bp.get("/<int:person_id>/")
@login_required
@min_role("leader")
def detail(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        # 404, not 403. Telling one church that a person id exists somewhere
        # else is itself a disclosure.
        abort(404)

    events = db.session.scalars(
        PersonEvent.for_person(g.church.id, person.id)
    ).all()

    household_members = []
    if person.household is not None:
        household_members = [
            member for member in person.household.members if member.id != person.id
        ]

    return render_template(
        "people/detail.html",
        church=g.church,
        content=PEOPLE,
        stuck=STUCK,
        person=person,
        events=events,
        contacts=db.session.scalars(
            ContactLog.for_person(g.church.id, person.id)
        ).all(),
        open_steps=db.session.scalars(
            NextStep.open_for_person(g.church.id, person.id)
        ).all(),
        recommended=recommended_next_step(person.stage),
        assignable=_assignable_users(),
        contact_methods=CONTACT_METHODS,
        household_members=household_members,
        stages=stages_for(g.church),
        next_stage=next_stage(person.stage),
        active="people",
    )


def _assignable_users():
    """Staff and leaders at this church. A member cannot own a next step."""
    return db.session.scalars(
        db.select(User)
        .where(
            User.church_id == g.church.id,
            User.role.in_(("staff", "leader")),
            User.is_active_account.is_(True),
        )
        .order_by(User.name)
    ).all()


@bp.post("/<int:person_id>/stage/")
@login_required
@min_role("leader")
def move_stage(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    target = (request.form.get("stage") or "").strip()
    if target not in STAGE_BY_CODE:
        abort(400)

    if target == person.stage:
        return redirect(url_for("people.detail", person_id=person.id))

    previous = person.stage
    direction = "forward" if is_forward(previous, target) else "back"

    person.stage = target
    # Resetting the clock is the point. Increment 3's stuck engine measures
    # time in the current stage, so a move has to restart it or a person who
    # just advanced would immediately read as stuck.
    person.stage_since = utcnow()

    PersonEvent.record(
        person,
        KIND_STAGE_CHANGE,
        PEOPLE["stage_moved"].format(
            frm=stage_label(previous), to=stage_label(target)
        ),
        detail=PEOPLE["stage_moved_detail"].format(direction=direction),
        actor=current_user,
    )
    db.session.commit()

    flash(
        PEOPLE["stage_flash"].format(
            name=person.first_name, stage=stage_label(target)
        ),
        "notice",
    )
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/note/")
@login_required
@min_role("leader")
def add_note(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(PEOPLE["note_empty"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    PersonEvent.record(
        person,
        KIND_NOTE,
        body[:255],
        detail=body if len(body) > 255 else None,
        actor=current_user,
    )
    db.session.commit()

    flash(PEOPLE["note_saved"], "notice")
    return redirect(url_for("people.detail", person_id=person.id))


# ---------------------------------------------------------------------------
# Increment 3: contact, next steps, ownership
# ---------------------------------------------------------------------------

@bp.get("/stuck/")
@login_required
@min_role("leader")
def stuck_list():
    people = db.session.scalars(Person.stuck(g.church.id)).all()
    return render_template(
        "people/stuck.html",
        church=g.church,
        content=PEOPLE,
        stuck=STUCK,
        people=people,
        window=CONTACT_WINDOW_DAYS,
        active="people",
    )


@bp.post("/<int:person_id>/contact/")
@login_required
@min_role("leader")
def log_contact(person_id: int):
    """Record that a human actually talked to this person.

    This is the hard stop. Logging contact updates `last_contact_at`, which is
    what clears a stuck flag. A note does not, deliberately: writing "should
    call Marcus" in the timeline is not calling Marcus, and a system that
    treats them the same will quietly stop flagging the people it exists to
    flag.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    method = (request.form.get("method") or "").strip()
    if method not in CONTACT_METHODS:
        abort(400)

    summary = (request.form.get("summary") or "").strip()
    if not summary:
        flash(STUCK["contact_empty"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    now = utcnow()
    contact = ContactLog(
        church_id=person.church_id,
        person_id=person.id,
        method=method,
        summary=summary[:255],
        detail=summary if len(summary) > 255 else None,
        occurred_at=now,
        logged_by_user_id=current_user.id,
        logged_by_name=current_user.name,
    )
    db.session.add(contact)

    # Only ever move forward. Backfilling an older conversation must not make
    # a person look more recently contacted than they are.
    if person.last_contact_at is None or now > person.last_contact_at:
        person.last_contact_at = now

    PersonEvent.record(
        person,
        KIND_CONTACT,
        f"{contact.method_label}: {summary[:180]}",
        actor=current_user,
        occurred_at=now,
    )
    db.session.commit()

    flash(STUCK["contact_saved"].format(name=person.first_name), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/step/")
@login_required
@min_role("leader")
def assign_step(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash(STUCK["step_title_required"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    owner = None
    owner_id = request.form.get("owner_user_id", type=int)
    if owner_id:
        # Scoped lookup. An owner id from another church must not attach.
        owner = db.session.scalar(
            db.select(User).where(
                User.id == owner_id,
                User.church_id == g.church.id,
                User.role.in_(("staff", "leader")),
            )
        )
        if owner is None:
            abort(400)

    due_on = None
    raw_due = (request.form.get("due_on") or "").strip()
    if raw_due:
        try:
            due_on = date.fromisoformat(raw_due)
        except ValueError:
            abort(400)

    step = NextStep(
        church_id=person.church_id,
        person_id=person.id,
        title=title[:200],
        owner_user_id=owner.id if owner else None,
        owner_name=owner.name if owner else None,
        due_on=due_on,
        status=STATUS_OPEN,
        created_by_user_id=current_user.id,
    )
    db.session.add(step)

    PersonEvent.record(
        person,
        KIND_NEXT_STEP,
        f"Next step assigned: {title[:180]}",
        detail=f"Owner: {owner.name if owner else 'nobody yet'}",
        actor=current_user,
    )
    db.session.commit()

    flash(
        STUCK["step_assigned"].format(owner=owner.name if owner else "nobody yet"),
        "notice",
    )
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/step/<int:step_id>/close/")
@login_required
@min_role("leader")
def close_step(person_id: int, step_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    step = db.session.scalar(
        db.select(NextStep).where(
            NextStep.id == step_id,
            NextStep.church_id == g.church.id,
            NextStep.person_id == person.id,
        )
    )
    if step is None:
        abort(404)

    status = request.form.get("status") or STATUS_DONE
    if status not in (STATUS_DONE, STATUS_DROPPED):
        abort(400)

    step.close(status)
    PersonEvent.record(
        person,
        KIND_NEXT_STEP,
        f"Next step {step.status_label.lower()}: {step.title[:180]}",
        actor=current_user,
    )
    db.session.commit()

    flash(STUCK["step_closed"].format(title=step.title), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/owner/")
@login_required
@min_role("leader")
def set_owner(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    owner_id = request.form.get("owner_user_id", type=int)
    if not owner_id:
        person.owner_user_id = None
        person.owner_name = None
        db.session.commit()
        flash(STUCK["owner_cleared"], "notice")
        return redirect(url_for("people.detail", person_id=person.id))

    owner = db.session.scalar(
        db.select(User).where(
            User.id == owner_id,
            User.church_id == g.church.id,
            User.role.in_(("staff", "leader")),
        )
    )
    if owner is None:
        abort(400)

    person.owner_user_id = owner.id
    person.owner_name = owner.name
    db.session.commit()

    flash(
        STUCK["owner_set"].format(owner=owner.name, name=person.first_name), "notice"
    )
    return redirect(url_for("people.detail", person_id=person.id))
