"""Settings, support, and the audit surface.

Staff only, all of it. Branding changes what every member sees, and the audit
log is a record of who did what, which is not a thing to hand a volunteer.
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

from app.audit import record
from app.brand import assert_accent_readable
from app.content import DOS_PRICE_CENTS, INCLUDED_NOT_SAVED, REPLACES, SETTINGS
from app.extensions import db
from app.models import AuditEvent, BibleVerse, PasswordResetToken, User
from app.models.audit import ROLE_CHANGED
from app.models.password_reset import LIFETIME_MINUTES
from app.models.user import ROLES
from app.models.audit import ACTIONS, BRAND_CHANGED, RETENTION_DAYS
from app.security import min_role
from app.timeutil import COMMON_TIMEZONES, is_valid_timezone

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _cost_context() -> dict:
    replaced = sum(item["cents"] for item in REPLACES)
    return {
        "replaces": REPLACES,
        "included": INCLUDED_NOT_SAVED,
        "replaced_cents": replaced,
        "ours_cents": DOS_PRICE_CENTS,
        "saving_cents": max(0, replaced - DOS_PRICE_CENTS),
    }


@bp.get("/")
@login_required
@min_role("staff")
def index():
    action = (request.args.get("action") or "").strip() or None
    if action and action not in ACTIONS:
        action = None

    return render_template(
        "settings/index.html",
        church=g.church,
        content=SETTINGS,
        timezones=COMMON_TIMEZONES,
        events=db.session.scalars(
            AuditEvent.recent(g.church.id, action=action)
        ).all(),
        actions=ACTIONS,
        active_action=action,
        audit_count=AuditEvent.count_since(g.church.id, 30),
        retention_days=RETENTION_DAYS,
        active="settings",
        users=db.session.scalars(User.for_church(g.church.id)).all(),
        roles=ROLES,
        bible_verses=BibleVerse.verse_count(),
        bible_books=len(BibleVerse.loaded_books()),
        **_cost_context(),
    )


@bp.post("/brand/")
@login_required
@min_role("staff")
def save_brand():
    church = g.church
    changes = []

    name = (request.form.get("name") or "").strip()
    if name and name != church.name:
        changes.append(f"name {church.name} to {name}")
        church.name = name[:120]

    # `in request.form` rather than `.get(...) or None`. A field the browser
    # did not send is not the same as a field somebody cleared, and treating
    # them alike wipes a value and records a change nobody made.
    if "app_name" in request.form:
        app_name = request.form["app_name"].strip() or None
        if app_name != church.app_name:
            changes.append("app name")
            church.app_name = app_name

    if "app_domain" in request.form:
        app_domain = request.form["app_domain"].strip() or None
        if app_domain != church.app_domain:
            changes.append("app domain")
            church.app_domain = app_domain

    timezone = (request.form.get("timezone") or "").strip()
    if timezone and timezone != church.timezone:
        if not is_valid_timezone(timezone):
            flash(SETTINGS["timezone_rejected"].format(value=timezone), "error")
            return redirect(url_for("settings.index"))
        changes.append(f"timezone to {timezone}")
        church.timezone = timezone

    accent = (request.form.get("accent_hex") or "").strip()
    if accent and accent != church.accent_hex:
        try:
            # The guard written in increment 0, finally wired to a form. A
            # pastor pasting a pale yellow is stopped here rather than
            # discovered by a volunteer squinting at a button in a lobby.
            assert_accent_readable(accent)
        except ValueError as exc:
            flash(SETTINGS["accent_rejected"].format(reason=str(exc)), "error")
            return redirect(url_for("settings.index"))
        changes.append(f"colour to {accent}")
        church.accent_hex = accent

    if changes:
        record(
            BRAND_CHANGED,
            "Branding changed",
            actor=current_user,
            subject_type="church",
            subject_id=church.id,
            subject_label=church.name,
            detail="Changed: " + ", ".join(changes),
        )
    db.session.commit()

    flash(SETTINGS["brand_saved"], "notice")
    return redirect(url_for("settings.index"))


@bp.post("/signup/")
@login_required
@min_role("staff")
def toggle_signup():
    """Open or close self-registration.

    Staff only, and audited, because it changes who can see the church's
    announcements.
    """
    church = g.church
    church.allow_self_signup = not church.allow_self_signup

    record(
        BRAND_CHANGED,
        "Self-registration turned "
        + ("on" if church.allow_self_signup else "off"),
        actor=current_user,
        subject_type="church",
        subject_id=church.id,
        subject_label=church.name,
    )
    db.session.commit()

    flash(
        SETTINGS["signup_changed_on"] if church.allow_self_signup
        else SETTINGS["signup_changed_off"],
        "notice",
    )
    return redirect(url_for("settings.index"))


# ---------------------------------------------------------------------------
# Accounts
#
# Staff only. These decide who can see the roster, the giving, and this page.
# ---------------------------------------------------------------------------

def _send_set_password(user, actor) -> bool:
    """Email a link so the person chooses their own password.

    Staff never type one. A password a staff member sets has to be told to
    somebody over text or in a hallway, and is then a password two people
    know, one of whom wrote it down.
    """
    from app.mail import NotQueued, queue

    PasswordResetToken.invalidate_all_for(user)
    _, raw = PasswordResetToken.issue(user)
    db.session.flush()

    link = url_for(
        "auth.reset", token=raw, _external=True,
        _scheme="https" if request.is_secure else "http",
    )
    try:
        queue(
            church_id=g.church.id,
            # Transactional: it is the account itself, not church news.
            category="account",
            subject=SETTINGS["invite_subject"].format(church=g.church.name),
            body_text=SETTINGS["invite_body"].format(
                name=user.name, church=g.church.name,
                actor=getattr(actor, "name", "Someone"),
                link=link, minutes=LIFETIME_MINUTES,
            ),
            to_email=user.email,
            to_name=user.name,
        )
    except NotQueued:
        return False
    return True


@bp.post("/accounts/")
@login_required
@min_role("staff")
def create_account():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "member").strip()

    if not name or not email:
        flash(SETTINGS["accounts_name_required"], "error")
        return redirect(url_for("settings.index"))
    if role not in ROLES:
        abort(400)

    if User.by_email(g.church.id, email) is not None:
        flash(SETTINGS["accounts_exists"].format(email=email), "error")
        return redirect(url_for("settings.index"))

    user = User(
        church_id=g.church.id, email=email, name=name[:120], role=role
    )
    # A staff member typing an address vouches for it more strongly than a
    # click in an inbox, so the account is verified on creation.
    user.mark_verified()
    user.set_unusable_password()
    db.session.add(user)
    db.session.flush()

    user.link_person_by_email()
    _send_set_password(user, current_user)

    record(
        ROLE_CHANGED,
        f"{user.name} was given a {role} account",
        actor=current_user,
        subject_type="user", subject_id=user.id, subject_label=user.name,
    )
    db.session.commit()

    flash(SETTINGS["accounts_created"].format(name=user.name), "notice")
    return redirect(url_for("settings.index"))


@bp.post("/accounts/<int:user_id>/role/")
@login_required
@min_role("staff")
def change_role(user_id: int):
    user = User.get_for_church(g.church.id, user_id)
    if user is None:
        abort(404)

    role = (request.form.get("role") or "").strip()
    if role not in ROLES:
        abort(400)

    if user.id == current_user.id:
        # Changing your own access from this screen is how somebody demotes
        # themselves out of the screen they are standing on.
        flash(SETTINGS["account_self"], "error")
        return redirect(url_for("settings.index"))

    if (
        user.role == "staff"
        and role != "staff"
        and User.active_staff_count(g.church.id, excluding=user.id) == 0
    ):
        flash(SETTINGS["account_last_staff"], "error")
        return redirect(url_for("settings.index"))

    previous, user.role = user.role, role
    record(
        ROLE_CHANGED,
        f"{user.name} changed from {previous} to {role}",
        actor=current_user,
        subject_type="user", subject_id=user.id, subject_label=user.name,
    )
    db.session.commit()

    flash(SETTINGS["role_changed"].format(name=user.name, role=role), "notice")
    return redirect(url_for("settings.index"))


@bp.post("/accounts/<int:user_id>/toggle/")
@login_required
@min_role("staff")
def toggle_account(user_id: int):
    user = User.get_for_church(g.church.id, user_id)
    if user is None:
        abort(404)

    if user.id == current_user.id:
        flash(SETTINGS["account_self"], "error")
        return redirect(url_for("settings.index"))

    if (
        user.is_active_account
        and user.role == "staff"
        and User.active_staff_count(g.church.id, excluding=user.id) == 0
    ):
        flash(SETTINGS["account_last_staff"], "error")
        return redirect(url_for("settings.index"))

    user.is_active_account = not user.is_active_account
    if not user.is_active_account:
        # Switching an account off signs it out everywhere, for the same
        # reason a password reset does.
        user.session_version = (user.session_version or 1) + 1

    record(
        ROLE_CHANGED,
        f"{user.name} was switched "
        + ("back on" if user.is_active_account else "off"),
        actor=current_user,
        subject_type="user", subject_id=user.id, subject_label=user.name,
    )
    db.session.commit()

    flash(
        (SETTINGS["account_reactivated"] if user.is_active_account
         else SETTINGS["account_deactivated"]).format(name=user.name),
        "notice",
    )
    return redirect(url_for("settings.index"))


@bp.post("/accounts/<int:user_id>/resend/")
@login_required
@min_role("staff")
def resend_invite(user_id: int):
    user = User.get_for_church(g.church.id, user_id)
    if user is None:
        abort(404)

    _send_set_password(user, current_user)
    db.session.commit()

    flash(SETTINGS["account_resent"].format(email=user.email), "notice")
    return redirect(url_for("settings.index"))
