"""Configuration.

Two architecture rules live in this file and both are enforced with tests:

1. Normalize ``postgres://`` to ``postgresql+psycopg2://`` at boot.
   SQLAlchemy 2.x rejects the bare ``postgres://`` scheme that Render hands out.
2. Hard fail at boot when DATABASE_URL is missing in production.
   A production process that silently falls back to SQLite is worse than
   a process that refuses to start.
"""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """Raised at boot when the environment cannot support the app."""


def normalize_database_url(url: str | None) -> str | None:
    """Return a URL SQLAlchemy 2.x will accept.

    Render provisions ``postgres://``. SQLAlchemy 2.x removed that alias.
    Everything else passes through untouched.
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Base configuration, resolved from the environment at boot."""

    APP_ENV = "development"
    IS_PRODUCTION = False

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")

    # Host that tenant subdomains hang off when a church has no custom domain.
    # Journey uses app.thejourneychurchsemo.com, so its custom domain wins,
    # but every tenant also keeps a platform host for DNS cutover and support.
    PLATFORM_DOMAIN = os.environ.get("PLATFORM_DOMAIN", "localhost")

    # Local development convenience only. Ignored in production.
    DEFAULT_CHURCH_SLUG = os.environ.get("DEFAULT_CHURCH_SLUG")

    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = "http"

    @classmethod
    def init(cls) -> "type[Config]":
        return cls


class DevelopmentConfig(Config):
    APP_ENV = "development"
    IS_PRODUCTION = False

    @classmethod
    def init(cls):
        url = normalize_database_url(os.environ.get("DATABASE_URL"))
        cls.SQLALCHEMY_DATABASE_URI = url or "sqlite:///" + os.path.join(
            os.getcwd(), "instance", "dos-dev.sqlite"
        )
        return cls


class TestingConfig(Config):
    APP_ENV = "testing"
    IS_PRODUCTION = False
    TESTING = True
    WTF_CSRF_ENABLED = False

    @classmethod
    def init(cls):
        cls.SQLALCHEMY_DATABASE_URI = normalize_database_url(
            os.environ.get("TEST_DATABASE_URL")
        ) or "sqlite:///:memory:"
        return cls


class ProductionConfig(Config):
    APP_ENV = "production"
    IS_PRODUCTION = True
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def init(cls):
        raw = os.environ.get("DATABASE_URL")
        if not raw:
            raise ConfigError(
                "DATABASE_URL is not set. Refusing to start in production. "
                "Check that render.yaml provisioned the database and that the "
                "service was created from the Blueprint, not by hand."
            )
        secret = os.environ.get("SECRET_KEY")
        if not secret or secret == "dev-only-not-for-production":
            raise ConfigError(
                "SECRET_KEY is not set. Refusing to start in production."
            )
        if os.environ.get("PLATFORM_DOMAIN") in (None, "", "localhost"):
            raise ConfigError(
                "PLATFORM_DOMAIN is not set. Refusing to start in production. "
                "Tenant resolution cannot fall back without it."
            )
        cls.SQLALCHEMY_DATABASE_URI = normalize_database_url(raw)
        cls.SECRET_KEY = secret
        cls.PLATFORM_DOMAIN = os.environ["PLATFORM_DOMAIN"]
        return cls


_CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def resolve_config(name: str | None = None):
    """Pick and initialize the config class for this process."""
    key = (name or os.environ.get("APP_ENV") or "development").strip().lower()
    if key not in _CONFIGS:
        raise ConfigError(
            f"Unknown APP_ENV {key!r}. Expected one of: {', '.join(sorted(_CONFIGS))}."
        )
    return _CONFIGS[key].init()
