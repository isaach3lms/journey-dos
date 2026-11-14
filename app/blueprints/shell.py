"""The application shell.

Increment 0 ships the frame, not the features. Every nav item resolves to a
placeholder that names the increment it arrives in, so the shell can be walked
end to end without dead links.
"""

from types import SimpleNamespace

from flask import (
    Blueprint, abort, g, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

from app.content import (
    DASHBOARD,
    METRICS,
    INCREMENT_NAMES,
    AUTOMATION,
    NAV_ITEMS,
    PEOPLE,
    SHELL,
    STUCK,
)
from app.models import Person, SequenceEnrollment
from app.extensions import db
from app.dashboard import build as build_dashboard
from app.security import min_role
from app.stages import CONTACT_WINDOW_DAYS, stages_for

# Where "Request support" goes. Between Sundays supports every church on the
# platform, so this is the platform's address, not a church setting.
SUPPORT_EMAIL = "isaac@betweensundaysconsulting.com"

bp = Blueprint("shell", __name__)

_BY_KEY = {item.key: item for item in NAV_ITEMS}


@bp.get("/")
@login_required
def index():
    # A member has no business on the staff dashboard. Redirecting rather than
    # rendering a stripped-down version means there is one dashboard to
    # maintain instead of two that drift apart.
    if current_user.role == "member":
        return redirect(url_for("member.home"))

    # Members do not see the rail. It is a staff and leader view of everyone
    # else, which is not a thing a member should be handed.
    show_rail = current_user.at_least("leader")

    data = build_dashboard(g.church) if show_rail else {}
    return render_template(
        "shell/index.html",
        church=g.church,
        content=SHELL,
        dash=DASHBOARD,
        people_content=PEOPLE,
        show_rail=show_rail,
        stuck_content=STUCK,
        flagged=db.session.scalars(Person.stuck(g.church.id, limit=5)).all()
        if show_rail else [],
        contact_window=CONTACT_WINDOW_DAYS,
        automation=AUTOMATION,
        active="dashboard",
        user=current_user,
        support_email=SUPPORT_EMAIL,
        **data,
    )


@bp.get("/metrics/")
@login_required
@min_role("leader")
def metrics():
    """What people did, and what moved.

    Declared before the `/<key>/` catch-all below for readability; Flask
    matches a static rule ahead of a converter either way.

    Leader and above. The numbers are counts rather than names, but they are
    still a picture of a congregation's giving, prayer and attendance, which
    is not something a member should be able to read off a URL.
    """
    from app import metrics as metrics_module

    period = (request.args.get("period") or "week").strip()
    builder = {
        "week": metrics_module.current_week,
        "last_week": metrics_module.week_window,
        "month": metrics_module.current_month,
        "last_month": metrics_module.month_window,
    }.get(period)
    if builder is None:
        abort(404)

    window = builder(g.church)
    sections = metrics_module.sections_for(g.church, window)

    return render_template(
        "shell/metrics.html",
        church=g.church,
        content=METRICS,
        window=window,
        sections=sections,
        anything=any(section.total for section in sections),
        versus=METRICS["vs_week"] if window.kind == "week" else METRICS["vs_month"],
        active=period,
        windows=(
            SimpleNamespace(key="week", label=METRICS["this_week"]),
            SimpleNamespace(key="last_week", label=METRICS["last_week"]),
            SimpleNamespace(key="month", label=METRICS["this_month"]),
            SimpleNamespace(key="last_month", label=METRICS["last_month"]),
        ),
    )


@bp.get("/<key>/")
@login_required
def placeholder(key: str):
    item = _BY_KEY.get(key)
    if item is None:
        abort(404)
    # The nav hides what a role cannot reach; the route is what enforces it.
    # A hidden link is presentation, not a permission.
    if current_user.role not in item.roles:
        abort(403)
    return render_template(
        "shell/placeholder.html",
        church=g.church,
        content=SHELL,
        item=item,
        active=key,
        increment_name=INCREMENT_NAMES.get(item.increment, item.label),
    )
