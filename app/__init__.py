"""Application factory."""

from __future__ import annotations

import logging
import os

from flask import Flask, g

from app.brand import css_variables, tokens_for
from app.config import resolve_config
from app.extensions import csrf, db, login_manager, migrate
from app.errors import register_error_handlers

__version__ = "0.1.0"


def create_app(config_name: str | None = None, **overrides) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    config = resolve_config(config_name)
    app.config.from_object(config)
    app.config.update(overrides)

    _configure_logging(app)
    _ensure_instance_path(app)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def _load_user(user_id):  # noqa: ANN001
        """Placeholder until increment 1 introduces app_user.

        Flask-Login raises if no loader is registered, and the template context
        processor calls it on every render, so the stub ships now.
        """
        return None

    # Models are imported for their side effect: mapper registration.
    from app import models  # noqa: F401

    _register_blueprints(app)
    register_error_handlers(app)
    _register_request_hooks(app)
    _register_cli(app)

    app.logger.info(
        "DOS %s booted in %s against %s",
        __version__,
        app.config["APP_ENV"],
        _redact(app.config.get("SQLALCHEMY_DATABASE_URI")),
    )
    return app


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.health import bp as health_bp
    from app.blueprints.public import bp as public_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(public_bp)


def _register_request_hooks(app: Flask) -> None:
    from app.tenancy import load_current_church

    app.before_request(load_current_church)

    @app.context_processor
    def inject_brand():
        """Brand tokens for every template that extends base.html.

        This reads an already-loaded object off ``g``. It issues no query, so a
        template render cannot be the thing that touches the database first.
        """
        church = getattr(g, "church", None)
        return {"church": church, "brand_css": css_variables(tokens_for(church))}


def _register_cli(app: Flask) -> None:
    from app.cli import register_cli

    register_cli(app)


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if not app.config.get("IS_PRODUCTION") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.logger.setLevel(level)


def _ensure_instance_path(app: Flask) -> None:
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        os.makedirs(os.path.join(os.getcwd(), "instance"), exist_ok=True)
    except OSError:  # pragma: no cover - read-only filesystem on Render
        pass


def _redact(url: str | None) -> str:
    """Never log a password. This line ends up in Render's log stream."""
    if not url:
        return "unset"
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    head = url[: scheme_sep + 3] if scheme_sep != -1 else ""
    tail = url.rsplit("@", 1)[-1]
    return f"{head}***@{tail}"
