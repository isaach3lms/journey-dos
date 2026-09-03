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

from app.audit import record
from app.content import AUTH
from app.models.audit import PASSWORD_RESET, SIGN_IN, SIGN_IN_FAILED
from app.extensions import db
from app.mail import NotQueued, queue
from app.forms import ForgotPasswordForm, LoginForm, ResetPasswordForm
from app.models import PasswordResetToken, User
from app.models.password_reset import LIFETIME_MINUTES, MAX_REQUESTS_PER_HOUR
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
            # The address is not recorded. A log of attempted addresses is a
            # list of who somebody thinks attends this church.
            record(SIGN_IN_FAILED, "Failed sign-in", actor=None)
            db.session.commit()
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
            record(
                SIGN_IN_FAILED, "Failed sign-in", actor=None,
                subject_type="user", subject_id=user.id, subject_label=user.name,
            )
            flash(AUTH["failed"], "error")
            return render_template(
                "auth/login.html", church=g.church, form=form, content=AUTH
            ), 401

        user.register_successful_login()
        record(
            SIGN_IN, f"{user.name} signed in", actor=user,
            subject_type="user", subject_id=user.id, subject_label=user.name,
        )
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


# ---------------------------------------------------------------------------
# Password reset
#
# Both routes are public, because somebody who cannot sign in cannot be asked
# to sign in first. Everything they touch is scoped to the church resolved from
# the host, so a token minted at one tenant is inert at another.
# ---------------------------------------------------------------------------

@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if current_user.is_authenticated:
        return redirect(url_for("shell.index"))

    form = ForgotPasswordForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.by_email(g.church.id, email)

        # Everything below is conditional, and the response is not. A form that
        # says "no such account" is a way to find out who attends the church.
        if user is not None and user.is_active_account:
            if PasswordResetToken.recent_request_count(user) >= MAX_REQUESTS_PER_HOUR:
                current_app.logger.warning(
                    "Reset rate limit hit for user %s at church %s",
                    user.id,
                    g.church.id,
                )
            else:
                token, raw = PasswordResetToken.issue(
                    user, requested_ip=request.headers.get("X-Forwarded-For")
                )
                db.session.flush()

                link = url_for(
                    "auth.reset", token=raw, _external=True, _scheme="https"
                    if request.is_secure else "http"
                )
                try:
                    queue(
                        church_id=g.church.id,
                        # Transactional. It reaches somebody who unsubscribed
                        # from everything else, which is the entire reason that
                        # distinction exists.
                        category="account",
                        subject=AUTH["reset_email_subject"].format(church=g.church.name),
                        body_text=AUTH["reset_email_body"].format(
                            name=user.name,
                            church=g.church.name,
                            link=link,
                            minutes=LIFETIME_MINUTES,
                        ),
                        to_email=user.email,
                        to_name=user.name,
                    )
                except NotQueued as exc:
                    current_app.logger.error("Reset email not queued: %s", exc)
                db.session.commit()

        flash(AUTH["forgot_sent"], "notice")
        return render_template(
            "auth/forgot.html", church=g.church, form=ForgotPasswordForm(), content=AUTH
        )

    return render_template("auth/forgot.html", church=g.church, form=form, content=AUTH)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token: str):
    reset_token = PasswordResetToken.redeem(g.church.id, token)
    if reset_token is None:
        return render_template(
            "auth/reset_invalid.html", church=g.church, content=AUTH
        ), 404

    form = ResetPasswordForm()

    if form.validate_on_submit():
        user = reset_token.user

        # Order matters. Consume first, so a failure below cannot leave a token
        # that has already changed a password still usable.
        reset_token.consume()
        PasswordResetToken.invalidate_all_for(user)

        try:
            user.set_password(form.password.data)
        except ValueError as exc:
            db.session.rollback()
            form.password.errors.append(str(exc))
            return render_template(
                "auth/reset.html", church=g.church, form=form,
                content=AUTH, token=token,
            )

        # A reset is also a way back in for someone locked out by failed
        # attempts, so clear that too.
        user.failed_login_count = 0
        user.locked_until = None
        record(
            PASSWORD_RESET, f"{user.name} reset their password", actor=user,
            subject_type="user", subject_id=user.id, subject_label=user.name,
            detail="Every other signed-in device was signed out.",
        )
        db.session.commit()

        # set_password bumped session_version, so every other device is now
        # signed out. This login mints a cookie carrying the new version.
        login_user(user)
        current_app.logger.info(
            "Password reset completed for user %s at church %s", user.id, g.church.id
        )

        flash(AUTH["reset_done"] + " " + AUTH["reset_signed_out_elsewhere"], "notice")
        return redirect(url_for("shell.index"))

    return render_template(
        "auth/reset.html", church=g.church, form=form, content=AUTH, token=token
    )
