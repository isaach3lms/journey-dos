"""The application shell.

Increment 0 ships the frame, not the features. Every nav item resolves to a
placeholder that names the increment it arrives in, so the shell can be walked
end to end without dead links.
"""

from flask import Blueprint, abort, g, render_template
from flask_login import current_user, login_required

from app.content import INCREMENT_NAMES, NAV_ITEMS, PEOPLE, SHELL, SHIPPED_INCREMENTS, STUCK
from app.models import Person
from app.extensions import db
from app.stages import CONTACT_WINDOW_DAYS, stages_for

bp = Blueprint("shell", __name__)

_BY_KEY = {item.key: item for item in NAV_ITEMS}


@bp.get("/")
@login_required
def index():
    # Members do not see the rail. It is a staff and leader view of everyone
    # else, which is not a thing a member should be handed.
    show_rail = current_user.at_least("leader")

    return render_template(
        "shell/index.html",
        church=g.church,
        content=SHELL,
        people_content=PEOPLE,
        show_rail=show_rail,
        stages=stages_for(g.church) if show_rail else (),
        counts=Person.stage_counts(g.church.id) if show_rail else {},
        total=Person.total_for_church(g.church.id) if show_rail else 0,
        active_stage=None,
        stuck_content=STUCK,
        flagged=db.session.scalars(Person.stuck(g.church.id, limit=5)).all()
        if show_rail else [],
        stuck_count=Person.stuck_count(g.church.id) if show_rail else 0,
        contacted_count=Person.contacted_since(g.church.id, 7) if show_rail else 0,
        unowned_count=Person.unowned_count(g.church.id) if show_rail else 0,
        contact_window=CONTACT_WINDOW_DAYS,
        active="dashboard",
        increment_names=INCREMENT_NAMES,
        shipped=SHIPPED_INCREMENTS,
        user=current_user,
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
        increment_name=INCREMENT_NAMES[item.increment],
    )
