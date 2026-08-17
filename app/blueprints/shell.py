"""The application shell.

Increment 0 ships the frame, not the features. Every nav item resolves to a
placeholder that names the increment it arrives in, so the shell can be walked
end to end without dead links.
"""

from flask import Blueprint, abort, g, render_template

from app.content import INCREMENT_NAMES, NAV_ITEMS, SHELL

bp = Blueprint("shell", __name__)

_BY_KEY = {item.key: item for item in NAV_ITEMS}


@bp.get("/")
def index():
    return render_template(
        "shell/index.html",
        church=g.church,
        content=SHELL,
        active="dashboard",
        increment_names=INCREMENT_NAMES,
    )


@bp.get("/<key>/")
def placeholder(key: str):
    item = _BY_KEY.get(key)
    if item is None:
        abort(404)
    return render_template(
        "shell/placeholder.html",
        church=g.church,
        content=SHELL,
        item=item,
        active=key,
        increment_name=INCREMENT_NAMES[item.increment],
    )
