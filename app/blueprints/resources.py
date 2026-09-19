"""Writing and publishing resources.

Staff and leaders author here. Members read through the member app, which has
its own routes and its own visibility rule: a draft is invisible to a member
rather than merely unlinked, so guessing an id reveals nothing.
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

from app.content import RESOURCES
from app.extensions import db
from app.models import (
    RESOURCE_KINDS,
    STATUS_ARCHIVED,
    Resource,
    ResourceSession,
    SessionCompletion,
)
from app.models.resource import COVER_COUNT, KIND_LABELS, STATUS_DRAFT, STATUS_PUBLISHED
from app.security import min_role

bp = Blueprint("resources", __name__, url_prefix="/resources")


@bp.get("/")
@login_required
@min_role("leader")
def index():
    resources = db.session.scalars(Resource.for_church(g.church.id)).all()
    # Published first, then drafts, each alphabetical: what members see now
    # leads, work in progress follows.
    resources.sort(key=lambda r: (not r.is_published, r.title.lower()))
    archived = db.session.scalars(
        db.select(Resource)
        .where(Resource.church_id == g.church.id, Resource.status == STATUS_ARCHIVED)
        .order_by(Resource.title)
    ).all()
    return render_template(
        "resources/index.html",
        church=g.church,
        content=RESOURCES,
        resources=resources,
        archived=archived,
        covers=range(COVER_COUNT),
        started=SessionCompletion.started_counts(g.church.id),
        kinds=RESOURCE_KINDS,
        kind_labels=KIND_LABELS,
        active="resources",
    )


@bp.post("/")
@login_required
@min_role("leader")
def create():
    title = (request.form.get("title") or "").strip()
    if not title:
        flash(RESOURCES["title_required"], "error")
        return redirect(url_for("resources.index"))

    kind = (request.form.get("kind") or "").strip()
    if kind not in RESOURCE_KINDS:
        abort(400)

    resource = Resource(
        church_id=g.church.id,
        title=title[:200],
        summary=(request.form.get("summary") or "").strip() or None,
        kind=kind,
        cover=_cover_from_form(),
        created_by_user_id=current_user.id,
    )
    db.session.add(resource)
    db.session.commit()

    flash(RESOURCES["created"].format(title=resource.title), "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.get("/<int:resource_id>/")
@login_required
@min_role("leader")
def edit(resource_id: int):
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None:
        abort(404)
    return render_template(
        "resources/edit.html",
        church=g.church,
        content=RESOURCES,
        resource=resource,
        kinds=RESOURCE_KINDS,
        kind_labels=KIND_LABELS,
        covers=range(COVER_COUNT),
        started=SessionCompletion.started_counts(g.church.id).get(resource.id, 0),
        open_session=request.args.get("session", type=int),
        active="resources",
    )


def _cover_from_form() -> int:
    cover = request.form.get("cover", type=int)
    return cover if cover is not None and 0 <= cover < COVER_COUNT else 0


def _minutes_from_form() -> int | None:
    minutes = request.form.get("minutes", type=int)
    return minutes if minutes and 0 < minutes <= 240 else None


def _load(resource_id: int, session_id: int | None = None):
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None:
        abort(404)
    if session_id is None:
        return resource, None
    session = ResourceSession.get_for_church(g.church.id, session_id)
    if session is None or session.resource_id != resource.id:
        abort(404)
    return resource, session


def _saved(resource) -> str:
    """Tell the editor where their change went: live, or into a draft."""
    return RESOURCES["saved_live"] if resource.is_published else RESOURCES["saved_draft"]


@bp.post("/<int:resource_id>/details/")
@login_required
@min_role("leader")
def save_details(resource_id: int):
    """Title, kind, description, cover. Works the same before and after
    publishing: a typo in a live plan should take one save to fix, not an
    unpublish, an edit, and a republish during which members lose it."""
    resource, _ = _load(resource_id)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash(RESOURCES["title_required"], "error")
        return redirect(url_for("resources.edit", resource_id=resource.id))
    kind = (request.form.get("kind") or resource.kind).strip()
    if kind not in RESOURCE_KINDS:
        abort(400)

    resource.title = title[:200]
    resource.kind = kind
    resource.summary = (request.form.get("summary") or "").strip() or None
    resource.cover = _cover_from_form()
    db.session.commit()

    flash(_saved(resource), "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/sessions/<int:session_id>/")
@login_required
@min_role("leader")
def save_session(resource_id: int, session_id: int):
    """Edit one day in place. Completions point at the session row, not its
    words, so fixing a typo on day 3 keeps everyone's progress on day 3."""
    resource, session = _load(resource_id, session_id)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash(RESOURCES["session_title_required"], "error")
        return redirect(url_for("resources.edit", resource_id=resource.id, session=session.id))

    session.title = title[:200]
    session.passage_ref = (request.form.get("passage_ref") or "").strip() or None
    session.body = (request.form.get("body") or "").strip() or None
    session.question = (request.form.get("question") or "").strip() or None
    session.minutes = _minutes_from_form()
    db.session.commit()

    flash(_saved(resource), "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id, _anchor=f"s{session.id}"))


@bp.post("/<int:resource_id>/sessions/<int:session_id>/move/")
@login_required
@min_role("leader")
def move_session(resource_id: int, session_id: int):
    resource, session = _load(resource_id, session_id)
    direction = -1 if request.form.get("direction") == "up" else 1
    resource.move_session(session, direction)
    db.session.commit()
    return redirect(url_for("resources.edit", resource_id=resource.id, _anchor=f"s{session.id}"))


@bp.post("/<int:resource_id>/restore/")
@login_required
@min_role("leader")
def restore(resource_id: int):
    """Bring an archived resource back as a draft. Archive is not delete, so
    it should not be a one-way door either."""
    resource, _ = _load(resource_id)
    if resource.status == STATUS_ARCHIVED:
        resource.status = STATUS_DRAFT
        db.session.commit()
        flash(RESOURCES["restored"].format(title=resource.title), "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/sessions/")
@login_required
@min_role("leader")
def add_session(resource_id: int):
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None:
        abort(404)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash(RESOURCES["session_title_required"], "error")
        return redirect(url_for("resources.edit", resource_id=resource.id))

    session = ResourceSession(
        church_id=g.church.id,
        resource_id=resource.id,
        # Computed from what exists rather than taken from the form, so two
        # people adding a day at once cannot collide on the unique constraint.
        position=resource.next_position(),
        title=title[:200],
        passage_ref=(request.form.get("passage_ref") or "").strip() or None,
        body=(request.form.get("body") or "").strip() or None,
        question=(request.form.get("question") or "").strip() or None,
        minutes=_minutes_from_form(),
    )
    db.session.add(session)
    db.session.commit()

    flash(RESOURCES["session_saved_live"] if resource.is_published else RESOURCES["session_saved"], "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/sessions/<int:session_id>/delete/")
@login_required
@min_role("leader")
def delete_session(resource_id: int, session_id: int):
    resource = Resource.get_for_church(g.church.id, resource_id)
    session = ResourceSession.get_for_church(g.church.id, session_id)
    if resource is None or session is None or session.resource_id != resource.id:
        abort(404)

    if resource.is_published and resource.session_count <= 1:
        # A live plan with no days is the empty screen publish() refuses to
        # create. Unpublish first if the whole thing is going.
        flash(RESOURCES["session_last_live"], "error")
        return redirect(url_for("resources.edit", resource_id=resource.id))

    resource.sessions.remove(session)
    db.session.delete(session)
    db.session.flush()
    resource.renumber()
    db.session.commit()

    flash(RESOURCES["session_deleted"], "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/publish/")
@login_required
@min_role("leader")
def publish(resource_id: int):
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None:
        abort(404)

    if resource.is_published:
        resource.unpublish()
        db.session.commit()
        flash(RESOURCES["unpublished"].format(title=resource.title), "notice")
    else:
        try:
            resource.publish()
        except ValueError:
            flash(RESOURCES["publish_empty"], "error")
            return redirect(url_for("resources.edit", resource_id=resource.id))
        db.session.commit()
        flash(RESOURCES["published"].format(title=resource.title), "notice")

    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/archive/")
@login_required
@min_role("leader")
def archive(resource_id: int):
    """Archive rather than delete.

    People have completions against this, and deleting it would erase a record
    of what they actually read.
    """
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None:
        abort(404)

    resource.status = STATUS_ARCHIVED
    resource.published_at = None
    db.session.commit()

    flash(RESOURCES["archived"].format(title=resource.title), "notice")
    return redirect(url_for("resources.index"))
