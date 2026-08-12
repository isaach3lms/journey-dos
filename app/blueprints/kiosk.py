"""
The check in kiosk.

Runs on a tablet at the kids door. It deliberately does not use a staff login:
a volunteer tablet should never hold an admin session. Access is a kiosk PIN
entered once per device, held in the session.

Security model, in plain terms:
- A parent looks up their household by the last four digits of their phone.
- Checking in prints a three digit code onto the child tag and the parent tag.
- Nobody leaves with a child unless the code matches. Checkout requires it.
- Staff can override a checkout from the dashboard, and the override is recorded
  as such.
"""

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..extensions import db, utcnow
from ..ministry import CheckIn, Child, Household, Service, new_security_code
from ..models import Church

bp = Blueprint("kiosk", __name__, url_prefix="/kiosk")

SESSION_KEY = "kiosk_ok"


def church() -> Church:
    return Church.query.filter_by(slug=current_app.config["CHURCH_SLUG"]).first_or_404()


def unlocked() -> bool:
    return bool(session.get(SESSION_KEY))


def active_service(church_id: int):
    """The service closest to now. Check ins attach to it so attendance is
    countable without anyone typing a date."""
    now = utcnow()
    upcoming = (
        Service.query.filter(Service.church_id == church_id, Service.starts_at >= now)
        .order_by(Service.starts_at)
        .first()
    )
    recent = (
        Service.query.filter(Service.church_id == church_id, Service.starts_at < now)
        .order_by(Service.starts_at.desc())
        .first()
    )
    if upcoming and (upcoming.starts_at - now).total_seconds() < 4 * 3600:
        return upcoming
    if recent and (now - recent.starts_at).total_seconds() < 4 * 3600:
        return recent
    return upcoming or recent


@bp.route("/unlock", methods=["GET", "POST"])
def unlock():
    if request.method == "POST":
        pin = (request.form.get("pin") or "").strip()
        expected = current_app.config.get("KIOSK_PIN")
        if expected and pin == expected:
            session[SESSION_KEY] = True
            session.permanent = True
            return redirect(url_for("kiosk.home"))
        flash("That PIN is not right.", "error")
    return render_template("kiosk/unlock.html")


@bp.route("/", methods=["GET", "POST"])
def home():
    if not unlocked():
        return redirect(url_for("kiosk.unlock"))

    church_record = church()
    service = active_service(church_record.id)
    households = []
    searched = False

    if request.method == "POST" and request.form.get("action") == "lookup":
        searched = True
        digits = "".join(c for c in (request.form.get("phone") or "") if c.isdigit())
        if len(digits) >= 4:
            last4 = digits[-4:]
            households = [
                h
                for h in Household.query.filter_by(church_id=church_record.id).all()
                if h.phone_last4 == last4
            ]

    return render_template(
        "kiosk/home.html",
        households=households,
        searched=searched,
        service=service,
        tzname=church_record.timezone or "America/Chicago",
    )


@bp.route("/check-in/<int:household_id>", methods=["POST"])
def check_in(household_id: int):
    if not unlocked():
        return redirect(url_for("kiosk.unlock"))

    church_record = church()
    household = Household.query.filter_by(
        id=household_id, church_id=church_record.id
    ).first_or_404()
    service = active_service(church_record.id)

    selected = request.form.getlist("child_id")
    if not selected:
        flash("Tap at least one child.", "error")
        return redirect(url_for("kiosk.home"))

    # One code per household per check in, so a parent with three kids carries
    # one tag, not three.
    code = new_security_code()
    checked = []
    for child_id in selected:
        child = Child.query.filter_by(
            id=int(child_id), household_id=household.id, church_id=church_record.id
        ).first()
        if not child:
            continue
        already = CheckIn.query.filter_by(
            church_id=church_record.id, child_id=child.id, checked_out_at=None
        ).first()
        if already:
            checked.append((child, already.code))
            continue
        record = CheckIn(
            church_id=church_record.id,
            service_id=service.id if service else None,
            child_id=child.id,
            code=code,
            room=child.room,
            checked_in_by=household.name,
        )
        db.session.add(record)
        checked.append((child, code))

    db.session.commit()
    return render_template(
        "kiosk/tag.html", household=household, checked=checked, code=code, service=service
    )


@bp.route("/check-out", methods=["GET", "POST"])
def check_out():
    if not unlocked():
        return redirect(url_for("kiosk.unlock"))

    church_record = church()
    matches = []
    searched = False

    if request.method == "POST":
        searched = True
        code = "".join(c for c in (request.form.get("code") or "") if c.isdigit())[:3]
        if request.form.get("action") == "release":
            record = CheckIn.query.filter_by(
                id=request.form.get("checkin_id", type=int),
                church_id=church_record.id,
                checked_out_at=None,
            ).first_or_404()
            if record.code != code:
                flash("That code does not match. Do not release the child.", "error")
            else:
                record.checked_out_at = utcnow()
                record.checked_out_by = f"code {code}"
                db.session.commit()
                flash(f"{record.child.full_name} released.", "success")
                return redirect(url_for("kiosk.check_out"))
        elif code:
            matches = CheckIn.query.filter_by(
                church_id=church_record.id, code=code, checked_out_at=None
            ).all()

    open_count = CheckIn.query.filter_by(
        church_id=church_record.id, checked_out_at=None
    ).count()
    return render_template(
        "kiosk/checkout.html", matches=matches, searched=searched, open_count=open_count
    )


@bp.route("/lock")
def lock():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("kiosk.unlock"))
