"""Getting people to turn notifications on.

There is no way to turn them on for a church. iOS grants notification
permission per device, to the person holding the phone, on a tap, and Apple
exposes nothing a server or an administrator can use to do it for them. So
the only lever is the ask, and the ask has one property that makes it worth
this much care: **iOS shows its prompt once per install, ever.** A refusal
cannot be undone from inside the app.

The app used to fire that prompt automatically on the first signed-in page
load. That spent the single chance on somebody who had just typed a password
and had not seen the app, and every refusal was permanent. The ask is now a
button under a sentence saying what it is for.

What is tested here: that nothing asks on its own any more, that the server
records what a phone reports so a church can see its own coverage, and that
the report route cannot be used for anything else.
"""

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from app.push.health import push_health, reach, transport_summary
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}

ONESIGNAL = {
    "PUSH_TRANSPORT": "onesignal",
    "ONESIGNAL_APP_ID": "app-id",
    "ONESIGNAL_API_KEY": "secret",
}


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
    db.session.commit()
    return user


class TestNothingAsksOnItsOwn:
    def test_the_page_does_not_fire_the_prompt_on_load(self, client, sign_in,
                                                       linked):
        """The regression this change exists to prevent. An automatic prompt
        is indistinguishable from a working one until a church has burned
        through its one chance with every member it has."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        # The old automatic ask, keyed on a once-per-launch flag.
        assert "dos-push-ask" not in page
        # Asking is a function the screens call, not something that runs.
        assert "api.ask = function" in page

    def test_the_ask_is_a_button_with_a_reason_beside_it(self, client, sign_in,
                                                         linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "data-ask-go" in page
        assert "Turn on notifications" in page
        # Somebody who says no to the card has to be able to come back.
        assert "data-ask-later" in page

    def test_the_card_is_hidden_until_the_device_answers(self, client, sign_in,
                                                         linked):
        """Three things decide whether it should appear and only the phone
        knows any of them. Rendering it open would show a card about
        notifications to everybody on a laptop."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'id="pushask" hidden' in page

    def test_the_you_screen_no_longer_calls_the_app_unsupported(
            self, client, sign_in, linked):
        """It used to. iOS exposes no Web Push to a wrapped web view, so the
        only branch that existed told people notifications were impossible on
        the exact device they were working on."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/you/", headers=H).get_data(as_text=True)

        assert "window.dosPush && window.dosPush.available" in page


class TestReportingWhatThePhoneSaid:
    def test_a_granted_permission_is_recorded(self, db, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")

        answer = client.post("/me/push-state/", json={"granted": True},
                             headers=H)

        db.session.refresh(linked)
        assert answer.get_json()["permission"] == "granted"
        assert linked.push_is_on is True
        assert linked.push_asked_at is not None

    def test_a_refusal_is_recorded_as_a_refusal(self, db, client, sign_in,
                                                linked):
        """Not the same as never asked. One is somebody to nudge into the
        app, the other is somebody who has to be walked into iOS Settings,
        and a column that collapsed them would hide which."""
        sign_in("member@journeychurchsemo.com")

        client.post("/me/push-state/", json={"granted": False}, headers=H)

        db.session.refresh(linked)
        assert linked.push_permission == "denied"
        assert linked.push_is_on is False

    def test_turning_them_off_later_is_picked_up(self, db, client, sign_in,
                                                 linked):
        """Permission can be revoked in iOS Settings weeks later with
        nothing reaching this server, so the report has to be able to move
        the value back."""
        sign_in("member@journeychurchsemo.com")
        client.post("/me/push-state/", json={"granted": True}, headers=H)

        client.post("/me/push-state/", json={"granted": False}, headers=H)

        db.session.refresh(linked)
        assert linked.push_permission == "denied"

    def test_the_first_answer_sets_the_asked_time_and_later_ones_do_not(
            self, db, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        client.post("/me/push-state/", json={"granted": False}, headers=H)
        db.session.refresh(linked)
        first = linked.push_asked_at

        client.post("/me/push-state/", json={"granted": True}, headers=H)
        db.session.refresh(linked)

        assert linked.push_asked_at == first

    def test_the_page_carries_what_the_server_already_believes(
            self, db, client, sign_in, linked):
        """So an unchanged answer costs no request, which is every page load
        after the first."""
        linked.push_permission = "granted"
        db.session.commit()
        sign_in("member@journeychurchsemo.com")

        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert '"granted"' in page

    def test_a_signed_out_device_cannot_report(self, client):
        answer = client.post("/me/push-state/", json={"granted": True},
                             headers=H)

        assert answer.status_code in (302, 401, 403)

    def test_anything_but_a_boolean_is_refused(self, db, client, sign_in,
                                               linked):
        """The route exists so a device can report one thing about itself.
        Accepting a string would let it write whatever it liked into an
        indexed column that staff screens count."""
        sign_in("member@journeychurchsemo.com")

        for payload in ({"granted": "granted"}, {"granted": 1}, {}, {"granted": None}):
            answer = client.post("/me/push-state/", json=payload, headers=H)
            assert answer.status_code == 400, payload

        db.session.refresh(linked)
        assert linked.push_permission is None

    def test_a_device_can_only_report_about_itself(self, db, journey, client,
                                                  sign_in, linked):
        """There is no id in the route and no id in the body. The only row it
        can write is the signed-in account's own."""
        other = User(church_id=journey.id, email="other@journeychurchsemo.com",
                     name="Other Person", role="member")
        other.set_password(PASSWORD)
        other.mark_verified()
        db.session.add(other)
        db.session.commit()

        sign_in("member@journeychurchsemo.com")
        client.post("/me/push-state/", json={"granted": True}, headers=H)

        db.session.refresh(other)
        assert other.push_permission is None


class TestWhatStaffCanSee:
    def test_the_counts_split_the_three_different_problems(self, db, journey,
                                                           linked):
        """On, said no, and never asked are three different things to do
        something about, so they are three numbers rather than one."""
        linked.push_permission = "granted"
        for email, state in (("a@j.test", "denied"), ("b@j.test", None)):
            user = User(church_id=journey.id, email=email, name="Somebody",
                        role="member", push_permission=state)
            user.set_password(PASSWORD)
            db.session.add(user)
        db.session.commit()

        counts = reach(journey.id)

        assert counts["granted"] == 1
        assert counts["denied"] == 1
        assert counts["unasked"] == counts["accounts"] - 2
        assert counts["granted"] + counts["denied"] + counts["unasked"] \
            == counts["accounts"]

    def test_a_kiosk_account_is_not_counted(self, db, journey):
        """A check-in iPad is not a person who could be notified, and
        counting it would quietly lower the number staff are trying to
        raise."""
        before = reach(journey.id)["accounts"]
        kiosk = User(church_id=journey.id, email="kiosk@journey.invalid",
                     name="Lobby iPad", role="leader", is_kiosk=True)
        kiosk.set_password(PASSWORD)
        db.session.add(kiosk)
        db.session.commit()

        assert reach(journey.id)["accounts"] == before

    def test_another_churchs_accounts_are_not_counted(self, db, journey):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = User(church_id=riverbend.id, email="them@riverbend.test",
                      name="Them", role="member", push_permission="granted")
        theirs.set_password(PASSWORD)
        db.session.add(theirs)
        db.session.commit()

        assert reach(journey.id)["granted"] == 0


class TestTheSettingsPageStoppedLying:
    def test_onesignal_counts_as_a_real_transport(self):
        """The bug this found. The health check tested for the string
        'webpush', so a church running on OneSignal was told its
        notifications were off while they were being delivered."""
        summary = transport_summary(ONESIGNAL)

        assert summary["real"] is True
        assert summary["provider"] is True

    def test_a_provider_with_no_key_is_reported_as_missing_a_key(self):
        summary = transport_summary({**ONESIGNAL, "ONESIGNAL_API_KEY": ""})

        assert summary["real"] is False

    def test_web_push_still_works_the_way_it_did(self):
        """Churches on the old transport must not be broken by the fix."""
        summary = transport_summary({
            "PUSH_TRANSPORT": "webpush", "VAPID_PUBLIC_KEY": "pub",
            "VAPID_PRIVATE_KEY": "priv",
        })

        assert summary["real"] is True
        assert summary["provider"] is False

    def test_a_provider_with_somebody_on_reports_on(self, db, journey, app,
                                                    linked):
        linked.push_permission = "granted"
        db.session.commit()
        app.config.update(ONESIGNAL)

        assert push_health(journey.id)["state"] == "on"

    def test_a_provider_with_nobody_on_says_nobody_rather_than_off(
            self, db, journey, app):
        """Two opposite problems. 'Off' sends somebody to the Render
        dashboard to fix keys that are already correct."""
        app.config.update(ONESIGNAL)

        assert push_health(journey.id)["state"] == "nobody_on"

    def test_the_settings_page_shows_the_coverage(self, db, journey, client,
                                                  staff, app, linked):
        app.config.update(ONESIGNAL)
        linked.push_permission = "granted"
        db.session.commit()

        page = client.get("/settings/", headers=H).get_data(as_text=True)

        assert "Who can be reached" in page
        assert "have not been asked yet" in page

    def test_it_says_plainly_that_nobody_can_do_it_for_them(self, client, staff,
                                                            app):
        """The answer to the question that produced this work. A staff member
        looking for a switch should find the sentence saying there is not
        one, rather than keep looking."""
        app.config.update(ONESIGNAL)

        page = client.get("/settings/", headers=H).get_data(as_text=True)

        assert "no way to turn notifications on for somebody else" in page
