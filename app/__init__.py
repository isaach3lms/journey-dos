"""Application factory.

Boot order matters and is asserted by the tests:

1. Config loads and normalizes DATABASE_URL, hard-failing in production.
2. Extensions bind.
3. Tenancy attaches `g.church` before any view runs.
4. Brand tokens are injected from that church, and only from that church.
5. Error handlers register last so they wrap everything above them.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g

from app.brand import brand_css_vars, palette_for
from app.config import resolve_config
from app.content import ICONS, NAV_GROUPS, NAV_ITEMS
from app.errors import register_error_handlers
from app.extensions import db, migrate
from app.tenancy import register_tenancy

load_dotenv()


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    config_class = resolve_config(config_name)
    app.config.from_object(config_class)
    config_class.init_app(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    # render_as_batch keeps SQLite ALTERs working locally.
    # render_migration_item keeps application imports out of migration files.
    from app.models.base import render_migration_item
    migrate.init_app(
        app, db, render_as_batch=True, render_item=render_migration_item
    )

    # Imported for their side effect of registering with the metadata, so
    # Alembic autogenerate sees every table.
    from app import models  # noqa: F401

    from app.blueprints.health import bp as health_bp
    from app.blueprints.shell import bp as shell_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(shell_bp)

    register_tenancy(app)
    register_error_handlers(app)

    from app.cli import register_cli
    register_cli(app)

    @app.context_processor
    def inject_brand():
        """Hand every template its tenant's tokens.

        Wrapped defensively: a template render that fails because branding
        could not be resolved would take down the error page too.
        """
        church = getattr(g, "church", None)
        try:
            palette = palette_for(church)
            css = brand_css_vars(church)
        except Exception:  # noqa: BLE001
            app.logger.exception("Brand resolution failed, using platform default")
            palette = palette_for(None)
            css = brand_css_vars(None)
        return {
            "palette": palette,
            "brand_css": css,
            "nav_items": NAV_ITEMS,
            "nav_groups": NAV_GROUPS,
            "icons": ICONS,
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # The product is not indexed while it is being built.
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @app.shell_context_processor
    def shell_context():
        from app.models import Church
        return {"db": db, "Church": Church}

    return app


__all__ = ["create_app", "db"]
