"""Staying signed in.

Members had to sign in every time they opened the app. Two separate causes,
and one line fixed both, which is the thing worth remembering about it.

**The session was never permanent.** Without that, Flask issues a cookie with
no expiry, and a browser may drop it when the browser session ends. iOS does,
when an app closes. The config had promised a fourteen day session the whole
time and nothing ever asked for it.

**Strong session protection was destroying the session on a network change.**
`session_protection = "strong"` hashes the client IP and user agent into the
session and, on a mismatch, wipes the session *and* the remember cookie. A
phone changes IP walking from wifi to the mobile network. Flask-Login takes a
different, non-destructive branch when the session is permanent, so the same
line that fixed the first cause disarmed the second.

**And three of the four places that sign somebody in never did it at all**,
including the one a new member hits immediately after confirming their email.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from app.models import Church, User
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}
APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def member(db, journey):
    return db.session.scalar(
        db.select(User).where(
            User.church_id == journey.id,
            User.email == "member@journeychurchsemo.com",
        )
    )


def log_in(client, email="member@journeychurchsemo.com", remember=None):
    data = {"email": email, "password": PASSWORD}
    if remember:
        data["remember"] = "y"
    return client.post("/auth/login", data=data, headers=H, follow_redirects=True)


def cookie(client, name):
    """Werkzeug's own accessor, scoped to the host these requests use."""
    return client.get_cookie(name, domain=JOURNEY_HOST)


class TestTheSessionOutlivesTheApp:
    def test_signing_in_makes_the_session_permanent(self, app, db, journey, client):
        """The whole bug, in one assertion.

        A session that is not permanent has no expiry on its cookie, and iOS
        throws those away when the app closes.
        """
        with client:
            log_in(client)
            from flask import session

            assert session.permanent is True

    def test_the_session_cookie_has_an_expiry(self, app, db, journey, client):
        """The browser-visible half of the same thing.

        A cookie with no expiry is a session cookie, which a browser may drop
        whenever the browser session ends. iOS does that when an app closes,
        and that is the bug. An expiry in the future is the fix, visible from
        outside the app.
        """
        log_in(client)
        jar = cookie(client, "session")
        assert jar is not None, "no session cookie was set"
        assert jar.expires is not None, (
            "the session cookie has no expiry, so the browser is free to "
            "throw it away when the app closes"
        )

    def test_the_box_as_rendered_buys_a_long_cookie(
        self, app, db, journey, client
    ):
        """What the ticked box actually gets you.

        Posted the way a browser posts a ticked checkbox, rather than through
        the bare helper: a checkbox that is not ticked sends nothing at all,
        so a helper that omits the field is testing the opt-out, not the
        default. The default itself is asserted against the rendered page
        below.
        """
        log_in(client, remember=True)
        token = cookie(client, "remember_token")
        assert token is not None, "no remember cookie for somebody who asked"

        # Roughly a year out, not roughly a month. The number is the point:
        # thirty days was the old value and it was too short for somebody who
        # opens the app when there is something on.
        from datetime import datetime, timedelta, timezone

        assert token.expires is not None
        expires = token.expires
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        left = expires - datetime.now(timezone.utc)
        assert left > timedelta(days=300), f"only {left.days} days"

    def test_a_shared_computer_can_still_opt_out(self, app, db, journey, client):
        """The one case the box is genuinely for. Unticking it has to still
        mean something or the control is a lie."""
        client.post("/auth/login",
                    data={"email": "member@journeychurchsemo.com",
                          "password": PASSWORD},
                    headers=H, follow_redirects=True)
        # No `remember` key in the form at all, which is what an unticked
        # checkbox posts.
        assert cookie(client, "remember_token") is None

    def test_the_box_is_ticked_on_the_login_page(self, app, db, journey, client):
        page = client.get("/auth/login", headers=H).get_data(as_text=True)
        box = [line for line in page.splitlines() if 'name="remember"' in line]
        assert box, "the remember box is gone from the page"
        assert "checked" in box[0], box[0]


class TestAChangeOfNetworkDoesNotSignYouOut:
    """The second cause, and the one nobody would have guessed.

    Strong session protection hashes the client IP into the session. A phone
    walking out of the building changes IP, the hash stops matching, and
    Flask-Login's strong branch wipes the session and clears the remember
    cookie. A permanent session takes the other branch, which marks the
    session stale and leaves the person signed in.
    """

    def test_still_signed_in_from_a_different_address(self, app, client):
        """No `db` fixture, deliberately, for the reason conftest gives in
        that fixture's own docstring: it holds one application context open
        for the whole test, so Flask-Login finds its cached user in the
        shared `g` and never re-runs the check this test is about. With it,
        this passed even with the fix removed.
        """
        log_in(client)
        first = client.get("/me/", headers=H, environ_overrides={
            "REMOTE_ADDR": "203.0.113.10"})
        assert first.status_code == 200

        # Same device, different network.
        moved = client.get("/me/", headers=H, environ_overrides={
            "REMOTE_ADDR": "198.51.100.77"})
        assert moved.status_code == 200, (
            "changing network signed the member out"
        )

    def test_nothing_in_the_app_demands_a_fresh_session(self):
        """Which is why the non-destructive branch costs nothing.

        That branch marks the session stale rather than destroying it. If
        anything ever required freshness, a member who changed network would
        be bounced to a login screen on that route, and this permissive
        session would stop being free.
        """
        offenders = []
        for path in APP.rglob("*.py"):
            text = path.read_text()
            if "fresh_login_required" in text or "needs_refresh" in text:
                offenders.append(path.relative_to(APP.parent).as_posix())
        assert not offenders, (
            f"{offenders} require a fresh session. A permanent session goes "
            "stale on a network change, so those routes will bounce members "
            "to a login screen. Either drop the freshness requirement or "
            "revisit the session protection trade in app/security.py."
        )


class TestEveryWayInIsDurable:
    """Four places signed somebody in. One of them did this correctly."""

    def test_confirming_an_email_signs_you_in_durably(
        self, app, db, journey, client
    ):
        """A new member's first moment inside the app. A bare login_user here
        meant their first act was to close the app and be signed out."""
        import inspect

        from app.blueprints import auth

        source = inspect.getsource(auth.verify)
        assert "sign_in(" in source
        assert "login_user(" not in source

    def test_finishing_a_password_reset_signs_you_in_durably(self):
        import inspect

        from app.blueprints import auth

        source = inspect.getsource(auth.reset)
        assert "sign_in(" in source
        assert "login_user(" not in source

    def test_the_login_route_signs_you_in_durably(self):
        import inspect

        from app.blueprints import auth

        source = inspect.getsource(auth.login)
        assert "sign_in(" in source
        assert "login_user(" not in source


class TestNoOtherPlaceCanForget:
    """The guard, in the shape this codebase already uses for notifications.

    Four call sites and three of them wrong is not something review catches,
    because each one looks right on its own. `login_user` leaves somebody
    signed in, the page after it works, and the defect only shows up when the
    person comes back tomorrow.
    """

    ALLOWED = {
        "app/security.py": "This is `sign_in`. It is the one that calls it.",
    }

    def test_nothing_calls_login_user_directly(self):
        offenders = []
        for path in sorted(APP.rglob("*.py")):
            rel = path.relative_to(APP.parent).as_posix()
            if rel in self.ALLOWED:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "login_user"):
                    offenders.append(rel)
                    break
        assert not offenders, (
            f"These call login_user directly: {offenders}. That skips "
            "`session.permanent`, so the person is signed out when they next "
            "close the app and when their phone changes network. Use "
            "app.security.sign_in."
        )

    def test_the_exemption_is_real(self):
        """A stale exemption is permission to forget, granted by a reason
        about code somebody deleted."""
        for rel in self.ALLOWED:
            tree = ast.parse((APP.parent / rel).read_text())
            calls = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "login_user"
                for n in ast.walk(tree)
            )
            assert calls, f"{rel} no longer calls login_user. Remove it."

    def test_the_guard_catches_a_bare_call(self, tmp_path):
        """The check above passes on a clean tree, which proves nothing on
        its own."""
        bad = tmp_path / "sneaky.py"
        bad.write_text("def view(u):\n    login_user(u)\n")
        tree = ast.parse(bad.read_text())
        assert any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "login_user"
            for n in ast.walk(tree)
        )


class TestTheLongCookieIsStillRevokable:
    """What makes a year defensible. None of this is new; it is the reason
    the year is safe, so it is asserted here next to the year."""

    # These three deliberately skip the `db` fixture. It holds one
    # application context open for the whole test, so every request the
    # client makes reuses the same `g`, and Flask-Login caches the signed-in
    # user there. Under that fixture a revoked session keeps answering 200
    # from cache and these tests pass or fail for reasons that have nothing
    # to do with revocation. conftest says so in the fixture's own docstring.

    def _change(self, app, fn):
        """Make a change to the member, in a context of its own."""
        with app.app_context():
            from app.extensions import db as orm
            from app.models import Church, User

            church = orm.session.scalar(
                orm.select(Church).where(Church.slug == "journey"))
            user = orm.session.scalar(
                orm.select(User).where(
                    User.church_id == church.id,
                    User.email == "member@journeychurchsemo.com"))
            fn(user)
            orm.session.commit()

    def test_deactivating_an_account_signs_the_device_out(self, app, client):
        log_in(client)
        assert client.get("/me/", headers=H).status_code == 200

        def deactivate(user):
            user.is_active_account = False

        self._change(app, deactivate)

        after = client.get("/me/", headers=H, follow_redirects=False)
        assert after.status_code in (302, 401), (
            "a deactivated account kept its session"
        )

    def test_changing_the_password_signs_other_devices_out(self, app, client):
        """The thing that makes a year-long cookie defensible."""
        log_in(client)
        assert client.get("/me/", headers=H).status_code == 200

        def rotate(user):
            user.set_password("a-completely-different-password")

        self._change(app, rotate)

        after = client.get("/me/", headers=H, follow_redirects=False)
        assert after.status_code in (302, 401), (
            "the old cookie outlived the password it was minted under"
        )

    def test_a_cookie_from_another_church_is_refused(self, app, client):
        """Unchanged by any of this, and the reason the cookie carries the
        church id at all. A longer cookie would be a longer window for a
        cross-tenant replay if this ever stopped holding."""
        from tests.conftest import RIVERBEND_HOST

        log_in(client)
        other = client.get("/me/", headers={"Host": RIVERBEND_HOST},
                           follow_redirects=False)
        assert other.status_code in (302, 401, 404)


class TestTheNumbers:
    def test_the_session_lasts_a_fortnight(self, app):
        assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=14)

    def test_the_remember_cookie_lasts_a_year(self, app):
        """Thirty days was long enough for a weekly attender and too short
        for somebody who opens the app when there is something on."""
        assert app.config["REMEMBER_COOKIE_DURATION"] == timedelta(days=365)

    def test_the_session_window_slides_forward(self, app):
        """Measured from the last visit rather than from the sign-in, so
        anybody who opens the app most weeks is never asked again."""
        assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True

    def test_the_remember_cookie_does_not_slide(self, app):
        """Asserted as a decision, not an oversight.

        REMEMBER_COOKIE_REFRESH_EACH_REQUEST would slide it, and it also
        writes a remember cookie for every signed-in user on every response
        without ever checking whether they asked to be remembered. Turning it
        on silently overrides the opt-out on a shared computer. The test
        above for that opt-out is what caught it.
        """
        assert not app.config.get("REMEMBER_COOKIE_REFRESH_EACH_REQUEST")

    def test_the_cookies_are_still_locked_down(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_production_still_requires_https(self):
        from app.config import ProductionConfig

        assert ProductionConfig.SESSION_COOKIE_SECURE is True
        assert ProductionConfig.REMEMBER_COOKIE_SECURE is True
