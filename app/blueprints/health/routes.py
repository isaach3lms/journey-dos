"""Health checks.

Two endpoints, deliberately different:

- /livez answers without touching the database or resolving a tenant. Render
  uses it to decide whether the process is alive. If this needed the database,
  a database blip would trigger a restart loop that cannot fix anything.
- /readyz runs one trivial query. Use it for deploy verification and for the
  scheduler to confirm the app is serving before it starts a run.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.get("/livez")
def livez():
    return jsonify(status="ok", env=current_app.config["APP_ENV"]), 200


@bp.get("/readyz")
def readyz():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("readyz failed: %s", exc)
        return jsonify(status="degraded", database="unreachable"), 503
    return jsonify(status="ok", database="ok"), 200
