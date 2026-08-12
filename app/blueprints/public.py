"""
The DOS has no marketing site. The church's public website is built and hosted
elsewhere, so this blueprint exists only to get people from that site into this
database.

Three ways in, in order of preference:

1. POST to /api/intake from the existing site's own form. Best experience,
   because the form stays styled like their site and the visitor never leaves.
2. Iframe /embed/connect. Zero code on their side beyond one tag.
3. Staff enter people by hand under Staff > People > Add a person.

Routes here are deliberately thin. Everything else lives behind a login.
"""

import time

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from ..automations import enroll
from ..content import CONNECT
from ..emails import notify_new_person
from ..extensions import csrf, db
from ..models import Church, Person, Stage
from ..ratelimit import limit

bp = Blueprint("public", __name__)

MIN_SECONDS_ON_FORM = 3

# Which stage a submission lands in, per form type. Unknown types land in the
# first stage rather than being rejected, because a lost lead is worse than a
# mislabelled one.
FORM_STAGES = {
    "connect card": "Interested",
    "launch team": "Launch team",
    "prayer request": "Interested",
    "serve interest": "Connected",
}


def current_church() -> Church:
    return Church.query.filter_by(slug=current_app.config["CHURCH_SLUG"]).first_or_404()


def _allowed_origin() -> str:
    return current_app.config.get("PUBLIC_SITE_URL", "").rstrip("/")


def _spam_check(form) -> bool:
    """Honeypot plus timing gate. True means it looks like a bot."""
    if form.get("website"):
        return True
    started = form.get("started_at")
    if started in (None, ""):
        return False  # the site's own form may not send one
    try:
        return (time.time() - float(started)) < MIN_SECONDS_ON_FORM
    except ValueError:
        return True


def record_person(data, source: str):
    """Create or update a person and put them into the right sequence.
    Returns (person, created) or (None, False) when the payload is unusable."""
    church = current_church()
    email = (data.get("email") or "").strip().lower()
    first = (data.get("first_name") or data.get("name") or "").strip()
    if not email or not first:
        return None, False

    # A single "name" field from a site form still needs splitting.
    last = (data.get("last_name") or "").strip()
    if not last and " " in first:
        first, last = first.split(" ", 1)

    person = Person.query.filter_by(church_id=church.id, email=email).first()
    created = person is None
    if created:
        person = Person(church_id=church.id, first_name=first, email=email)
        db.session.add(person)

    person.first_name = first
    person.last_name = last
    person.phone = (data.get("phone") or "").strip() or None
    person.source = source
    note = (data.get("message") or data.get("notes") or "").strip()
    if note:
        person.notes = f"{person.notes}\n{note}" if person.notes else note

    target = Stage.query.filter_by(
        church_id=church.id, name=FORM_STAGES.get(source, "Interested")
    ).first()
    if target and (
        person.stage_id is None or (person.stage and person.stage.position < target.position)
    ):
        person.move_to_stage(target, note=f"Submitted {source}")

    db.session.commit()
    enroll(person)
    notify_new_person(person, source, created=created)
    return person, created


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


@bp.route("/")
def home():
    """This host is the back end, not the website. Send people where they
    were actually going."""
    if current_user.is_authenticated:
        return redirect(
            url_for("staff.dashboard") if current_user.is_staff else url_for("portal.home")
        )
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------
# Option 1: the existing site posts here
# --------------------------------------------------------------------------


@bp.route("/api/intake", methods=["POST", "OPTIONS"])
@csrf.exempt
@limit("intake", limit_count=30, window_seconds=3600, json_response=True)
def api_intake():
    """Accepts JSON or form encoded posts from the church's own website.

    Auth is a shared token, sent as X-Intake-Token or a token field. It is not
    a secret in the strong sense, since it ships in the site's markup if they
    post from the browser. It exists to stop drive by posting, which is why the
    honeypot and rate of submission still matter.
    """
    origin = _allowed_origin()

    if request.method == "OPTIONS":
        response = make_response("", 204)
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Intake-Token"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response

    data = request.get_json(silent=True) or request.form
    expected = current_app.config.get("INTAKE_TOKEN")
    supplied = request.headers.get("X-Intake-Token") or data.get("token")
    if not expected or supplied != expected:
        return jsonify({"ok": False, "error": "bad token"}), 401

    if _spam_check(data):
        # Answer as if it worked. Bots should learn nothing.
        return jsonify({"ok": True}), 200

    source = (data.get("form") or "connect card").strip().lower()
    person, created = record_person(data, source if source in FORM_STAGES else "connect card")
    if not person:
        return jsonify({"ok": False, "error": "name and email are required"}), 400

    response = jsonify({"ok": True, "created": created})
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    return response


# --------------------------------------------------------------------------
# Option 2: iframe this
# --------------------------------------------------------------------------


@bp.route("/embed/connect", methods=["GET", "POST"])
@csrf.exempt
@limit("intake", limit_count=30, window_seconds=3600)
def embed_connect():
    """A standalone form with no site chrome, sized to be dropped into an
    iframe on the church's website.

    CSRF protection is off here on purpose, not by oversight. In a cross site
    iframe the session cookie is not sent, because SameSite=Lax blocks it, so a
    CSRF token could never validate. The honeypot and the timing gate carry the
    load instead, and the worst case for a forged post is a junk person record,
    not a state change on anyone's account. For the same reason this view never
    uses flash, which also needs the session.
    """
    form_kind = (request.args.get("form") or "connect card").lower()
    if form_kind not in FORM_STAGES:
        form_kind = "connect card"

    error = None
    if request.method == "POST":
        if _spam_check(request.form):
            return redirect(url_for("public.thanks"))
        person, _ = record_person(request.form, form_kind)
        if person:
            return redirect(url_for("public.thanks"))
        error = "Add your name and email so we can reach you."

    return render_template(
        "public/embed_connect.html",
        c=CONNECT,
        now=time.time(),
        form_kind=form_kind,
        error=error,
    )


@bp.route("/embed/thanks")
def thanks():
    return render_template("public/embed_thanks.html", c=CONNECT)



