"""
Accounts.

The rule that shapes this file: a person record existing in the database is not
proof that the person asking for access is that person. Anyone can guess a
church member's email address. So an account is only ever claimed through a
single use link sent to that address, never by typing the address into a form.
"""

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

from ..emails import send_email
from ..extensions import db
from ..models import Church, Person, consume_token, issue_token
from ..ratelimit import clear, limit

bp = Blueprint("auth", __name__, url_prefix="/account")

# Shown whether or not the email exists, so the form cannot be used to find out
# who attends this church.
NEUTRAL_MESSAGE = (
    "If that email is in our system, a link is on its way. Check your inbox."
)


def _church() -> Church:
    return Church.query.filter_by(slug=current_app.config["CHURCH_SLUG"]).first_or_404()


def _landing(person: Person) -> str:
    return url_for("staff.dashboard") if person.is_staff else url_for("portal.home")


def _send_link(person: Person, purpose: str) -> None:
    raw, _ = issue_token(person, purpose)
    db.session.commit()
    link = url_for("auth.set_password", token=raw, _external=True)
    if purpose == "reset":
        subject = "Reset your password"
        opening = "Someone asked to reset the password on your account."
    else:
        subject = f"Set up your {current_app.config.get('CHURCH_NAME', 'church')} account"
        opening = "Here is your link to set up your account."
    send_email(
        to=person.email,
        subject=subject,
        html=(
            f"<div style='font-family:Inter,Arial,sans-serif'>"
            f"<p>{person.first_name},</p><p>{opening}</p>"
            f"<p><a href='{link}'>{link}</a></p>"
            f"<p>This link works once and expires in 48 hours. "
            f"If you did not ask for it, ignore this email and nothing changes.</p></div>"
        ),
    )


@bp.route("/login", methods=["GET", "POST"])
@limit("login", limit_count=10, window_seconds=900)
def login():
    if current_user.is_authenticated:
        return redirect(_landing(current_user))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        person = Person.query.filter_by(church_id=_church().id, email=email).first()
        if person and person.check_password(password) and person.is_active_record:
            clear("login")
            login_user(person, remember=True)
            target = request.args.get("next") or ""
            # Never redirect off site on the strength of a query string.
            if not target.startswith("/") or target.startswith("//"):
                target = _landing(person)
            return redirect(target)
        flash("That email and password do not match.", "error")

    return render_template("auth/login.html")


@bp.route("/claim", methods=["GET", "POST"])
@limit("claim", limit_count=6, window_seconds=900)
def claim():
    """Ask for the account link. Never reveals whether the email is known."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        person = Person.query.filter_by(church_id=_church().id, email=email).first()
        if person and person.is_active_record:
            _send_link(person, "reset" if person.password_hash else "claim")
        flash(NEUTRAL_MESSAGE, "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/claim.html")


@bp.route("/forgot", methods=["GET", "POST"])
@limit("claim", limit_count=6, window_seconds=900)
def forgot():
    return claim()


@bp.route("/set-password/<token>", methods=["GET", "POST"])
@limit("set_password", limit_count=10, window_seconds=900)
def set_password(token: str):
    """The only route that can set a password without knowing the old one, and
    it requires a link that was emailed to the address on the record."""
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(password) < 8:
            return render_template(
                "auth/set_password.html", token=token, error="Use at least 8 characters."
            )
        if password != confirm:
            return render_template(
                "auth/set_password.html", token=token, error="Those two do not match."
            )

        person = consume_token(token, "reset") or consume_token(token, "claim")
        if not person:
            db.session.commit()
            return render_template("auth/link_expired.html"), 400

        person.set_password(password)
        db.session.commit()
        login_user(person, remember=True)
        flash("You are all set.", "success")
        return redirect(_landing(person))

    # A GET does not spend the token, so a mail client that prefetches links
    # cannot burn someone's only chance to set a password.
    from ..models import AccessToken, hash_token

    record = AccessToken.query.filter_by(token_hash=hash_token(token)).first()
    if not record or not record.is_valid:
        return render_template("auth/link_expired.html"), 400
    return render_template("auth/set_password.html", token=token, error=None)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
