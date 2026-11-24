"""Asking the notification service what it has.

**Why this exists at all.** Three rounds of debugging a silent iPhone each
ended with somebody being asked to read a screen on the phone and report
back, and each round moved nothing. The phone can only report the half of the
system that was working: that permission was granted, that a token was handed
over. It cannot see whether the service kept the token or whether the service
holds the Apple credential needed to deliver to it.

So the states here are the point of the module, and they are what these tests
cover. Each one sends whoever is reading somewhere different, and two of them
look identical from the phone:

  * no Apple credential at the service, which silences every iPhone at once
    while every send is reported as accepted
  * a credential pointed at the wrong Apple environment, same silence,
    completely different fix
  * a person the service has never heard of, so nothing registered
  * a person with an iPhone the service holds as unsubscribed

A wrong answer here is worse than no answer, because somebody acts on it.
"""

import json
import urllib.error
import urllib.request

import pytest

from tests.conftest import JOURNEY_HOST
from app.push.diagnose import (
    APPLE_NO_CREDENTIAL,
    APPLE_READY,
    APPLE_SANDBOX,
    APPLE_UNKNOWN,
    PERSON_APPLE_NO_TOKEN,
    PERSON_APPLE_OFF,
    PERSON_NO_APPLE,
    PERSON_NO_PUSH,
    PERSON_NOT_FOUND,
    PERSON_READY,
    ServiceUnreachable,
    apple_state,
    diagnose,
    person_state,
    subscriptions,
)


class TestWhetherItCanReachAnIPhoneAtAll:
    """The failure that looks like a bug in our code. The service accepts
    every send, reports it as accepted, and drops it, because it has no
    credential to talk to Apple with."""

    def test_no_credential_at_all(self):
        assert apple_state({}) == APPLE_NO_CREDENTIAL

    def test_a_p8_key_counts(self):
        """One of the two ways to hold a credential."""
        assert apple_state({"apns_key_id": "ABC123"}) == APPLE_READY

    def test_a_p12_certificate_counts_too(self):
        """The other way. Older setups use this, and reporting them as
        unconfigured would send somebody to upload a key they do not need."""
        assert apple_state({"apns_certificates": "-----BEGIN"}) == APPLE_READY

    def test_an_empty_string_is_not_a_credential(self):
        """Their API returns the field present and empty rather than absent,
        so a truthiness check on the key alone reports a working setup."""
        assert apple_state({"apns_key_id": "  "}) == APPLE_NO_CREDENTIAL

    def test_sandbox_is_called_out_separately(self):
        """Same silence as no credential, and a different thing to fix.
        Collapsing the two would send somebody to upload a key they already
        have."""
        state = apple_state({"apns_key_id": "ABC", "apns_env": "sandbox"})

        assert state == APPLE_SANDBOX

    def test_the_environment_is_read_case_insensitively(self):
        state = apple_state({"apns_key_id": "ABC", "apns_env": "Sandbox"})

        assert state == APPLE_SANDBOX

    def test_production_is_ready(self):
        state = apple_state({"apns_key_id": "ABC", "apns_env": "production"})

        assert state == APPLE_READY

    def test_nothing_to_read_says_nothing(self):
        """Not "configured" and not "broken". A diagnostic that guesses when
        it was told nothing is the thing that cost the three days."""
        assert apple_state(None) == APPLE_UNKNOWN
        assert apple_state("not a dict") == APPLE_UNKNOWN


class TestWhatItHasForOnePerson:
    def _person(self, *subs):
        return {"subscriptions": list(subs)}

    def _apple(self, **over):
        base = {"type": "iOSPush", "enabled": True, "token": "a" * 64}
        base.update(over)
        return base

    def test_never_heard_of_them(self):
        """Distinct from having them with no devices: one means the phone
        never reached the service, the other means it reached it and left
        nothing behind."""
        assert person_state(None) == PERSON_NOT_FOUND

    def test_known_with_no_devices(self):
        assert person_state(self._person()) == PERSON_NO_PUSH

    def test_an_android_only_account(self):
        assert person_state(self._person(
            {"type": "AndroidPush", "enabled": True, "token": "x"},
        )) == PERSON_NO_APPLE

    def test_an_iphone_with_no_token_is_half_registered(self):
        """Registration started and never finished, which is what a build
        without the push capability looks like from here."""
        assert person_state(
            self._person(self._apple(token=""))) == PERSON_APPLE_NO_TOKEN

    def test_an_iphone_held_as_unsubscribed(self):
        assert person_state(
            self._person(self._apple(enabled=False))) == PERSON_APPLE_OFF

    def test_an_iphone_that_should_work(self):
        assert person_state(self._person(self._apple())) == PERSON_READY

    def test_one_working_iphone_among_several_is_enough(self):
        """Somebody with an old phone still on the account should not be
        reported as unreachable because the old one is dead."""
        assert person_state(self._person(
            self._apple(enabled=False, token="b" * 64),
            self._apple(),
        )) == PERSON_READY

    def test_email_and_sms_are_not_devices_here(self):
        """They sit in the same list and are not why a phone is quiet."""
        assert person_state(self._person(
            {"type": "Email", "enabled": True, "token": "a@b.com"},
        )) == PERSON_NO_PUSH

    def test_safari_is_not_counted_as_an_iphone(self):
        """A different channel and a different credential. Counting it would
        report a working iPhone where there is only a laptop browser."""
        assert person_state(self._person(
            {"type": "SafariPush", "enabled": True, "token": "x"},
        )) == PERSON_NO_APPLE


class TestWhatReachesTheScreen:
    def test_the_whole_token_never_does(self):
        """It is a credential. The last few characters tell two phones apart
        and are no use to anybody reading over a shoulder."""
        token = "f" * 58 + "abc123"
        subs = subscriptions({"subscriptions": [
            {"type": "iOSPush", "enabled": True, "token": token},
        ]})

        assert subs[0]["token_tail"] == "abc123"
        assert token not in json.dumps(subs)

    def test_the_service_detail_is_passed_through_rather_than_interpreted(self):
        """Their own docs describe this field without saying what the values
        mean, so inventing a reading of it here would be guessing in the one
        place somebody is trusting us not to."""
        subs = subscriptions({"subscriptions": [
            {"type": "iOSPush", "enabled": True, "token": "x",
             "notification_types": -31},
        ]})

        assert subs[0]["status_detail"] == -31

    def test_a_subscription_that_is_not_a_dict_is_skipped(self):
        subs = subscriptions({"subscriptions": ["nonsense", None]})

        assert subs == []


class TestAskingTheService:
    """`diagnose` makes two requests and must report what it managed to learn
    when one of them fails. It runs because something is already broken."""

    def _canned(self, monkeypatch, app=None, person=None,
                app_error=None, person_error=None):
        def fake(request, timeout=None):
            url = request.full_url
            if "/users/by/external_id/" in url:
                if person_error:
                    raise person_error
                payload = person
            else:
                if app_error:
                    raise app_error
                payload = app

            class Response:
                def read(self):
                    return json.dumps(payload).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Response()

        monkeypatch.setattr(urllib.request, "urlopen", fake)

    def test_no_credentials_configured_asks_nothing(self):
        found = diagnose("", "", "ext-1")

        assert found["asked"] is False
        assert found["errors"]

    def test_both_answers_come_back(self, monkeypatch):
        self._canned(
            monkeypatch,
            app={"apns_key_id": "ABC", "apns_env": "production",
                 "apns_bundle_id": "com.church.app", "messageable_players": 104},
            person={"subscriptions": [
                {"type": "iOSPush", "enabled": True, "token": "a" * 64},
            ]},
        )

        found = diagnose("app-1", "key-1", "ext-1")

        assert found["apple"] == APPLE_READY
        assert found["person"] == PERSON_READY
        assert found["bundle_id"] == "com.church.app"
        assert found["messageable"] == 104
        assert not found["errors"]

    def test_a_missing_person_is_an_answer_not_an_error(self, monkeypatch):
        """Their API answers 404, which is the service telling us it has
        never heard of them. Treating that as a failed lookup would hide the
        most useful finding this page can produce."""
        self._canned(
            monkeypatch,
            app={"apns_key_id": "ABC"},
            person_error=urllib.error.HTTPError(
                "u", 404, "Not Found", {}, None),
        )

        found = diagnose("app-1", "key-1", "ext-1")

        assert found["person"] == PERSON_NOT_FOUND
        assert not found["errors"]

    def test_the_app_lookup_failing_still_reports_the_person(self, monkeypatch):
        """Half an answer beats none. The second request is the one that
        says whether this phone registered."""
        self._canned(
            monkeypatch,
            app_error=urllib.error.HTTPError("u", 500, "Boom", {}, None),
            person={"subscriptions": [
                {"type": "iOSPush", "enabled": False, "token": "a" * 64},
            ]},
        )

        found = diagnose("app-1", "key-1", "ext-1")

        assert found["apple"] == APPLE_UNKNOWN
        assert found["person"] == PERSON_APPLE_OFF
        assert found["errors"]

    def test_an_account_with_no_service_id_is_not_looked_up(self, monkeypatch):
        """Nothing has ever registered for them, so there is nothing to ask
        about and no request worth making."""
        self._canned(monkeypatch, app={"apns_key_id": "ABC"})

        found = diagnose("app-1", "key-1", None)

        assert found["person"] == PERSON_NOT_FOUND

    def test_the_key_is_sent_the_way_their_api_wants_it(self, monkeypatch):
        """`Key <value>`, not `Bearer`. Getting this wrong gives a 401 that
        reads like a wrong key rather than a wrong header."""
        seen = {}

        def fake(request, timeout=None):
            seen["auth"] = request.headers.get("Authorization")

            class Response:
                def read(self):
                    return b"{}"

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Response()

        monkeypatch.setattr(urllib.request, "urlopen", fake)
        diagnose("app-1", "secret-key", None)

        assert seen["auth"] == "Key secret-key"

    def test_it_only_ever_reads(self, monkeypatch):
        """A diagnostic that can also reconfigure is one nobody dares run."""
        methods = []

        def fake(request, timeout=None):
            methods.append(request.get_method())

            class Response:
                def read(self):
                    return b"{}"

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Response()

        monkeypatch.setattr(urllib.request, "urlopen", fake)
        diagnose("app-1", "key-1", "ext-1")

        assert methods and set(methods) == {"GET"}

    def test_an_unreachable_service_is_not_a_verdict(self, monkeypatch):
        """"We could not look" and "we looked and it is broken" send
        somebody to two different places."""
        self._canned(
            monkeypatch,
            app_error=OSError("connection reset"),
            person_error=OSError("connection reset"),
        )

        found = diagnose("app-1", "key-1", "ext-1")

        assert found["apple"] == APPLE_UNKNOWN
        assert len(found["errors"]) == 2

    def test_a_non_404_http_error_carries_its_reason(self):
        from app.push.diagnose import _get

        with pytest.raises(ServiceUnreachable, match="401"):
            def fake(request, timeout=None):
                raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

            original = urllib.request.urlopen
            urllib.request.urlopen = fake
            try:
                _get("/apps/x", "key")
            finally:
                urllib.request.urlopen = original


def content_marker(body: str) -> bool:
    """Did the diagnostic itself render, rather than the "nothing to ask"
    branch? Without the service key configured the page renders a single
    sentence, and every assertion about what is absent from it holds
    trivially."""
    return "Sending to iPhones" in body


class TestThePage:
    def test_staff_can_open_it(self, monkeypatch, staff, app):
        import app.push.diagnose as diag

        app.config["ONESIGNAL_API_KEY"] = "test-key"
        monkeypatch.setattr(diag, "fetch_app", lambda *a, **k: {
            "apns_key_id": "ABC", "apns_env": "production"})
        monkeypatch.setattr(diag, "fetch_person", lambda *a, **k: {
            "subscriptions": [
                {"type": "iOSPush", "enabled": True, "token": "a" * 64},
            ]})

        page = staff.get("/settings/notification-service/",
                         headers={"Host": JOURNEY_HOST})

        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "iOSPush" in body, "the page rendered without the findings"

    def test_a_member_cannot(self, client, member):
        """It reports how the church's push is set up."""
        page = client.get("/settings/notification-service/",
                          headers={"Host": JOURNEY_HOST})

        assert page.status_code in (302, 403, 404)

    def test_the_page_never_prints_the_key(self, monkeypatch, staff, app):
        """The one thing on this screen that would be worth stealing."""
        import app.push.diagnose as diag

        app.config["ONESIGNAL_API_KEY"] = "super-secret-key-value"
        monkeypatch.setattr(diag, "fetch_app", lambda *a, **k: {
            "apns_key_id": "ABC"})
        monkeypatch.setattr(diag, "fetch_person", lambda *a, **k: None)

        body = staff.get("/settings/notification-service/",
                         headers={"Host": JOURNEY_HOST}).get_data(as_text=True)

        assert "super-secret-key-value" not in body
        # Proves the page rendered the real branch. Asserting a secret is
        # absent from a page that rendered nothing passes for free, which is
        # how a leak test ends up guarding nothing.
        assert content_marker(body), "the page rendered without the findings"

    def test_the_page_never_prints_a_whole_token(self, monkeypatch, staff,
                                                 app):
        import app.push.diagnose as diag

        app.config["ONESIGNAL_API_KEY"] = "test-key"
        token = "f" * 58 + "abc123"
        monkeypatch.setattr(diag, "fetch_app", lambda *a, **k: {
            "apns_key_id": "ABC"})
        monkeypatch.setattr(diag, "fetch_person", lambda *a, **k: {
            "subscriptions": [
                {"type": "iOSPush", "enabled": True, "token": token},
            ]})

        body = staff.get("/settings/notification-service/",
                         headers={"Host": JOURNEY_HOST}).get_data(as_text=True)

        assert token not in body
        assert "abc123" in body
