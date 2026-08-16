"""Public shell.

Increment 0 ships one route so that tenant resolution and brand tokens are
demonstrable end to end. The connect card lands here at increment 6.
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template

from app.tenancy import current_church

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    if current_church is None or current_church._get_current_object() is None:
        # Unknown host. Never fall back to an arbitrary tenant.
        abort(404)
    return render_template("public/index.html")
