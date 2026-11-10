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
from datetime import timedelta
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

    # Session hardening. SESSION_COOKIE_DOMAIN is deliberately absent: setting
    # it would share one cookie across every tenant subdomain, which is a
    # cross-tenant session leak. app/security.py fails the boot if it appears.
    # None means "ask hashlib what this interpreter can do". See
    # User._strongest_available_hash. Only TestingConfig overrides it.
    PASSWORD_HASH_METHOD = None

    # Email. Resend over HTTPS on port 443, never SMTP: port 587 is blocked
    # outbound on Render and most managed hosts, and finding that out at deploy
    # time after building against SMTP is a rewrite, not a config change.
    MAIL_TRANSPORT = os.environ.get("MAIL_TRANSPORT", "console")
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "The Journey Church <no-reply@example.com>")
    # A hair above the 15 MB per-file cap in app/files.py, so an oversized
    # upload is refused with our message rather than Werkzeug's.
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    MAIL_TIMEOUT = 15
    OUTBOX_BATCH_SIZE = 50
    # Account email (confirm, reset, set up) goes out inside the request that
    # queued it, rather than waiting up to five minutes for the worker.
    MAIL_SEND_NOW = True

    # Push. VAPID identifies this application to the push services, so one key
    # pair covers every church. It lives here rather than in a column because
    # rotating it invalidates every subscription everywhere, which should be a
    # deploy rather than a form somebody can submit.
    PUSH_TRANSPORT = os.environ.get("PUSH_TRANSPORT", "null")
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_SUBJECT = os.environ.get(
        "VAPID_SUBJECT", "mailto:isaac@betweensundaysconsulting.com"
    )

    # OneSignal, which is what actually reaches an iPhone. Web Push above
    # cannot: inside the wrapper the app ships as, Apple exposes no push.
    #
    # The App ID is compiled into every copy of the app and is public by
    # construction, so it is not a secret and is not treated as one. The REST
    # key can send to the whole church and is read from the environment only.
    ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "")
    ONESIGNAL_API_KEY = os.environ.get("ONESIGNAL_API_KEY", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    # How long somebody stays signed in.
    #
    # These two numbers sat here for months doing nothing, because the login
    # route never marked a session permanent and the remember box defaulted
    # to off. Every member therefore got a cookie with no expiry at all,
    # which iOS discards when the app closes. See `sign_in` in
    # app/security.py for the fix and for why it also cured a second logout
    # nobody had connected to this one.
    #
    # Fourteen days on the session, and Flask slides it forward on every
    # request, so somebody who opens the app most weeks is never asked again.
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    # A year, raised from thirty days. Thirty is long enough for a weekly
    # attender and too short for the person who opens the app when there is
    # something on, which is most of a congregation: they came back after six
    # weeks and met a login screen, which is the whole complaint.
    #
    # The length is not what makes a session safe here. `load_user` re-checks
    # the account on every single request, so a deactivated account or a
    # changed password signs that device out on its next tap no matter how
    # long the cookie had left. A year of cookie is a year of *convenience*,
    # not a year of unrevokable access.
    REMEMBER_COOKIE_DURATION = timedelta(days=365)

    # **REMEMBER_COOKIE_REFRESH_EACH_REQUEST is deliberately off.**
    #
    # It looks like exactly what this wants: it would slide the year forward
    # on every visit instead of measuring it from the day somebody signed in.
    # What it actually does is set `session["_remember"] = "set"` on every
    # response for every signed-in user, and then write the cookie. It never
    # consults whether that person asked to be remembered, so switching it on
    # quietly issues a year-long cookie to the staff member who unticked the
    # box on a shared computer. Read `_update_remember_cookie` in
    # flask_login/login_manager.py; the choice is simply not in that code
    # path. A test caught this, which is the only reason it is written down
    # rather than shipped.
    #
    # Nothing is really lost. The session cookie slides on every request
    # (SESSION_REFRESH_EACH_REQUEST, on by default), so anybody who opens the
    # app within a fortnight is carried by that and never reaches the
    # remember cookie at all. The remember cookie is the fallback for a
    # longer gap than that, and a fixed year of it is long enough for the
    # person who turns up at Christmas and Easter.
    WTF_CSRF_TIME_LIMIT = None

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
    # Only ever lowered here. See User._hash_method.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1"
    MAIL_TRANSPORT = "memory"
    # Off so tests can see what was queued. Tests of immediate delivery turn
    # it on themselves.
    MAIL_SEND_NOW = False
    # A value, because the page that starts notifications is rendered only
    # when one is configured. Tests of the unconfigured case clear it.
    ONESIGNAL_APP_ID = "test-onesignal-app"
    PUSH_TRANSPORT = "memory"


class ProductionConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("DATABASE_URL"))
    # Cookies never leave TLS in production.
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def init_app(cls, app):
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise MissingDatabaseURL(
                "DATABASE_URL is not set. Refusing to boot in production. "
                "Falling back to SQLite here would put every church's data on "
                "an ephemeral disk that is wiped on the next deploy."
            )
        if app.config.get("PUSH_TRANSPORT") == "webpush" and not app.config.get(
            "VAPID_PRIVATE_KEY"
        ):
            raise RuntimeError(
                "PUSH_TRANSPORT is 'webpush' but VAPID_PRIVATE_KEY is empty. "
                "A church whose people tapped Turn on notifications and never "
                "receive one is worse off than a deploy that refused to start."
            )
        if app.config.get("PUSH_TRANSPORT") == "onesignal" and not (
            app.config.get("ONESIGNAL_APP_ID")
            and app.config.get("ONESIGNAL_API_KEY")
        ):
            raise RuntimeError(
                "PUSH_TRANSPORT is 'onesignal' but ONESIGNAL_APP_ID or "
                "ONESIGNAL_API_KEY is empty. Same reasoning as above: failing "
                "at boot is louder than failing once per notification."
            )
        if app.config.get("MAIL_TRANSPORT") == "resend" and not app.config.get(
            "RESEND_API_KEY"
        ):
            raise RuntimeError(
                "MAIL_TRANSPORT is 'resend' but RESEND_API_KEY is empty. A "
                "church that believes it sent a welcome email and did not is "
                "worse off than one whose deploy refused to start."
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
