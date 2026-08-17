"""Session security and authorization.

The one bug this module exists to prevent
--------------------------------------------------------------------
Flask-Login hands the user loader whatever string `get_id()` put in the
cookie, and nothing else. In a single-tenant app that is a user id and the
loader does a primary key lookup. In a multi-tenant app that same pattern is a
cross-tenant session replay: a cookie minted while signed in to one church's
host is presented on another church's host, the loader looks up the id, finds
a valid user, and that user is now authenticated inside a church they have no
account at.

Two defenses, both required:

1. `User.get_id()` returns `church_id:user_id`, so a mismatch is detectable.
2. `load_user` compares that church id to the church the host resolved to, and
   returns None on any disagreement.

Cookie scope is the third leg. `SESSION_COOKIE_DOMAIN` is never set, so the
browser scopes the cookie to the exact host that issued it and a cookie for
one subdomain is never sent to another. `assert_cookie_scope_is_safe` fails
the boot if that is ever configured away.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, current_app, flash, g, redirect, request, url_for
from flask_login import LoginManager, current_user

from app.content import AUTH
from app.extensions import db

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.session_protection = "strong"


def load_user(composite_id: str):
    """Resolve a session cookie to a user, or refuse.

    Returns None rather than raising. Flask-Login treats None as anonymous,
    which is the correct outcome: the request continues as a signed-out
    visitor instead of erroring, and the login view handles it.
    """
    from app.models import User

    church = getattr(g, "church", None)
    if church is None:
        return None

    try:
        church_id_str, user_id_str = str(composite_id).split(":", 1)
        church_id, user_id = int(church_id_str), int(user_id_str)
    except (ValueError, AttributeError):
        # An old-format or tampered cookie. Sign them out rather than guess.
        return None

    if church_id != church.id:
        current_app.logger.warning(
            "Rejected a session for church %s presented on church %s",
            church_id,
            church.id,
        )
        return None

    user = db.session.get(User, user_id)
    if user is None or user.church_id != church.id:
        return None
    if not user.is_active:
        return None
    return user


def assert_cookie_scope_is_safe(app) -> None:
    """Refuse to boot with a session cookie shared across tenant subdomains."""
    domain = app.config.get("SESSION_COOKIE_DOMAIN")
    if domain:
        raise RuntimeError(
            f"SESSION_COOKIE_DOMAIN is set to {domain!r}. That shares one "
            f"session cookie across every tenant subdomain, which is a "
            f"cross-tenant session leak. Leave it unset so the browser scopes "
            f"the cookie to the exact host that issued it."
        )


def register_security(app) -> None:
    login_manager.init_app(app)
    login_manager.user_loader(load_user)
    login_manager.login_message = AUTH["login_required"]
    login_manager.login_message_category = "notice"
    assert_cookie_scope_is_safe(app)


# ---------------------------------------------------------------------------
# Authorization decorators
# ---------------------------------------------------------------------------

def role_required(*roles: str):
    """Allow only the named roles. Anonymous users go to the login page."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if not current_user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def min_role(role: str):
    """Allow the named role and anything above it in the hierarchy."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if not current_user.at_least(role):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def safe_next_url(candidate: str | None, fallback_endpoint: str) -> str:
    """Only ever redirect inside this site.

    An unvalidated `?next=` is an open redirect: a link that looks like the
    church's own login page can drop someone on an attacker's page after they
    authenticate. Anything with a scheme or a host is discarded.
    """
    fallback = url_for(fallback_endpoint)
    if not candidate:
        return fallback
    if candidate.startswith("//") or "://" in candidate:
        return fallback
    if not candidate.startswith("/"):
        return fallback
    return candidate
