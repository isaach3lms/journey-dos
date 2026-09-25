"""Writing and publishing resources.

Staff and leaders author here. Members read through the member app, which has
its own routes and its own visibility rule: a draft is invisible to a member
rather than merely unlinked, so guessing an id reveals nothing.
"""

from __future__ import annotations

from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.content import RESOURCES
from app.extensions import db
from app.files import RefusedFile, check_image
from app.models import (
    RESOURCE_KINDS,
    STATUS_ARCHIVED,
    TAG_STAGE,
    TAG_THEME,
    Resource,
    ResourceSession,
    ResourceTag,
    SessionCompletion,
)
from app.models.resource import (
    COVER_COUNT,
    KIND_LABELS,
    MAX_THEME_LENGTH,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
)
from app.models.songchart import stored_bytes
from app.stages import STAGE_BY_CODE, stages_for
from app.models.verse import WeeklyVerse, week_of
from app.security import min_role

bp = Blueprint("resources", __name__, url_prefix="/resources")


@bp.get("/")
@login_required
@min_role("leader")
def index():
    stage, theme = _filters_from_request()
    resources = db.session.scalars(
        Resource.for_church(g.church.id, stage=stage, theme=theme)
    ).all()
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
        stages=stages_for(g.church),
        stages_in_use=ResourceTag.stages_in_use(g.church.id),
        themes=ResourceTag.themes_for_church(g.church.id),
        active_stage=stage,
        active_theme=theme,
        **_verse_context(),
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
        stages=stages_for(g.church),
        themes=ResourceTag.themes_for_church(g.church.id),
        started=SessionCompletion.started_counts(g.church.id).get(resource.id, 0),
        open_session=request.args.get("session", type=int),
        active="resources",
    )


def _filters_from_request(published_only: bool = False) -> tuple[str | None, str | None]:
    """What the screen is filtered by, checked rather than echoed.

    A stage that is not a stage, or a theme nothing carries, becomes no
    filter at all instead of an empty screen that looks broken.
    """
    stage = (request.args.get("stage") or "").strip() or None
    if stage and stage not in STAGE_BY_CODE:
        stage = None

    theme = (request.args.get("theme") or "").strip() or None
    if theme and theme not in ResourceTag.themes_for_church(
        g.church.id, published_only=published_only
    ):
        theme = None
    return stage, theme


def _themes_from_form() -> list[str]:
    """Themes as typed, one per line or comma separated.

    Matched against what the church already uses so "Prayer" and "prayer"
    do not become two filters for the same thing.
    """
    raw = (request.form.get("themes") or "").replace("\n", ",")
    known = {t.lower(): t for t in ResourceTag.themes_for_church(g.church.id)}
    out = []
    for piece in raw.split(","):
        name = " ".join(piece.split())[:MAX_THEME_LENGTH]
        if not name:
            continue
        name = known.get(name.lower(), name)
        if name not in out:
            out.append(name)
    return out[:12]


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


# ---------------------------------------------------------------------------
# Verse of the week
#
# Lives on the Resources page because it is content published to members, the
# same as a reading plan. See app/models/verse.py for how the week is chosen.
# ---------------------------------------------------------------------------

def _today():
    from app.timeutil import now_local

    return now_local(g.church).date()


def _verse_context() -> dict:
    today = _today()
    current = WeeklyVerse.current(g.church.id, today)
    this_week = week_of(today)
    return {
        "verse_current": current,
        "verse_upcoming": db.session.scalars(WeeklyVerse.upcoming(g.church.id, today)).all(),
        "verse_past": db.session.scalars(
            WeeklyVerse.past(g.church.id, current.starts_on if current else this_week)
        ).all(),
        "verse_this_week": this_week,
        "verse_next_week": this_week + timedelta(days=7),
    }


def _world_english_text(reference: str) -> str | None:
    """Fill a blank verse from the public domain translation, if loaded."""
    from app.bible.reference import parse
    from app.models import BibleVerse

    parsed = parse(reference)
    if parsed is None:
        return None
    rows = BibleVerse.passage(parsed)
    return " ".join(row.text for row in rows) or None


def _verse_from_form(verse: WeeklyVerse | None = None):
    """Read and check the form. Returns (fields, error_key)."""
    from datetime import date as _date

    reference = (request.form.get("reference") or "").strip()[:120]
    text = (request.form.get("text") or "").strip()
    translation = (request.form.get("translation") or "").strip()[:20] or None
    raw_week = (request.form.get("starts_on") or "").strip()

    if not reference:
        return None, "verse_reference_required"
    try:
        starts_on = week_of(_date.fromisoformat(raw_week)) if raw_week else week_of(_today())
    except ValueError:
        return None, "verse_bad_date"

    if not text:
        text = _world_english_text(reference)
        if not text:
            return None, "verse_text_required"
        translation = "WEB"

    return {
        "reference": reference,
        "text": text[:2000],
        "translation": translation,
        "starts_on": starts_on,
    }, None


@bp.post("/verse/")
@login_required
@min_role("leader")
def save_verse():
    """Set the verse for a week. Setting a week that already has one replaces
    it, because two verses for one week is never what anybody meant."""
    fields, error = _verse_from_form()
    if error:
        flash(RESOURCES[error], "error")
        return redirect(url_for("resources.index", _anchor="verse"))

    verse = WeeklyVerse.for_week(g.church.id, fields["starts_on"])
    if verse is None:
        verse = WeeklyVerse(church_id=g.church.id, created_by_user_id=current_user.id, **fields)
        db.session.add(verse)
    else:
        for key, value in fields.items():
            setattr(verse, key, value)
    db.session.commit()

    flash(_verse_saved_message(verse), "notice")
    return redirect(url_for("resources.index", _anchor="verse"))


@bp.post("/verse/<int:verse_id>/")
@login_required
@min_role("leader")
def update_verse(verse_id: int):
    verse = WeeklyVerse.get_for_church(g.church.id, verse_id)
    if verse is None:
        abort(404)
    fields, error = _verse_from_form(verse)
    if error:
        flash(RESOURCES[error], "error")
        return redirect(url_for("resources.index", _anchor="verse"))

    clash = WeeklyVerse.for_week(g.church.id, fields["starts_on"])
    if clash is not None and clash.id != verse.id:
        flash(RESOURCES["verse_week_taken"].format(date=fields["starts_on"].strftime("%B %-d")), "error")
        return redirect(url_for("resources.index", _anchor="verse"))

    for key, value in fields.items():
        setattr(verse, key, value)
    db.session.commit()
    flash(_verse_saved_message(verse), "notice")
    return redirect(url_for("resources.index", _anchor="verse"))


@bp.post("/verse/<int:verse_id>/delete/")
@login_required
@min_role("leader")
def delete_verse(verse_id: int):
    verse = WeeklyVerse.get_for_church(g.church.id, verse_id)
    if verse is None:
        abort(404)
    db.session.delete(verse)
    db.session.commit()
    flash(RESOURCES["verse_deleted"], "notice")
    return redirect(url_for("resources.index", _anchor="verse"))


def _verse_saved_message(verse) -> str:
    when = verse.starts_on.strftime("%B %-d")
    if verse.starts_on <= _today():
        return RESOURCES["verse_saved_live"].format(reference=verse.reference)
    return RESOURCES["verse_saved_scheduled"].format(reference=verse.reference, date=when)


# ---------------------------------------------------------------------------
# What a resource is for, what it is about, and what it looks like
# ---------------------------------------------------------------------------

@bp.post("/<int:resource_id>/tags/")
@login_required
@min_role("leader")
def save_tags(resource_id: int):
    """Stages and themes, saved together because they are one form."""
    resource, _ = _load(resource_id)

    wanted = [c for c in request.form.getlist("stages") if c in STAGE_BY_CODE]
    resource.set_tags(TAG_STAGE, wanted)
    resource.set_tags(TAG_THEME, _themes_from_form())
    db.session.commit()

    flash(_saved(resource), "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id, _anchor="tags"))


@bp.post("/<int:resource_id>/thumbnail/")
@login_required
@min_role("leader")
def save_thumbnail(resource_id: int):
    """A church's own artwork in place of the gradient.

    Checked by its first bytes rather than its name, and counted against the
    same storage quota as every other upload.
    """
    resource, _ = _load(resource_id)

    upload = request.files.get("thumbnail")
    data = upload.read() if upload else b""
    try:
        # The image being replaced does not count against the room for the
        # new one, or replacing a picture with the same picture could fail.
        used = stored_bytes(g.church.id) - (resource.thumb_bytes or 0)
        content_type = check_image(data, used_bytes=max(0, used))
    except RefusedFile as refused:
        flash(RESOURCES[refused.reason].format(**refused.fields), "error")
        return redirect(url_for("resources.edit", resource_id=resource.id, _anchor="art"))

    resource.set_thumb(data, content_type)
    db.session.commit()

    flash(RESOURCES["thumb_saved"], "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id, _anchor="art"))


@bp.post("/<int:resource_id>/thumbnail/remove/")
@login_required
@min_role("leader")
def remove_thumbnail(resource_id: int):
    resource, _ = _load(resource_id)
    resource.clear_thumb()
    db.session.commit()
    flash(RESOURCES["thumb_removed"], "notice")
    return redirect(url_for("resources.edit", resource_id=resource.id, _anchor="art"))


@bp.get("/<int:resource_id>/thumb/")
@login_required
def thumbnail(resource_id: int):
    """The image itself.

    Not behind min_role: a member has to be able to see the artwork on a
    plan they can read. A draft stays invisible to them, the same rule the
    member routes use, so guessing an id reveals nothing.
    """
    resource = Resource.get_for_church(g.church.id, resource_id)
    if resource is None or not resource.has_thumb:
        abort(404)
    if not current_user.at_least("leader") and not resource.is_published:
        abort(404)

    response = make_response(resource.thumb_data)
    response.headers["Content-Type"] = resource.thumb_type or "image/png"
    response.headers["Content-Length"] = str(resource.thumb_bytes)
    # The URL carries thumb_set_at, so a cached copy is always the copy that
    # URL named. Private: it belongs to one church.
    response.headers["Cache-Control"] = "private, max-age=604800, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
