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
from flask_login import current_user, login_required

from app.content import PEOPLE
from app.extensions import db
from app.models import KIND_NOTE, KIND_STAGE_CHANGE, Person, PersonEvent
from app.models.base import utcnow
from app.security import min_role
from app.stages import (
    STAGE_BY_CODE,
    is_forward,
    next_stage,
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
        person=person,
        events=events,
        household_members=household_members,
        stages=stages_for(g.church),
        next_stage=next_stage(person.stage),
        active="people",
    )


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
