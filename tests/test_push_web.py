"""Notifications in an ordinary browser, and staying out of the apps.

**What this adds.** Until now notifications existed only inside the two
wrappers. A member reading the site on a laptop, or on an Android phone that
has not installed the app, could not be reached at all. The provider's web SDK
closes that gap, and it matters most right now because the Android app is
waiting on a store review while its members are reachable through Chrome
today.

**The two ways this goes wrong, both silent.**

The first is the service worker. `sw.js` is served from the root on purpose,
because only a worker at the root has a scope covering `/me/`, and it runs the
whole offline app. The provider's SDK registers its own worker at the root by
default, and a second registration at a scope replaces the first. Shipping
their snippet unchanged would unregister the worker that serves offline pages,
and the symptom would be an install quietly losing its offline behaviour,
which nobody would connect to a notifications change.

The second is loading the web SDK inside a wrapper. The native SDK there
already holds a subscription for this person. A web subscription under the
same external id means the provider delivers to both, so one phone shows every
notification twice. That one would be reported as "notifications are buggy"
and would take a long time to trace back here.

Both are prevented by decisions in `push_identity.html` and the route below,
and both are the kind of decision that looks like a detail until it is a
support thread.
"""

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def linked(db, journey):
    """A member account with a roster record, which is what gets an external
    id minted and therefore renders the reporting half of the partial."""
    user = db.session.scalar(db.select(User).where(
        User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=journey.id, first_name="Alicia",
                    last_name="Romero", email=user.email, stage="member",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return user


class TestTheProvidersWorkerRoute:
    def test_it_is_served_and_public(self, client):
        """Fetched by the browser on registration without cookies, often
        before anybody has signed in. A redirect to a login page registers
        as a failure with nothing saying why."""
        page = client.get("/push/onesignal/OneSignalSDKWorker.js", headers=H)

        assert page.status_code == 200

    def test_it_imports_the_providers_worker(self, client):
        page = client.get("/push/onesignal/OneSignalSDKWorker.js", headers=H)
        body = page.get_data(as_text=True)

        assert "importScripts(" in body
        assert "OneSignalSDK.sw.js" in body

    def test_it_is_javascript(self, client):
        """A worker served as text/plain is refused by the browser."""
        page = client.get("/push/onesignal/OneSignalSDKWorker.js", headers=H)

        assert "javascript" in page.headers["Content-Type"]

    def test_it_does_not_widen_its_own_scope(self, client):
        """The one that matters.

        `sw.js` sends `Service-Worker-Allowed: /` so its scope reaches the
        root. If this one did too, it could claim the root as well, and the
        later registration wins. That is precisely the collision moving the
        file to a subdirectory exists to prevent, so sending the header here
        would undo the whole arrangement while still looking correct.
        """
        page = client.get("/push/onesignal/OneSignalSDKWorker.js", headers=H)

        assert "Service-Worker-Allowed" not in page.headers

    def test_it_is_not_cached(self, client):
        """A cached worker keeps running old rules long after a fix ships."""
        page = client.get("/push/onesignal/OneSignalSDKWorker.js", headers=H)

        assert "no-store" in page.headers.get("Cache-Control", "")

    def test_the_apps_worker_still_owns_the_root(self, client):
        """The regression guard for the pair of them. If this header ever
        goes missing, offline stops covering `/me/` and the reason will look
        like anything except a notifications change."""
        page = client.get("/sw.js", headers=H)

        assert page.headers.get("Service-Worker-Allowed") == "/"


class TestItStaysOutOfTheApps:
    def test_the_guard_is_the_bridge_not_the_plugin(self, client, sign_in,
                                                    linked):
        """A wrapper whose plugin failed to register is a wrapper to fix, not
        one to quietly back with a second SDK that half works. Guarding on a
        found plugin rather than on the bridge would do the latter."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "var in_app = !!window.Capacitor;" in page
        assert "var can_web = !in_app" in page

    def test_the_script_is_injected_rather_than_hardcoded_in_markup(
            self, client, sign_in, linked):
        """A plain script tag would load inside the wrappers too, which is
        the duplicate-subscription bug. It has to be a decision made at
        runtime, after the page can see whether a bridge exists."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'document.createElement("script")' in page
        assert '<script src="https://cdn.onesignal.com' not in page


class TestHowTheSdkIsStarted:
    def test_the_worker_path_has_no_leading_slash(self, client, sign_in,
                                                  linked):
        """The SDK takes a path relative to the site root here and an
        absolute scope below. The asymmetry is theirs, and getting it wrong
        fails registration with nothing useful in the console."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert '"push/onesignal/OneSignalSDKWorker.js"' in page

    def test_the_scope_keeps_it_out_of_the_root(self, client, sign_in,
                                                linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'serviceWorkerParam: {scope: "/push/onesignal/"}' in page

    def test_the_app_id_comes_from_config(self, app, client, sign_in, linked):
        """Not written into markup. It is already served from
        `ONESIGNAL_APP_ID`, and a second copy in a template is a second place
        to change and a chance for the two to disagree."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert app.config["ONESIGNAL_APP_ID"] in page

    def test_a_browser_without_push_is_not_offered_anything(
            self, client, sign_in, linked):
        """Checked before anything loads. Offering a member a button that
        cannot work is worse than staying quiet."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "window.isSecureContext" in page
        assert '"serviceWorker" in navigator' in page
        assert '"PushManager" in window' in page

    def test_a_stalled_sdk_does_not_hang_every_screen(self, client, sign_in,
                                                      linked):
        """A script that loads but never runs its callback would leave the
        card waiting on a promise that never settles, so it would simply
        never appear with nothing in the console to explain it."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "window.setTimeout(function () { give(null); }, 10000);" in page


class TestTheServerStillLearnsTheAnswer:
    def test_reporting_is_shared_by_both_paths(self, client, sign_in, linked):
        """`report` is defined above the branch rather than inside the native
        half. Leaving it below would mean a browser could grant permission
        and the server would never hear, so coverage on the staff screen
        would undercount by every member not using the app."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        before_branch = page.split("if (!signal) {")[0]

        assert "api.report = function (known)" in before_branch

    def test_the_web_path_reports_on_load(self, client, sign_in, linked):
        """Same as the app: a permission revoked in browser settings weeks
        later never reaches this server on its own."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)
        # Bounded by the two markers rather than by the next `return api;`,
        # because the branch returns early for a browser that cannot do push
        # and that return comes first.
        web_branch = page[
            page.index('api.transport = "web"'):page.index('once("dos-push-init")')
        ]

        assert "api.report();" in web_branch
