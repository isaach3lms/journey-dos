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
from flask_login import LoginManager, current_user, login_user

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
        church_id_str, user_id_str, version_str = str(composite_id).split(":", 2)
        church_id, user_id, version = (
            int(church_id_str),
            int(user_id_str),
            int(version_str),
        )
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
    if user.session_version != version:
        # The password changed after this cookie was issued. Everything minted
        # under the old one stops working, on every device.
        return None
    return user


def sign_in(user, remember: bool = True) -> None:
    """Sign somebody in so that they stay signed in. The one way to do it.

    There were four places that signed a person in and only one of them did
    this, which is why members had to log in every time they opened the app.
    The other three, confirming an email address after signing up, finishing
    a password reset, and replacing a temporary password, called
    `login_user(user)` bare. A new member's first act in the app was therefore
    to be signed out of it.

    **`session.permanent` is the load-bearing line, and it fixes two separate
    logouts at once.**

    The first is obvious once seen. Without it Flask issues a session cookie
    with no expiry, which a browser is entitled to drop the moment the browser
    session ends, and iOS does exactly that when an app is closed. The config
    has said `PERMANENT_SESSION_LIFETIME = 14 days` the whole time; nothing
    was asking for it.

    The second is not obvious at all. `session_protection = "strong"`, set
    just above, hashes the client's IP address and user agent into the session
    and wipes the session **and the remember cookie** when that hash changes.
    A phone changes IP every time it moves between wifi and the mobile
    network, so strong protection was signing people out for walking out of
    the building. Flask-Login's own code takes a different branch for a
    permanent session: it marks the session stale instead of destroying it,
    which keeps the person signed in. Nothing here requires a fresh session,
    so that branch costs nothing.

    So strong protection stays on, and is simply no longer destructive. That
    is better than turning it down, which is the usual advice and gives up the
    protection for everybody rather than only where it misfires.

    `remember` is the person's own choice on a shared computer, and defaults
    to staying signed in. The remember cookie is the belt to the session's
    braces: it outlives the session cookie and silently restores the session
    when somebody opens the app after a fortnight away.

    **A long cookie is safe here because `load_user` above re-checks it on
    every single request:** the account still exists, still belongs to this
    church, is still active, and the password has not changed since the cookie
    was minted. Deactivating somebody in Settings signs their phone out on its
    next tap, whatever the cookie says. That invalidation path is what a long
    session needs to be defensible, and it was already built.
    """
    from flask import session

    session.permanent = True

    # A kiosk account gets a year whether or not anybody ticked anything. A
    # tablet that logs out is a tablet somebody signs into with their own
    # account to get past the login screen, which is the problem the kiosk
    # exists to remove. What makes it safe is that the account can only reach
    # the check-in screens; see app/kiosk.py.
    if getattr(user, "is_kiosk", False):
        from app.kiosk import KIOSK_SESSION

        login_user(user, remember=True, duration=KIOSK_SESSION)
        return

    login_user(user, remember=remember)


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
