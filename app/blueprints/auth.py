"""Sign in and sign out.

Every lookup here is scoped to `g.church`. No query in this module can reach a
user at another church, by construction rather than by filtering afterwards.

The failure message is deliberately identical whether the email is unknown,
the password is wrong, or the account is deactivated. A login form that
distinguishes them tells an outsider who attends the church, which for a small
congregation is a real privacy leak rather than a theoretical one.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.content import AUTH
from app.extensions import db
from app.forms import LoginForm
from app.models import User
from app.security import safe_next_url

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("shell.index"))

    form = LoginForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.by_email(g.church.id, email)

        if user is None:
            # Spend the same time as a real check so the form cannot be used
            # to discover which addresses have accounts.
            User.burn_timing_budget(form.password.data)
            current_app.logger.info(
                "Failed login for unknown address at church %s", g.church.id
            )
            flash(AUTH["failed"], "error")
            return render_template(
                "auth/login.html", church=g.church, form=form, content=AUTH
            ), 401

        if user.is_locked:
            flash(AUTH["locked"], "error")
            return render_template(
                "auth/login.html", church=g.church, form=form, content=AUTH
            ), 429

        if not user.check_password(form.password.data) or not user.is_active_account:
            user.register_failed_login()
            db.session.commit()
            current_app.logger.info(
                "Failed login for user %s at church %s", user.id, g.church.id
            )
            flash(AUTH["failed"], "error")
            return render_template(
                "auth/login.html", church=g.church, form=form, content=AUTH
            ), 401

        user.register_successful_login()
        db.session.commit()

        # Flask-Login rotates the session on login, which retires any
        # pre-authentication session identifier an attacker could have planted.
        login_user(user, remember=bool(form.remember.data))
        current_app.logger.info("User %s signed in at church %s", user.id, g.church.id)

        return redirect(safe_next_url(request.args.get("next"), "shell.index"))

    return render_template("auth/login.html", church=g.church, form=form, content=AUTH)


@bp.post("/logout")
@login_required
def logout():
    """POST only. A GET logout can be triggered by any image tag on any page."""
    logout_user()
    flash(AUTH["signed_out"], "notice")
    return redirect(url_for("auth.login"))
