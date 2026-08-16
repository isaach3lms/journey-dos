"""Error handling.

The rule this file exists to enforce: the 500 page must not touch the database.

If ``base.html`` ever grows a query, a navigation count, or a tenant lookup,
then a database fault makes the 500 page itself unrenderable and the user gets
a bare Werkzeug traceback page. The templates in ``templates/errors/`` extend
nothing, inherit nothing, and read only ``app.brand``, which is a plain Python
module with no imports from the app.
"""

from __future__ import annotations

import logging

from flask import render_template
from sqlalchemy.exc import SQLAlchemyError

from app.brand import DEFAULT_TOKENS, css_variables
from app.extensions import db

log = logging.getLogger(__name__)

#: Rendered once at import. No database, no request context, no tenant.
SAFE_CSS = css_variables(DEFAULT_TOKENS)


def register_error_handlers(app) -> None:
    @app.errorhandler(404)
    def not_found(error):  # noqa: ANN001
        return render_template("errors/404.html", safe_css=SAFE_CSS), 404

    @app.errorhandler(403)
    def forbidden(error):  # noqa: ANN001
        return render_template("errors/403.html", safe_css=SAFE_CSS), 403

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):  # noqa: ANN001
        log.exception("Database error", exc_info=error)
        _rollback_quietly()
        return render_template("errors/500.html", safe_css=SAFE_CSS), 500

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(error):  # noqa: ANN001
        log.exception("Unhandled error", exc_info=error)
        _rollback_quietly()
        return render_template("errors/500.html", safe_css=SAFE_CSS), 500


def _rollback_quietly() -> None:
    """A failed session must not poison the next request on this worker."""
    try:
        db.session.rollback()
    except Exception:  # pragma: no cover - the database is already unhappy
        log.warning("Rollback failed during error handling", exc_info=True)
