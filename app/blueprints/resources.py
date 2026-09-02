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
from app.models.resource import KIND_LABELS
from app.security import min_role

bp = Blueprint("resources", __name__, url_prefix="/resources")


@bp.get("/")
@login_required
@min_role("leader")
def index():
    resources = db.session.scalars(Resource.for_church(g.church.id)).all()
    return render_template(
        "resources/index.html",
        church=g.church,
        content=RESOURCES,
        resources=resources,
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
        active="resources",
    )


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
    )
    db.session.add(session)
    db.session.commit()

    flash(RESOURCES["session_saved"], "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id))


@bp.post("/<int:resource_id>/sessions/<int:session_id>/delete/")
@login_required
@min_role("leader")
def delete_session(resource_id: int, session_id: int):
    resource = Resource.get_for_church(g.church.id, resource_id)
    session = ResourceSession.get_for_church(g.church.id, session_id)
    if resource is None or session is None or session.resource_id != resource.id:
        abort(404)

    db.session.delete(session)
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
