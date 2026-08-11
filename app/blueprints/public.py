import time

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..automations import enroll
from ..content import ABOUT, CONNECT, FOOTER, GIVE, HOME, LAUNCH_TEAM
from ..emails import notify_new_person
from ..extensions import db
from ..models import Church, Person, Stage

bp = Blueprint("public", __name__)

MIN_SECONDS_ON_FORM = 3


def current_church() -> Church:
    return Church.query.filter_by(slug=current_app.config["CHURCH_SLUG"]).first_or_404()


def _spam_check(form) -> bool:
    """Honeypot plus timing gate. True means the submission looks like a bot."""
    if form.get("website"):
        return True
    try:
        started = float(form.get("started_at", "0"))
    except ValueError:
        return True
    return (time.time() - started) < MIN_SECONDS_ON_FORM


def _intake(form, source: str, stage_name: str):
    church = current_church()
    email = (form.get("email") or "").strip().lower()
    first = (form.get("first_name") or "").strip()
    if not email or not first:
        flash("Add your name and email so we can reach you.", "error")
        return None

    person = Person.query.filter_by(church_id=church.id, email=email).first()
    if not person:
        person = Person(church_id=church.id, first_name=first, email=email)
        db.session.add(person)

    person.first_name = first
    person.last_name = (form.get("last_name") or "").strip()
    person.phone = (form.get("phone") or "").strip() or None
    person.source = source
    note = (form.get("message") or "").strip()
    if note:
        person.notes = f"{person.notes}\n{note}" if person.notes else note

    stage = Stage.query.filter_by(church_id=church.id, name=stage_name).first()
    if stage and (person.stage_id is None or (person.stage and person.stage.position < stage.position)):
        person.move_to_stage(stage, note=f"Submitted {source}")
    db.session.commit()

    enroll(person)
    notify_new_person(person, source)
    return person


@bp.route("/")
def home():
    return render_template("public/home.html", c=HOME, footer=FOOTER)


@bp.route("/about")
def about():
    return render_template("public/about.html", c=ABOUT, footer=FOOTER)


@bp.route("/launch-team", methods=["GET", "POST"])
def launch_team():
    if request.method == "POST":
        if _spam_check(request.form):
            return redirect(url_for("public.thanks"))
        if _intake(request.form, "launch team", "Launch team"):
            return redirect(url_for("public.thanks"))
    return render_template(
        "public/launch_team.html", c=LAUNCH_TEAM, footer=FOOTER, now=time.time()
    )


@bp.route("/connect", methods=["GET", "POST"])
def connect():
    if request.method == "POST":
        if _spam_check(request.form):
            return redirect(url_for("public.thanks"))
        if _intake(request.form, "connect card", "Interested"):
            return redirect(url_for("public.thanks"))
    return render_template("public/connect.html", c=CONNECT, footer=FOOTER, now=time.time())


@bp.route("/thanks")
def thanks():
    return render_template("public/thanks.html", c=CONNECT, footer=FOOTER)


@bp.route("/give")
def give():
    church = current_church()
    give_url = church.tithely_give_url or current_app.config.get("TITHELY_GIVE_URL")
    form_id = church.tithely_form_id or current_app.config.get("TITHELY_FORM_ID")
    if not give_url and form_id:
        give_url = f"https://give.tithe.ly/?formId={form_id}"
    return render_template("public/give.html", c=GIVE, footer=FOOTER, give_url=give_url)
