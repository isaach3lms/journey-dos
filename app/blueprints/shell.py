"""The application shell.

Increment 0 ships the frame, not the features. Every nav item resolves to a
placeholder that names the increment it arrives in, so the shell can be walked
end to end without dead links.
"""

from flask import Blueprint, abort, g, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.content import (
    DASHBOARD,
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
