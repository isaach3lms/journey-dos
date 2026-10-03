"""Proving a notification arrives, rather than reporting that it should.

The check page already reads every setting involved and every one of them can
say yes while the phone stays silent: a key rotated on the provider's side, a
device the provider dropped, a login with no roster record behind it. The only
honest answer is a notification that actually lands, so the page can send one.

What is worth testing here is not that it sends. It is that each distinct
reason it did not send comes back as a different answer, because every one of
them is a different thing to go and fix and a page that collapses them into
"something went wrong" sends somebody to reinstall an app over a wrong server
key.
"""

import pytest

from app.models import Church, NotificationPreference, Person, User
from app.models.base import utcnow
from app.push import MemoryPushTransport
from app.push import selftest
from app.push.transport import PushFailed, SubscriptionGone
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def provider():
    """A transport that addresses people, which is what OneSignal is."""
    return MemoryPushTransport(addresses_people=True)


def a_member(db, journey, email="kaela@example.com", with_login=True):
    person = Person(church_id=journey.id, first_name="Kaela", last_name="Menz",
                    email=email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    if not with_login:
        db.session.commit()
        return person, None
    user = User(church_id=journey.id, email=email, name="Kaela Menz",
                role="member", person_id=person.id)
    user.set_password(PASSWORD)
    user.mark_verified()
    user.accept_community()
    user.ensure_push_external_id()
    db.session.add(user)
    db.session.commit()
    return person, user


def run(user, provider):
    return selftest.run(user, title="Journey", body="Test.", transport=provider)


class TestWhatItReports:
    def test_a_reachable_phone_is_sent_to(self, db, journey, provider):
        _, user = a_member(db, journey)

        assert run(user, provider) == selftest.SENT
        assert provider.sent[0][0] == user.push_external_id

    def test_a_phone_the_provider_has_dropped_is_blocked_not_failed(
            self, db, journey, provider):
        """The ordinary case of somebody who installed the app and never
        allowed notifications. Reporting it as a failure points at the server
        when the fix is on the phone."""
        _, user = a_member(db, journey)
        provider.fail_with = SubscriptionGone("no subscribers")

        assert run(user, provider) == selftest.BLOCKED

    def test_a_refused_send_is_a_server_problem(self, db, journey, provider):
        """A wrong or expired key. Nobody in the church is receiving anything,
        and no amount of reinstalling the app will change that, so this must
        not read like a phone problem."""
        _, user = a_member(db, journey)
        provider.fail_with = PushFailed("OneSignal refused (401)")

        assert run(user, provider) == selftest.FAILED

    def test_an_account_that_never_registered_is_its_own_answer(
            self, db, journey, provider):
        _, user = a_member(db, journey)
        user.push_external_id = None
        db.session.commit()

        assert run(user, provider) == selftest.NOT_REGISTERED
        assert provider.sent == []

    def test_a_login_with_no_roster_record_is_named_as_such(
            self, db, journey, provider):
        """Production notifications go to a person, not a login. An account
        with no person attached receives nothing forever, and that is a staff
        fix rather than anything the phone's owner can do."""
        user = db.session.scalar(db.select(User).where(
            User.email == "member@journeychurchsemo.com"))
        user.person_id = None
        db.session.commit()

        assert run(user, provider) == selftest.UNLINKED

    def test_a_transport_that_cannot_send_says_so(self, db, journey):
        """The important one. The development transport prints to the log and
        reports success, so a page that trusted it would show a green tick to
        somebody whose server has no keys at all."""
        _, user = a_member(db, journey)
        logs_only = MemoryPushTransport(addresses_people=False)

        assert selftest.run(user, title="J", body="T",
                            transport=logs_only) == selftest.UNCONFIGURED
        assert logs_only.sent == []


class TestItGoesDownTheRealPath:
    def test_an_opt_out_does_not_suppress_the_test_somebody_asked_for(
            self, db, journey, provider):
        """The category is transactional on purpose. Somebody who turned off
        announcements has not asked to be told their phone is broken."""
        person, user = a_member(db, journey)
        db.session.add(NotificationPreference(
            church_id=journey.id, person_id=person.id,
            category=selftest.CATEGORY, allowed=False,
        ))
        db.session.commit()

        assert run(user, provider) == selftest.SENT

    def test_every_state_has_something_to_say(self, app):
        """A state with no sentence renders a KeyError on a phone, which is
        the one surface with no way to read the traceback."""
        from app.content import MEMBER

        for state in selftest.STATES:
            assert MEMBER[f"appcheck_test_{state}"]


class TestThePage:
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

    def test_the_button_is_there(self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/app-check/", headers=H).get_data(as_text=True)

        assert "Send me a test notification" in page
        assert "/me/app-check/test/" in page
        # Posting without one is rejected, and the failure would look like a
        # broken notification system rather than a missing input.
        assert "csrf_token" in page

    def test_it_sends_and_reports_back(self, client, sign_in, linked,
                                       monkeypatch, provider):
        monkeypatch.setattr(selftest, "build_push_transport",
                            lambda config: provider)
        sign_in("member@journeychurchsemo.com")

        answer = client.post("/me/app-check/test/", headers=H).get_json()

        assert answer["state"] == selftest.SENT
        assert "Sent" in answer["message"]
        assert len(provider.sent) == 1

    def test_a_second_tap_within_seconds_does_not_send_again(
            self, client, sign_in, linked, monkeypatch, provider):
        """Only so a stuck finger cannot sit on the provider's API. Short
        enough that a real retry is not blocked."""
        monkeypatch.setattr(selftest, "build_push_transport",
                            lambda config: provider)
        sign_in("member@journeychurchsemo.com")

        client.post("/me/app-check/test/", headers=H)
        again = client.post("/me/app-check/test/", headers=H).get_json()

        assert again["state"] == "cooldown"
        assert len(provider.sent) == 1

    def test_a_stranger_cannot_make_it_send(self, client):
        answer = client.post("/me/app-check/test/", headers=H)
        assert answer.status_code in (302, 401, 403)

    def test_it_only_ever_reaches_the_person_who_asked(
            self, db, journey, client, sign_in, linked, monkeypatch, provider):
        """The whole safety argument for leaving this on a page any member can
        open: the worst case is one notification on the sender's own phone."""
        monkeypatch.setattr(selftest, "build_push_transport",
                            lambda config: provider)
        _, somebody_else = a_member(db, journey)
        sign_in("member@journeychurchsemo.com")
        db.session.refresh(linked)
        mine = linked.push_external_id

        client.post("/me/app-check/test/", headers=H)

        assert [address for address, _ in provider.sent] == [mine]
        assert somebody_else.push_external_id not in [
            address for address, _ in provider.sent
        ]
