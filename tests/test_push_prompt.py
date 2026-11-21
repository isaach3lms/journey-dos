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


class TestAndroidIsNotAniPhone:
    """The two platforms answer a refusal differently, and the card has to
    know which one it is standing on.

    **iOS shows its prompt once per install, ever.** Everything above is
    shaped by that: the single chance, the card that spends it deliberately,
    the Settings instruction once it is gone.

    **Android does not work that way at all.** A refusal there is an answer to
    that one prompt and nothing more. Ask again and the system asks again. It
    takes two refusals before Android stops showing it, and only then is
    Settings the honest instruction.

    Shipping the iOS copy to Android would tell somebody who refused once to
    go hunting through Settings for a switch, when one more tap on the card
    would have shown them the prompt again. That is a worse outcome than the
    bug it came from: the member is now being sent somewhere they do not need
    to go, by a sentence that sounds authoritative.
    """

    def test_the_app_says_which_platform_it_is(self, client, sign_in, linked):
        """Read once, in the shared partial, rather than at each screen that
        needs it. Two screens guessing separately is how they come to
        disagree."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "platform: platform" in page
        assert "window.Capacitor.getPlatform" in page

    def test_only_android_may_be_asked_twice(self, client, sign_in, linked):
        """Written as a capability rather than a platform check, so a third
        platform lands on the cautious branch instead of the wrong one."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'can_reask: platform === "android"' in page

    def test_android_is_offered_another_prompt_before_settings(
            self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "your phone will ask again" in page.lower()
        assert "Ask me again" in page

    def test_android_hears_about_settings_once_it_has_stopped_asking(
            self, client, sign_in, linked):
        """The second half. The retry copy would become a lie after Android
        has given up, because the button stops producing a prompt."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "Your phone has stopped asking" in page

    def test_the_iphone_copy_is_still_there_for_iphones(self, client, sign_in,
                                                        linked):
        """The regression guard. Adding the Android branch must not take the
        iOS one with it."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "Your phone is blocking notifications for this app" in page
        assert "Open Settings" in page

    def test_refusals_are_counted_somewhere_that_outlives_the_app_closing(
            self, client, sign_in, linked):
        """The thing being counted lives in the operating system. Counting it
        in sessionStorage would reset every launch, so the card would offer a
        retry forever to a phone that had stopped obliging."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'window.localStorage.setItem("dos-push-no"' in page
        assert "var ANDROID_PROMPTS = 2" in page

    def test_turning_them_on_clears_the_count(self, client, sign_in, linked):
        """Somebody who allows notifications, turns them off in Settings
        months later, and comes back should get the ordinary ask rather than
        being told on their first refusal that their phone has given up."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'window.localStorage.removeItem("dos-push-no")' in page


class TestAskingThePhoneWhatItDecided:
    """The bug that made every screen above do nothing.

    The partial asked for permission with `getPermissionAsync`. That method
    exists on neither platform and never has: both native plugins define
    `getPermission`, returning `{permission: true}`.

    Nothing looked broken, which is the part worth remembering. Capacitor's
    bridge builds a callable for any method name asked of it, so the
    `typeof === "function"` guard passed. The call reached the native side,
    the native side rejected an unknown method, and the rejection was caught
    and turned into null.

    null is the "could not answer" value, and every screen treats it as a
    reason to stay quiet rather than risk telling somebody who already has
    notifications to switch them on. So:

      - the ask card never appeared, on any phone, ever;
      - tapping Turn them on threw the prompt's answer away and re-read it
        through the same broken call, so granting permission still left the
        card insisting it had not been granted;
      - `report` never posted, so `push_permission` stayed unset and every
        account read as never-asked on the staff coverage screen.

    A notification system delivering correctly end to end reported itself,
    from inside the product, as one nobody had ever switched on. The tests
    below pin the method name, the shape it returns, and the fallback that
    keeps an older wrapper working.
    """

    def test_it_asks_for_the_permission_by_the_name_that_exists(
            self, client, sign_in, linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "p.getPermission()" in page

    def test_the_working_call_is_not_skipped_past(self, client, sign_in,
                                                  linked):
        """Found by a mutation, and it exposed what these tests cannot do.

        Every assertion in this class checks that a string is present. The
        original bug was not a missing string: `getPermissionAsync` was
        spelled correctly and guarded properly, and was simply a name the
        plugin does not have. A structural test cannot see that, and it
        cannot see an early return placed above the working call either,
        which leaves the right code in the file and unreachable.

        One `return legacy();` is the fallback at the end of the guard.
        A second one means something returns before `getPermission` is ever
        reached, which is the original bug wearing the fixed code as a
        costume. Pinning the count is a proxy, not a proof. The proof is
        running the page in a browser against a fake plugin, which is what
        `prove_prompt.py` beside this suite does.
        """
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert page.count("return legacy();") == 1

    def test_it_reads_the_shape_the_plugin_actually_returns(
            self, client, sign_in, linked):
        """`{permission: true}`, not a bare boolean. Reading it as a boolean
        makes every answer truthy, which is worse than the bug it replaced:
        a refusal would register as consent."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'typeof result.permission === "boolean"' in page

    def test_an_unrecognised_answer_is_not_mistaken_for_a_refusal(
            self, client, sign_in, linked):
        """null and false have to stay apart. "The phone did not answer" and
        "the phone said no" lead to opposite screens."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "function asBool(result)" in page
        assert "return null;" in page

    def test_the_old_name_is_still_tried_afterwards(self, client, sign_in,
                                                    linked):
        """This partial is not the only thing built against it. A wrapper
        carrying an older plugin must not be broken by the fix to a newer
        one, so the old name becomes the fallback rather than disappearing."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "getPermissionAsync" in page
        assert "function legacy()" in page

    def test_the_prompt_result_is_used_rather_than_asked_for_again(
            self, client, sign_in, linked):
        """The second half of the bug. The tap already carries the answer."""
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "api.report(asBool(result))" in page

    def test_reporting_accepts_an_answer_already_in_hand(self, client, sign_in,
                                                         linked):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert "api.report = function (known)" in page
        assert "known === true || known === false" in page
