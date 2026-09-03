"""Settings, support, and the audit surface.

Staff only, all of it. Branding changes what every member sees, and the audit
log is a record of who did what, which is not a thing to hand a volunteer.
"""

from __future__ import annotations

from flask import (
    Blueprint,
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
from app.models import AuditEvent, BibleVerse
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
