"""Error handlers.

The 500 page is a static file served straight off disk. It does not extend
`base.html`, does not render Jinja, does not run a context processor, and does
not open a database session. That is deliberate. The most common cause of a
500 is the database being unreachable, and an error page that needs the
database to render its own chrome turns one failure into a blank screen.

The 404 page does render through `base.html`, because a 404 means routing
worked and the tenant is known. If the tenant is not known, the handler falls
back to the same static page as the 500 rather than risking a second failure
inside the error path.
"""

from __future__ import annotations

from pathlib import Path

from flask import current_app, g, render_template
from werkzeug.exceptions import HTTPException

from app.content import AUTH, ERRORS

STATIC_500 = "500.html"


def _static_error_page(filename: str, status: int):
    """Read a static HTML file off disk and return it. No Jinja, no session."""
    path = Path(current_app.static_folder) / filename
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        body = (
            "<!doctype html><meta charset=utf-8>"
            f"<title>{ERRORS['500_title']}</title>"
            f"<h1>{ERRORS['500_title']}</h1><p>{ERRORS['500_body']}</p>"
        )
    return body, status, {"Content-Type": "text/html; charset=utf-8"}


def register_error_handlers(app) -> None:

    @app.errorhandler(404)
    def not_found(error):
        # A 404 raised before tenant resolution has no brand to render with.
        if getattr(g, "church", None) is None:
            return _static_error_page(STATIC_500, 404)
        description = getattr(error, "description", None) or ERRORS["404_body"]
        return (
            render_template(
                "errors/404.html",
                church=g.church,
                title=ERRORS["404_title"],
                body=description,
            ),
            404,
        )

    @app.errorhandler(403)
    def forbidden(error):
        if getattr(g, "church", None) is None:
            return _static_error_page(STATIC_500, 403)
        return (
            render_template(
                "errors/403.html",
                church=g.church,
                title=AUTH["forbidden_title"],
                body=AUTH["forbidden_body"],
            ),
            403,
        )

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(error):
        # A 403 or a 405 is a routing answer, not a crash. Hand it back
        # untouched rather than reporting every HTTP error as a 500.
        if isinstance(error, HTTPException) and error.code != 500:
            return error

        # Let Flask's debugger handle it in development.
        if current_app.debug and not current_app.config.get("FORCE_ERROR_PAGES"):
            raise error

        current_app.logger.exception("Unhandled exception")
        return _static_error_page(STATIC_500, 500)
