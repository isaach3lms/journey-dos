"""Platform endpoints. These deliberately bypass tenant resolution.

`/healthz` must answer even when no church exists and the database is down,
because Render uses it to decide whether the service is alive. `/readyz`
touches the database on purpose, so a failing database shows up as not ready
rather than as a healthy service serving errors.
"""

from pathlib import Path

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    return jsonify(status="ok")


def migration_state() -> dict:
    """Which migration the database is on, and which the code expects.

    Reported rather than enforced. A service that refuses to start on a
    pending migration is a service nobody can get into to run the migration,
    which is the wrong failure when the person holding the shell is the one
    who needs it.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from flask import current_app

    try:
        config = Config(str(Path(current_app.root_path).parent / "migrations" / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(current_app.root_path).parent / "migrations")
        )
        head = ScriptDirectory.from_config(config).get_current_head()
    except Exception as exc:  # noqa: BLE001
        return {"expected": None, "error": str(exc)[:200]}

    try:
        current = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    except Exception:  # noqa: BLE001
        # No table at all means the schema was built directly rather than
        # migrated, which is what the test harness and `flask init-db` do.
        # That is not "behind", it is "unknown", and reporting it as behind
        # would cry wolf often enough that nobody would trust the real signal.
        return {"expected": head, "applied": None, "pending": None}

    return {
        "expected": head,
        "applied": current,
        "pending": current != head,
    }


@bp.get("/readyz")
def readyz():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return jsonify(status="degraded", database=str(exc)[:200]), 503

    migrations = migration_state()
    if migrations.get("pending"):
        # Serving on a schema the code does not expect is how a half-deployed
        # state stays invisible until somebody hits the one screen that needs
        # the missing column.
        return jsonify(
            status="degraded",
            database="ok",
            migrations=migrations,
            fix="Run `flask db upgrade` in the shell.",
        ), 503

    return jsonify(status="ok", database="ok", migrations=migrations)
