from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import Church, Person, Stage

bp = Blueprint("auth", __name__, url_prefix="/account")


def _church() -> Church:
    return Church.query.filter_by(slug=current_app.config["CHURCH_SLUG"]).first_or_404()


def _landing(person: Person) -> str:
    return url_for("staff.dashboard") if person.is_staff else url_for("portal.home")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_landing(current_user))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        person = Person.query.filter_by(church_id=_church().id, email=email).first()
        if person and person.check_password(password) and person.is_active_record:
            login_user(person, remember=True)
            return redirect(request.args.get("next") or _landing(person))
        flash("That email and password do not match.", "error")

    return render_template("auth/login.html")


@bp.route("/claim", methods=["GET", "POST"])
def claim():
    """Anyone already in the database sets a password and gets the member app.
    A church plant has no members to invite yet, so the record comes first and
    the login comes second."""
    if request.method == "POST":
        church = _church()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if len(password) < 8:
            flash("Use at least 8 characters.", "error")
            return render_template("auth/claim.html")

        person = Person.query.filter_by(church_id=church.id, email=email).first()
        if not person:
            person = Person(
                church_id=church.id,
                first_name=(request.form.get("first_name") or "").strip() or "Friend",
                last_name=(request.form.get("last_name") or "").strip(),
                email=email,
                source="self signup",
            )
            first_stage = (
                Stage.query.filter_by(church_id=church.id).order_by(Stage.position).first()
            )
            db.session.add(person)
            db.session.flush()
            if first_stage:
                person.move_to_stage(first_stage, note="Created an account")
        elif person.password_hash:
            flash("That account already exists. Sign in instead.", "info")
            return redirect(url_for("auth.login"))

        person.set_password(password)
        db.session.commit()
        login_user(person, remember=True)
        return redirect(_landing(person))

    return render_template("auth/claim.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("public.home"))
