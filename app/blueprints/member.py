"""The member app.

The same database, the same brand, a different reader. A member sees their own
record and nothing else, and that is enforced by only ever loading
`current_user.person` rather than by taking an id from a URL. There is no route
in this blueprint that accepts a person id, so there is no way to ask it for
somebody else's record.

Staff and leaders can reach it too, showing their own record. That is a preview
of what the church's people see, not impersonation: there is deliberately no
way for a staff member to view the member app *as* another person. Reading a
member's screen through their eyes would mean a staff account could see a
private view with no audit trail, and nothing in this increment needs it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.categories import CATEGORIES, OPTIONAL_CATEGORIES
from app.content import MEMBER
from app.extensions import db
from app.mail import opt_in, opt_out
from app.models import NextStep
from app.stages import STAGE_BY_CODE, stages_for

bp = Blueprint("member", __name__, url_prefix="/me")


def _greeting(name: str) -> str:
    """Time of day from the server's clock.

    A per-church timezone lands with Services at increment 10, which is the
    first feature where being an hour out actually matters. Until then UTC is
    honest about being approximate rather than pretending otherwise.
    """
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        key = "greeting_morning"
    elif hour < 17:
        key = "greeting_afternoon"
    else:
        key = "greeting_evening"
    return MEMBER[key].format(name=name)


def _base_context(person):
    return {
        "church": g.church,
        "content": MEMBER,
        "person": person,
        "is_preview": current_user.role != "member",
    }


@bp.get("/")
@login_required
def home():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    open_steps = db.session.scalars(
        NextStep.open_for_person(g.church.id, person.id)
    ).all()

    days = None
    if person.first_seen_on:
        days = (datetime.now(timezone.utc).date() - person.first_seen_on).days + 1

    return render_template(
        "member/home.html",
        greeting=_greeting(person.first_name),
        days=days,
        open_steps=open_steps,
        stage=STAGE_BY_CODE.get(person.stage),
        stages=stages_for(g.church),
        tab="home",
        **_base_context(person),
    )


@bp.get("/you/")
@login_required
def you():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    household_members = []
    pin = None
    if person.household is not None:
        household_members = [
            member for member in person.household.members if member.id != person.id
        ]
        # Minted on first view rather than at household creation. Most
        # households never open this screen, and an unused code is one more
        # secret to look after for no benefit.
        pin = person.household.ensure_checkin_pin()
        db.session.commit()

    return render_template(
        "member/you.html",
        household_members=household_members,
        pin=pin,
        categories=CATEGORIES,
        optional_categories=OPTIONAL_CATEGORIES,
        tab="you",
        **_base_context(person),
    )


@bp.post("/you/preferences/")
@login_required
def set_preferences():
    """A member changing their own preferences. No id in the request."""
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    for category in OPTIONAL_CATEGORIES:
        person.set_preference(
            category.code, request.form.get(f"cat_{category.code}") == "on"
        )
    db.session.commit()

    flash(MEMBER["prefs_saved"], "notice")
    return redirect(url_for("member.you"))


@bp.post("/you/optout/")
@login_required
def toggle_opt_out():
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    if person.has_opted_out:
        opt_in(person)
    else:
        opt_out(person, reason="Turned off by the member in the app")
    db.session.commit()

    return redirect(url_for("member.you"))
