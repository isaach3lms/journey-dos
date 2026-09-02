"""Kids check-in.

**The kiosk requires a signed-in staff or leader session.** A tablet in a lobby
accepting PIN attempts from anyone who walks past is a device that will
eventually enumerate every household code in the church. The volunteer running
the desk signs the tablet in once on a Sunday morning; the families who use it
never see a login. Attempts are also rate limited per browser session, so a
tablet left unattended still cannot be walked through the code space.

Nothing here accepts a household PIN where a pickup code belongs, or the
reverse. See `app/pickup.py` for why that separation is the whole safety model.
"""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from app.content import KIDS
from app.extensions import db
from app.mail import NotQueued, queue
from app.models import Checkin, CheckinSession, Household, Person
from app.pickup import normalize
from app.security import min_role
from app.timeutil import from_local

bp = Blueprint("kids", __name__, url_prefix="/kids")

# A tablet in a lobby is physically available to anyone. Ten wrong codes in a
# row is far past a parent mistyping and well short of walking the space.
MAX_PIN_ATTEMPTS = 10
ATTEMPT_KEY = "kiosk_pin_attempts"


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

@bp.get("/")
@login_required
@min_role("leader")
def index():
    return render_template(
        "kids/index.html",
        church=g.church,
        content=KIDS,
        open_session=CheckinSession.open_session(g.church.id),
        sessions=db.session.scalars(CheckinSession.recent(g.church.id)).all(),
        active="kids",
    )


@bp.post("/sessions/")
@login_required
@min_role("leader")
def open_session():
    raw = (request.form.get("starts_at") or "").strip()
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        flash(KIDS["bad_time"], "error")
        return redirect(url_for("kids.index"))

    checkin_session = CheckinSession(
        church_id=g.church.id,
        name=(request.form.get("name") or "Sunday").strip()[:160] or "Sunday",
        starts_at=from_local(naive, g.church),
    )
    db.session.add(checkin_session)
    db.session.commit()

    flash(KIDS["opened"].format(name=checkin_session.name), "notice")
    return redirect(url_for("kids.index"))


@bp.post("/sessions/<int:session_id>/toggle/")
@login_required
@min_role("leader")
def toggle_session(session_id: int):
    checkin_session = CheckinSession.get_for_church(g.church.id, session_id)
    if checkin_session is None:
        abort(404)

    if checkin_session.is_open:
        # Closing never checks anyone out. A child still in a room at the end
        # of a service is exactly what staff need to see.
        checkin_session.close()
        flash(KIDS["closed"].format(name=checkin_session.name), "notice")
    else:
        checkin_session.reopen()
        flash(KIDS["opened"].format(name=checkin_session.name), "notice")
    db.session.commit()

    return redirect(url_for("kids.index"))


# ---------------------------------------------------------------------------
# Kiosk
# ---------------------------------------------------------------------------

def _attempts() -> int:
    return session.get(ATTEMPT_KEY, 0)


def _record_failed_attempt() -> None:
    session[ATTEMPT_KEY] = _attempts() + 1


def _clear_attempts() -> None:
    session.pop(ATTEMPT_KEY, None)


@bp.get("/kiosk/")
@login_required
@min_role("leader")
def kiosk():
    return render_template(
        "kids/kiosk.html",
        church=g.church,
        content=KIDS,
        open_session=CheckinSession.open_session(g.church.id),
        locked=_attempts() >= MAX_PIN_ATTEMPTS,
    )


@bp.post("/kiosk/")
@login_required
@min_role("leader")
def kiosk_pin():
    checkin_session = CheckinSession.open_session(g.church.id)
    if checkin_session is None:
        flash(KIDS["kiosk_closed"], "error")
        return redirect(url_for("kids.kiosk"))

    if _attempts() >= MAX_PIN_ATTEMPTS:
        flash(KIDS["kiosk_too_many"], "error")
        return redirect(url_for("kids.kiosk"))

    pin = normalize(request.form.get("pin"))
    household = db.session.scalar(
        db.select(Household).where(
            Household.church_id == g.church.id, Household.checkin_pin == pin
        )
    ) if pin else None

    if household is None:
        _record_failed_attempt()
        flash(KIDS["kiosk_unknown"], "error")
        return redirect(url_for("kids.kiosk"))

    _clear_attempts()
    return redirect(
        url_for("kids.kiosk_family", household_id=household.id)
    )


@bp.get("/kiosk/family/<int:household_id>/")
@login_required
@min_role("leader")
def kiosk_family(household_id: int):
    checkin_session = CheckinSession.open_session(g.church.id)
    household = Household.get_for_church(g.church.id, household_id)
    if checkin_session is None or household is None:
        abort(404)

    already = {
        c.person_id for c in checkin_session.checkins if c.household_id == household.id
    }
    return render_template(
        "kids/family.html",
        church=g.church,
        content=KIDS,
        household=household,
        members=household.members,
        already=already,
        open_session=checkin_session,
    )


@bp.post("/kiosk/family/<int:household_id>/")
@login_required
@min_role("leader")
def kiosk_check_in(household_id: int):
    checkin_session = CheckinSession.open_session(g.church.id)
    household = Household.get_for_church(g.church.id, household_id)
    if checkin_session is None or household is None:
        abort(404)

    person_ids = request.form.getlist("person_id", type=int)
    if not person_ids:
        flash(KIDS["check_in_none"], "error")
        return redirect(url_for("kids.kiosk_family", household_id=household.id))

    # One code for the whole household, so a parent carries one, not three.
    code = checkin_session.issue_pickup_code(household.id)
    checked_in = []

    for person_id in person_ids:
        person = Person.get_for_church(g.church.id, person_id)
        # A person id from outside this household would put somebody else's
        # child under this family's pickup code.
        if person is None or person.household_id != household.id:
            continue
        if any(
            c.person_id == person.id and c.household_id == household.id
            for c in checkin_session.checkins
        ):
            continue

        db.session.add(
            Checkin(
                church_id=g.church.id,
                session_id=checkin_session.id,
                person_id=person.id,
                household_id=household.id,
                # Copied so a household rename or a child moving later does not
                # rewrite what happened this Sunday.
                household_name=household.name,
                pickup_code=code,
                checked_in_by_user_id=current_user.id,
            )
        )
        checked_in.append(person)

    db.session.commit()

    return render_template(
        "kids/label.html",
        church=g.church,
        content=KIDS,
        household=household,
        code=code,
        checked_in=checked_in,
    )


@bp.route("/kiosk/forgot/", methods=["GET", "POST"])
@login_required
@min_role("leader")
def kiosk_forgot():
    """Email the household's check-in code.

    The spec says text it. There is no SMS provider in this system yet, and
    inventing one here would mean a second vendor, a second set of credentials,
    and a second thing to rotate. Email uses the outbox that already exists,
    and the `account` category means it reaches somebody who unsubscribed from
    everything else.
    """
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        person = db.session.scalar(
            db.select(Person).where(
                Person.church_id == g.church.id,
                Person.email == email,
                Person.is_archived.is_(False),
            )
        ) if email else None

        # Everything below is conditional and the response is not. A kiosk that
        # says "no such address" is a device for finding out who attends.
        if person is not None and person.household is not None:
            pin = person.household.ensure_checkin_pin()
            try:
                queue(
                    church_id=g.church.id,
                    category="kids_checkin",
                    subject=KIDS["forgot_email_subject"].format(church=g.church.name),
                    body_text=KIDS["forgot_email_body"].format(
                        name=person.first_name, church=g.church.name, pin=pin
                    ),
                    person=person,
                )
            except NotQueued:
                pass
            db.session.commit()

        flash(KIDS["forgot_sent"], "notice")
        return redirect(url_for("kids.kiosk"))

    return render_template("kids/forgot.html", church=g.church, content=KIDS)


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------

@bp.get("/checkout/")
@login_required
@min_role("leader")
def checkout():
    checkin_session = CheckinSession.open_session(g.church.id)
    code = normalize(request.args.get("code"))
    matches = (
        Checkin.by_pickup_code(g.church.id, checkin_session.id, code)
        if checkin_session and code
        else []
    )
    return render_template(
        "kids/checkout.html",
        church=g.church,
        content=KIDS,
        open_session=checkin_session,
        code=code,
        matches=matches,
        searched=bool(code),
    )


@bp.post("/checkout/")
@login_required
@min_role("leader")
def do_checkout():
    checkin_session = CheckinSession.open_session(g.church.id)
    if checkin_session is None:
        abort(404)

    code = normalize(request.form.get("code"))
    checkin_ids = request.form.getlist("checkin_id", type=int)
    if not checkin_ids:
        flash(KIDS["checkout_none"], "error")
        return redirect(url_for("kids.checkout", code=code))

    # Re-derive from the code rather than trusting the ids in the form. An id
    # alone would let a posted request check out a child whose code the person
    # at the desk never had.
    allowed = {c.id: c for c in Checkin.by_pickup_code(g.church.id, checkin_session.id, code)}

    collected_by = (request.form.get("collected_by") or "").strip()
    names = []
    for checkin_id in checkin_ids:
        checkin = allowed.get(checkin_id)
        if checkin is None or not checkin.is_present:
            continue
        checkin.check_out(collected_by=collected_by, user=current_user)
        names.append(checkin.person.full_name)

    db.session.commit()

    if names:
        flash(KIDS["checkout_done"].format(names=", ".join(names)), "notice")
    else:
        flash(KIDS["checkout_already"], "error")
    return redirect(url_for("kids.checkout"))
