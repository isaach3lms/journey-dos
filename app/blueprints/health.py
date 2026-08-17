"""Platform endpoints. These deliberately bypass tenant resolution.

`/healthz` must answer even when no church exists and the database is down,
because Render uses it to decide whether the service is alive. `/readyz`
touches the database on purpose, so a failing database shows up as not ready
rather than as a healthy service serving errors.
"""

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    return jsonify(status="ok")


@bp.get("/readyz")
def readyz():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return jsonify(status="degraded", database=str(exc)[:200]), 503
    return jsonify(status="ok", database="ok")
