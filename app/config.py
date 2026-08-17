"""Configuration for the Discipleship Operating System.

Two rules live here and nowhere else:

1. `postgres://` is normalized to `postgresql+psycopg2://`. Render hands out
   the former, SQLAlchemy 2.x refuses it. Normalizing at boot means no other
   module ever has to think about it.
2. Production hard-fails if DATABASE_URL is missing. A web service that
   silently falls back to SQLite on an ephemeral disk loses a church's data
   on the next deploy and gives no warning that it happened.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class MissingDatabaseURL(RuntimeError):
    """Raised at boot when production has no database configured."""


def normalize_database_url(url: str | None) -> str | None:
    """Coerce a provider URL into a driver SQLAlchemy 2.x accepts.

    Render, Heroku, and Fly all still emit `postgres://`. SQLAlchemy dropped
    that alias. Handles the bare scheme and the `postgresql://` form that has
    no driver pinned.
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Tenancy. See app/tenancy.py for how these are used.
    PLATFORM_DOMAIN = os.environ.get("PLATFORM_DOMAIN", "")
    DEFAULT_TENANT_SLUG = os.environ.get("DEFAULT_TENANT_SLUG", "journey")
    ALLOW_TENANT_QUERY_OVERRIDE = False

    # Reserved hosts that are the platform itself, never a tenant.
    RESERVED_SUBDOMAINS = {"www", "app", "api", "admin", "static", "assets"}

    TESTING = False
    DEBUG = False

    @classmethod
    def init_app(cls, app):
        """Hook for per-environment boot checks."""


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    ALLOW_TENANT_QUERY_OVERRIDE = True
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.environ.get("DATABASE_URL")
    ) or f"sqlite:///{BASE_DIR / 'instance' / 'dos-dev.sqlite'}"


class TestingConfig(BaseConfig):
    TESTING = True
    ALLOW_TENANT_QUERY_OVERRIDE = True
    PLATFORM_DOMAIN = "dos.test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProductionConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("DATABASE_URL"))

    @classmethod
    def init_app(cls, app):
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise MissingDatabaseURL(
                "DATABASE_URL is not set. Refusing to boot in production. "
                "Falling back to SQLite here would put every church's data on "
                "an ephemeral disk that is wiped on the next deploy."
            )
        if app.config["SECRET_KEY"] == "dev-only-not-for-production":
            raise RuntimeError(
                "SECRET_KEY is still the development default. Set a real one "
                "before serving a single session."
            )


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def resolve_config(name: str | None = None):
    key = (name or os.environ.get("FLASK_ENV") or "development").strip().lower()
    return CONFIGS.get(key, DevelopmentConfig)
