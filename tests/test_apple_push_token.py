"""Reaching an iPhone through Apple's own plugin instead of the provider's SDK.

**The failure.** A church's App Store app was built around Capacitor's
`PushNotifications` plugin rather than the provider's SDK. The app was
entirely capable of receiving notifications and held a real device token from
Apple, while the provider had no record of the phone, because nothing in the
app ever told it.

Every screen the church could see reported health. The account worked. The
server had its keys. The device was known. The notification check screen even
said the app "was built before notifications were added" and "needs
rebuilding", which was wrong twice: the capability was installed, and a
rebuild was not the fix. Acting on that sentence would have cost a store
review to arrive back where they started.

They reached two people out of a hundred, and the two were artefacts of an
earlier build.

**Why this route rather than a rebuild.** The token Apple issues to that
plugin is the same token the provider's SDK would have obtained, and the
provider's API accepts a subscription built from one. So the fix reaches an
app already installed on a hundred phones, today, with nothing for the member
to do but allow notifications. A rebuild needs a Mac, a store review, and
then every member taking an update.

**What the tests below are mostly about.** Not the happy path, which is four
lines. They are about the ways this fails silently: a token in the wrong
shape, an app with no push entitlement, a provider that refuses, a token that
changed since last launch. Each of those ends with a member who granted
permission and still receives nothing, which is indistinguishable from the bug
being fixed.
"""

import json
from unittest.mock import patch

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from app.push.apple import (
    SANDBOX,
    AppleRegistrationFailed,
    normalise,
    register,
)
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
TOKEN = "a1b2c3d4" * 8  # 64 hex characters, which is what Apple issues.


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def linked(db, journey):
    user = db.session.scalar(db.select(User).where(
        User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=journey.id, first_name="Alicia",
                    last_name="Romero", email=user.email, stage="member",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    user.ensure_push_external_id()
    db.session.commit()
    return user


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestWhatCountsAsAnAppleToken:
    def test_the_shape_apple_issues_is_accepted(self):
        assert normalise(TOKEN) == TOKEN

    def test_the_bracketed_form_older_layers_return_is_cleaned_up(self):
        """Apple's own APIs have returned this as `<32 bytes of hex>` with
        brackets and spaces, and the plugin layers above have not always
        agreed on stripping them. One phone with an older plugin going
        quietly unreachable is the failure this prevents."""
        messy = "<" + TOKEN[:32] + " " + TOKEN[32:] + ">"

        assert normalise(messy) == TOKEN

    def test_uppercase_is_folded(self):
        assert normalise(TOKEN.upper()) == TOKEN

    def test_anything_the_wrong_length_is_refused(self):
        """Better to refuse here than to post it. A token the provider will
        not take becomes a phone that never receives anything, and the reason
        sits in a third party's logs rather than anywhere a church looks."""
        assert normalise(TOKEN[:-1]) is None
        assert normalise(TOKEN + "a") is None

    def test_a_web_push_endpoint_is_not_a_token(self):
        """The two arrive through similar-looking code paths and confusing
        them would register nonsense."""
        assert normalise("https://web.push.apple.com/abc123") is None

    def test_nothing_is_nothing(self):
        assert normalise("") is None
        assert normalise(None) is None


class TestRegisteringWithTheProvider:
    def test_a_new_person_is_created(self, app, linked):
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen",
                       return_value=FakeResponse(200)):
                assert register("ext-1", TOKEN) == "created"

    def test_an_existing_person_is_updated_not_treated_as_a_failure(
            self, app, linked):
        """202 is what comes back on every launch after the first. Treating
        only 200 as success would make the ordinary case look broken."""
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen",
                       return_value=FakeResponse(202)):
                assert register("ext-1", TOKEN) == "updated"

    def test_it_sends_the_shape_the_provider_documents(self, app, linked):
        captured = {}

        def fake(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["auth"] = request.headers.get("Authorization")
            return FakeResponse(200)

        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="app-7", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen", side_effect=fake):
                register("ext-1", TOKEN)

        assert captured["url"].endswith("/apps/app-7/users")
        assert captured["body"]["identity"] == {"external_id": "ext-1"}
        assert captured["body"]["subscriptions"] == [
            {"type": "iOSPush", "token": TOKEN}]

    def test_production_tokens_carry_no_environment_flag(self, app, linked):
        """App Store and TestFlight are both production, which is every
        member. Sending the field on the common path is one more thing that
        can be wrong."""
        captured = {}

        def fake(request, timeout=None):
            captured["body"] = json.loads(request.data)
            return FakeResponse(200)

        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen", side_effect=fake):
                register("ext-1", TOKEN)

        assert "test_type" not in captured["body"]["subscriptions"][0]

    def test_a_development_token_says_so(self, app, linked):
        """Apple runs two push servers and a token from one is refused by the
        other, with the notification simply never arriving."""
        captured = {}

        def fake(request, timeout=None):
            captured["body"] = json.loads(request.data)
            return FakeResponse(200)

        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen", side_effect=fake):
                register("ext-1", TOKEN, sandbox=True)

        assert captured["body"]["subscriptions"][0]["test_type"] == SANDBOX

    def test_a_bad_token_is_refused_before_any_call(self, app, linked):
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen") as called:
                with pytest.raises(AppleRegistrationFailed):
                    register("ext-1", "not-a-token")

                assert called.call_count == 0

    def test_no_external_id_is_refused_before_any_call(self, app, linked):
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen") as called:
                with pytest.raises(AppleRegistrationFailed):
                    register("", TOKEN)

                assert called.call_count == 0

    def test_missing_credentials_are_refused_before_any_call(self, app,
                                                             linked):
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="", ONESIGNAL_API_KEY="")
            with patch("urllib.request.urlopen") as called:
                with pytest.raises(AppleRegistrationFailed):
                    register("ext-1", TOKEN)

                assert called.call_count == 0

    def test_a_conflict_names_itself(self, app, linked):
        """409 is the provider saying this external id already belongs to a
        different user over there. Retrying will never fix it and a human
        has to look, so the message has to say which problem it is."""
        import urllib.error

        error = urllib.error.HTTPError(
            "u", 409, "Conflict", {}, None)
        error.read = lambda: b'{"errors":["conflicting_aliases"]}'

        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen", side_effect=error):
                with pytest.raises(AppleRegistrationFailed) as caught:
                    register("ext-1", TOKEN)

        assert "different identity" in str(caught.value)

    def test_an_outage_does_not_raise_something_unrelated(self, app, linked):
        """Callers handle one exception type. A socket error escaping as
        itself would reach a member as a 500."""
        with app.test_request_context(headers=H):
            app.config.update(ONESIGNAL_APP_ID="a", ONESIGNAL_API_KEY="k")
            with patch("urllib.request.urlopen",
                       side_effect=OSError("connection reset")):
                with pytest.raises(AppleRegistrationFailed):
                    register("ext-1", TOKEN)


class TestThePhoneHandingOverItsToken:
    def test_a_token_is_registered_and_the_outcome_reported(
            self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")

        with patch("app.push.apple.register", return_value="created") as reg:
            page = client.post("/me/push-token/", headers=H,
                               json={"token": TOKEN})

        assert page.status_code == 200
        assert page.get_json() == {"ok": True, "outcome": "created"}
        assert reg.call_args.args[1] == TOKEN

    def test_it_registers_against_this_account_and_no_other(
            self, client, sign_in, linked):
        """The device is trusted to report its own token and nothing further,
        the same narrowness as the permission route beside it."""
        sign_in("member@journeychurchsemo.com")

        with patch("app.push.apple.register", return_value="created") as reg:
            # An id for somebody else, sent on purpose. Found by a mutation:
            # asserting only that the right id came out passed happily when
            # the route started preferring one from the request body, because
            # the test never sent one to prefer.
            client.post("/me/push-token/", headers=H,
                        json={"token": TOKEN, "external_id": "somebody-else"})

        assert reg.call_args.args[0] == linked.push_external_id
        assert reg.call_args.args[0] != "somebody-else"

    def test_a_token_proves_permission_was_granted(self, db, client, sign_in,
                                                   linked):
        """Apple does not issue one otherwise, so the coverage figures learn
        it here rather than waiting for a separate report on the next page
        load."""
        sign_in("member@journeychurchsemo.com")

        with patch("app.push.apple.register", return_value="created"):
            client.post("/me/push-token/", headers=H, json={"token": TOKEN})
        db.session.refresh(linked)

        assert linked.push_permission == User.PUSH_GRANTED

    def test_a_refusal_comes_back_with_its_reason(self, client, sign_in,
                                                  linked):
        """Printed on the check screen. A member pressing a button on a phone
        has no console, and "nothing happened" is the answer that sent two
        days into the wrong half of the system."""
        sign_in("member@journeychurchsemo.com")

        with patch("app.push.apple.register",
                   side_effect=AppleRegistrationFailed("Provider refused.")):
            page = client.post("/me/push-token/", headers=H,
                               json={"token": TOKEN})

        assert page.status_code == 502
        assert page.get_json()["ok"] is False
        assert "Provider refused." in page.get_json()["error"]

    def test_a_missing_token_is_a_bad_request(self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")

        assert client.post("/me/push-token/", headers=H,
                           json={}).status_code == 400

    def test_a_non_string_token_is_a_bad_request(self, client, sign_in,
                                                 linked):
        sign_in("member@journeychurchsemo.com")

        assert client.post("/me/push-token/", headers=H,
                           json={"token": 12345}).status_code == 400

    def test_signing_in_is_required(self, client):
        """Anonymous registration would let anybody attach a phone to
        somebody else's notifications."""
        page = client.post("/me/push-token/", headers=H,
                           json={"token": TOKEN})

        assert page.status_code in (302, 401, 403)


class TestWhatTheCheckScreenSays:
    def test_apples_plugin_is_recognised_as_notifications(self, client,
                                                          sign_in, linked):
        """The row used to see only the provider's SDK, so an app built
        around Apple's own plugin was told it needed rebuilding. That
        sentence would have cost a store review to disprove."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        # The exact line, not just the plugin name. That name also appears
        # in the shared partial this page includes, so the looser assertion
        # was being satisfied by a different file and passed with this row
        # switched off.
        assert ("var apple = !signal && plugins && plugins.PushNotifications;"
                in page)
        # Asserted without the apostrophe: `tojson` escapes it to \u0027, so
        # the copy as written in content.py never appears literally.
        assert "own notification system, which works" in page

    def test_the_old_wording_no_longer_claims_a_rebuild_is_needed(
            self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        assert "built before notifications were added" not in page

    def test_apples_own_words_are_shown_when_it_refuses(self, client,
                                                        sign_in, linked):
        """Where a build with no push entitlement identifies itself. The
        provider's logs show nothing, because the attempt never reached
        them."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        # The condition, not the body. The reference to `last_error` lives
        # inside the block, so it stays in the file when the condition is
        # switched off, and a presence check cannot tell the difference.
        assert "if (window.dosPush && window.dosPush.last_error) {" in page
        assert "What your phone reported" in page
