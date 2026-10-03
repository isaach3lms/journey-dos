"""Reaching an iPhone.

Web Push, which this system already had, cannot. Inside the wrapper the app
ships as, Apple exposes no push API at all, so every notification the platform
sent was reaching browsers and desktops and nothing the church actually uses.
OneSignal is the transport that reaches Apple's own push service.

Two things are worth testing and one is not. Worth testing: that a person is
addressed by an id which tells the provider nothing about them, and that the
opt-out still decides, because routing notifications through somebody else's
server is exactly where a consent check gets quietly skipped. Not worth
testing: that OneSignal delivers, which is their job and cannot be asserted
from here.
"""

import json

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from app.push import MemoryPushTransport, PushMessage, send_to_person
from app.push.transport import (
    OneSignalTransport,
    PushFailed,
    SubscriptionGone,
    build_push_transport,
)
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def provider():
    return MemoryPushTransport(addresses_people=True)


def a_member(db, journey, first="Kaela", email="kaela@example.com"):
    """Somebody with a roster record and a login, which is what having the
    app installed implies."""
    person = Person(church_id=journey.id, first_name=first, last_name="Menz",
                    email=email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user = User(church_id=journey.id, email=email, name=f"{first} Menz",
                role="member", person_id=person.id)
    user.set_password(PASSWORD)
    user.mark_verified()
    user.accept_community()
    user.ensure_push_external_id()
    db.session.add(user)
    db.session.commit()
    return person, user


class TestAddressingAPerson:
    def test_the_provider_is_given_the_external_id(self, db, journey, provider):
        """No device rows are involved. The provider holds the devices."""
        person, user = a_member(db, journey)

        counts = send_to_person(
            person, PushMessage(title="Sunday", body="You are on the plan."),
            "group", transport=provider,
        )

        assert counts["sent"] == 1
        assert provider.sent[0][0] == user.push_external_id

    def test_somebody_with_no_login_is_skipped_quietly(self, db, journey, provider):
        """Most of a roster has never signed in. That is not an error and the
        email is still going."""
        person = Person(church_id=journey.id, first_name="Ray", last_name="Who",
                        email="ray@example.com", stage="visitor",
                        approved_at=utcnow())
        db.session.add(person)
        db.session.commit()

        counts = send_to_person(
            person, PushMessage(title="Hi", body="There."), "group",
            transport=provider,
        )

        assert counts["sent"] == 0
        assert provider.sent == []

    def test_a_deactivated_account_is_not_reached(self, db, journey, provider):
        person, user = a_member(db, journey)
        user.is_active_account = False
        db.session.commit()

        counts = send_to_person(
            person, PushMessage(title="Hi", body="There."), "group",
            transport=provider,
        )
        assert counts["sent"] == 0

    def test_the_opt_out_still_decides(self, db, journey, provider):
        """The check that matters most here. Routing notifications through
        somebody else's server is exactly where a consent check gets quietly
        skipped, and the provider has no idea anybody opted out."""
        from app.models import NotificationPreference

        person, _ = a_member(db, journey)
        db.session.add(NotificationPreference(
            church_id=journey.id, person_id=person.id,
            category="group", allowed=False,
        ))
        db.session.commit()

        counts = send_to_person(
            person, PushMessage(title="Hi", body="There."), "group",
            transport=provider,
        )

        assert counts["suppressed"] == 1
        assert provider.sent == []

    def test_a_provider_outage_is_counted_not_raised(self, db, journey):
        """Push is best effort. A provider having a bad afternoon must never
        be the reason somebody does not get the email."""
        person, _ = a_member(db, journey)
        broken = MemoryPushTransport(addresses_people=True,
                                     fail_with=PushFailed("502"))

        counts = send_to_person(
            person, PushMessage(title="Hi", body="There."), "group",
            transport=broken,
        )
        assert counts["failed"] == 1

    def test_nobody_subscribed_is_not_a_failure_to_retry(self, db, journey):
        """The app is installed and notifications were never allowed. Nothing
        here can fix that and retrying never will."""
        person, _ = a_member(db, journey)
        empty = MemoryPushTransport(addresses_people=True,
                                    fail_with=SubscriptionGone("no subscribers"))

        counts = send_to_person(
            person, PushMessage(title="Hi", body="There."), "group",
            transport=empty,
        )
        assert counts["gone"] == 1
        assert counts["failed"] == 0


class TestTheExternalIdItself:
    def test_it_says_nothing_about_the_person(self, db, journey):
        """One provider account serves every church on the platform. An id
        like "journey-42" would tell them which church somebody attends."""
        person, user = a_member(db, journey, email="kaela@example.com")
        token = user.push_external_id

        assert token
        assert "journey" not in token.lower()
        assert "kaela" not in token.lower()
        assert str(user.id) != token
        assert len(token) >= 20

    def test_two_people_never_share_one(self, db, journey):
        _, one = a_member(db, journey, first="Kaela", email="kaela@example.com")
        _, two = a_member(db, journey, first="Sam", email="sam@example.com")
        assert one.push_external_id != two.push_external_id

    def test_it_is_minted_once_and_kept(self, db, journey):
        """It has to survive, or a phone registered yesterday is addressed by
        an id the server has forgotten."""
        _, user = a_member(db, journey)
        first = user.push_external_id
        assert user.ensure_push_external_id() == first

    def test_it_is_minted_for_anybody_already_signed_in(self, db, journey, client,
                                                        sign_in):
        """Everybody already had an account when this shipped. Minting at
        sign-in would have left them all unreachable until they signed out."""
        user = db.session.scalar(db.select(User).where(
            User.email == "member@journeychurchsemo.com"))
        user.push_external_id = None
        db.session.commit()

        sign_in("member@journeychurchsemo.com")
        client.get("/me/", headers=H)

        db.session.refresh(user)
        assert user.push_external_id


class TestTheAppIsToldWhoIsSignedIn:
    @pytest.fixture
    def linked(self, db, journey):
        """The member tab only renders its own shell for somebody with a
        roster record; without one it shows the "we cannot find you" page,
        which is a different template and carries no app chrome."""
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

    def test_the_page_carries_the_id(self, db, journey, client, sign_in, linked):
        user = linked
        sign_in("member@journeychurchsemo.com")

        page = client.get("/me/", headers=H).get_data(as_text=True)
        db.session.refresh(user)

        assert user.push_external_id in page
        assert "plugins.OneSignal" in page

    def test_a_signed_out_page_identifies_nobody(self, client, db, journey,
                                                 sign_in, linked):
        """The sign-in screen starts nothing and asks for nothing. iOS shows
        its permission prompt once ever, and spending that on somebody who is
        not into the app yet wastes it."""
        page = client.get("/auth/login", headers=H).get_data(as_text=True)
        assert "requestPermission" not in page
        assert "login(" not in page

    def test_nothing_renders_when_no_app_id_is_configured(self, app, db,
                                                          journey, sign_in,
                                                          client, linked):
        """A church whose keys are not set yet should not be running a
        notification SDK that cannot reach anything."""
        app.config["ONESIGNAL_APP_ID"] = ""
        sign_in("member@journeychurchsemo.com")

        page = client.get("/me/", headers=H).get_data(as_text=True)
        assert "plugins.OneSignal" not in page

    def test_it_starts_the_sdk_once_per_app_launch(self, db, journey, client,
                                                   sign_in, linked):
        """Repeating it every page would be harmless and pointless. The login
        call beside it is the one that must run every time."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "sessionStorage" in page
        assert "dos-push-init" in page

    def test_the_app_id_is_rendered_and_the_key_is_not(self, app, db, journey,
                                                       client, sign_in, linked):
        """The App ID is in every copy of the app already. The REST key can
        send to the whole church and must never reach a page."""
        app.config["ONESIGNAL_API_KEY"] = "os_v2_secret_value"
        sign_in("member@journeychurchsemo.com")

        page = client.get("/me/", headers=H).get_data(as_text=True)
        assert app.config["ONESIGNAL_APP_ID"] in page
        assert "os_v2_secret_value" not in page

    def test_it_runs_on_every_page_not_just_sign_in(self, db, journey, client,
                                                    sign_in, linked):
        """A member who signs out and hands the phone to their spouse has to
        stop receiving the first person's notifications, and the wrapper has
        no idea either of those things happened."""
        sign_in("member@journeychurchsemo.com")
        for path in ("/me/", "/me/you/", "/me/serve/"):
            page = client.get(path, headers=H).get_data(as_text=True)
            assert "plugins.OneSignal" in page, path

    def test_a_browser_without_the_app_is_untouched(self, db, journey, client,
                                                    sign_in, linked):
        """There is no SDK in a browser to call. The member app still works
        and the emails still send."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)
        # Guarded on the plugin existing, so nothing runs without it.
        assert "window.Capacitor" in page
        assert "if (!signal) { return; }" in page


class TestBuildingTheTransport:
    def test_the_config_selects_it(self):
        transport = build_push_transport({
            "PUSH_TRANSPORT": "onesignal",
            "ONESIGNAL_APP_ID": "app-id",
            "ONESIGNAL_API_KEY": "key",
        })
        assert isinstance(transport, OneSignalTransport)
        assert transport.addresses_people

    def test_it_refuses_to_build_without_a_key(self):
        """Failing at boot is louder than failing once per notification."""
        with pytest.raises(ValueError):
            build_push_transport({
                "PUSH_TRANSPORT": "onesignal",
                "ONESIGNAL_APP_ID": "app-id",
                "ONESIGNAL_API_KEY": "",
            })

    def test_web_push_still_fans_out_over_devices(self):
        """The two are different shapes and the old one is not broken."""
        transport = build_push_transport({
            "PUSH_TRANSPORT": "webpush", "VAPID_PRIVATE_KEY": "x",
        })
        assert transport.addresses_people is False

    def test_production_refuses_to_boot_without_the_key(self):
        from app.config import ProductionConfig

        class Fake:
            config = {
                "DATABASE_URL": "postgresql://x",
                "SQLALCHEMY_DATABASE_URI": "postgresql://x",
                "PUSH_TRANSPORT": "onesignal",
                "ONESIGNAL_APP_ID": "app-id",
                "ONESIGNAL_API_KEY": "",
                "SECRET_KEY": "x" * 32,
            }
            logger = None

        with pytest.raises(RuntimeError, match="ONESIGNAL"):
            ProductionConfig.init_app(Fake())


class TestTheRequestItSends:
    """Built without touching the network. What is asserted is the shape of
    the request, which is the part this codebase is responsible for."""

    def _captured(self, message, external_id="ext-123"):
        sent = {}

        class FakeResponse:
            def read(self):
                return b'{"id": "abc"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout=None):
            sent["url"] = request.full_url
            sent["headers"] = dict(request.headers)
            sent["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            OneSignalTransport("the-app-id", "the-key").send_to_person(
                external_id, message)
        finally:
            urllib.request.urlopen = original
        return sent

    def test_it_targets_the_external_id(self):
        sent = self._captured(PushMessage(title="Sunday", body="At nine."))
        assert sent["body"]["include_aliases"] == {"external_id": ["ext-123"]}
        assert sent["body"]["target_channel"] == "push"
        assert sent["body"]["app_id"] == "the-app-id"

    def test_the_key_goes_in_the_header_and_not_the_body(self):
        """A key in a request body ends up in logs that a key in a header
        does not."""
        sent = self._captured(PushMessage(title="Hi", body="There."))
        assert sent["headers"]["Authorization"] == "Key the-key"
        assert "the-key" not in json.dumps(sent["body"])

    def test_the_wording_is_carried(self):
        sent = self._captured(PushMessage(title="Sunday", body="At nine."))
        assert sent["body"]["headings"]["en"] == "Sunday"
        assert sent["body"]["contents"]["en"] == "At nine."

    def test_a_long_body_is_cut_to_what_a_lock_screen_shows(self):
        from app.push import MAX_BODY, MAX_TITLE

        sent = self._captured(PushMessage(title="T" * 200, body="B" * 500))
        assert len(sent["body"]["headings"]["en"]) <= MAX_TITLE
        assert len(sent["body"]["contents"]["en"]) <= MAX_BODY

    def test_it_sends_a_path_rather_than_a_hostname(self):
        """A push payload is delivered by a third party and is not the place
        to teach a phone to trust a hostname."""
        sent = self._captured(PushMessage(title="Hi", body="There.",
                                          url="/me/serve/"))
        assert sent["body"]["data"]["path"] == "/me/serve/"
        assert "http" not in json.dumps(sent["body"]["data"])

    def test_a_repeat_replaces_rather_than_stacks(self):
        """Three copies of "you are on the plan" is how somebody turns
        notifications off."""
        sent = self._captured(PushMessage(title="Hi", body="There.",
                                          tag="plan:12"))
        assert sent["body"]["collapse_id"] == "plan:12"

    def test_it_posts_to_onesignal(self):
        sent = self._captured(PushMessage(title="Hi", body="There."))
        assert sent["url"] == "https://api.onesignal.com/notifications"


class TestTheSetupCheck:
    """The page that answers "we set it up and nothing arrived".

    On an iPhone there is no console to open, so that sentence has five
    possible causes and no way to tell them apart from the outside. Each one
    gets a row, answered on the device.
    """

    @pytest.fixture
    def linked(self, db, journey):
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

    def test_any_signed_in_person_can_open_it(self, client, sign_in, linked):
        """The person holding the phone that is not working is usually not
        the person with a staff login."""
        sign_in("member@journeychurchsemo.com")
        assert client.get("/me/app-check/", headers=H).status_code == 200

    def test_signed_out_cannot(self, client):
        page = client.get("/me/app-check/", headers=H)
        assert page.status_code in (302, 401)

    def test_it_checks_each_thing_that_can_fail(self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        # Opened in the app rather than a browser.
        assert "window.Capacitor" in page
        # The SDK is in this build.
        assert "plugins.OneSignal" in page
        # Printing, which rides on a different plugin and fails separately.
        assert "plugins.Browser" in page

    def test_it_says_whether_the_server_is_configured(self, app, client,
                                                      sign_in, linked):
        """A church whose keys are not set cannot send to anybody, and that
        is invisible from the phone without being told."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)
        assert "Keys are in place" in page

        app.config["ONESIGNAL_APP_ID"] = ""
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)
        assert "not set on the server yet" in page

    def test_it_never_renders_the_rest_key(self, app, client, sign_in, linked):
        app.config["ONESIGNAL_API_KEY"] = "os_v2_secret_value"
        sign_in("member@journeychurchsemo.com")

        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)
        assert "os_v2_secret_value" not in page

    def test_it_offers_a_way_to_turn_notifications_on(self, client, sign_in,
                                                      linked):
        """iOS asks once ever. Somebody who said no needs a route back."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        assert "requestPermission" in page
        assert "Settings" in page

    def test_the_you_tab_links_to_it(self, client, sign_in, linked):
        """Somebody only looks for this after turning notifications on and
        getting nothing, so it sits directly under the notification settings
        rather than somewhere they would have to be told about."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/you/", headers=H).get_data(as_text=True)
        assert "/me/app-check/" in page
