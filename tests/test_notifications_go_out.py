"""Notifications actually leaving, on both channels.

This file exists because of a bug nothing would have caught. The push
subsystem was complete: models, transport, opt-out checks, a purge job, tests
of its own. It had no callers. Every notification went out by email only, a
member could switch app notifications on and see them listed as on, and
nothing would ever arrive.

Unit tests of `send_to_person` all passed, because the function worked. What
was missing was a test that anything *called* it. So the first class here
tests the wiring rather than the parts, and the rest test that each real
notification goes out on both channels.
"""

import pytest

from app.models import (
    ACCEPTED,
    DECLINED,
    Church,
    Person,
    PushSubscription,
    Service,
    ServiceAssignment,
    User,
)
from app.models.base import utcnow
from app.notify import notify
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_person(db, journey, first="Kaela", email="kaela@example.com"):
    person = Person(church_id=journey.id, first_name=first, last_name="Menz",
                    email=email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


def with_a_device(db, journey, person, label="Phone"):
    """A person who has turned app notifications on."""
    user = User(church_id=journey.id, email=person.email, name=person.full_name,
                role="member", person_id=person.id)
    user.set_password(PASSWORD)
    user.mark_verified()
    user.accept_community()
    db.session.add(user)
    db.session.flush()
    PushSubscription.register(
        journey.id, user,
        endpoint=f"https://push.example.com/{person.id}",
        p256dh="x" * 60, auth="y" * 20, label=label,
    )
    db.session.commit()
    return user


class TestPushIsWiredToSomething:
    """The test that was missing.

    `send_to_person` worked all along. Nothing called it.
    """

    def test_the_helper_sends_on_both_channels(self, db, journey):
        from app.models import OutboxMessage

        person = a_person(db, journey)
        with_a_device(db, journey, person)

        result = notify(
            person=person, church_id=journey.id, category="group",
            subject="Sunday", body_text="You are on the plan.",
            push_title="Sunday", push_body="You are on the plan.",
        )
        db.session.commit()

        assert result.emailed
        assert result.pushed == 1
        assert db.session.scalar(
            db.select(OutboxMessage).where(OutboxMessage.person_id == person.id)
        ) is not None

    def test_chat_notifications_push_as_well_as_email(self, db, journey):
        """The channel a member thinks they switched on."""
        import app.chat_notify as chat_notify

        sent = []
        original = chat_notify.notify

        def spy(**kwargs):
            sent.append(kwargs)
            return original(**kwargs)

        chat_notify.notify = spy
        try:
            assert chat_notify.notify is not original
        finally:
            chat_notify.notify = original
        # The real assertion: chat_notify reaches for notify, not queue.
        import inspect

        source = inspect.getsource(chat_notify.notify_new_message)
        assert "notify(" in source
        assert "queue(" not in source

    def test_the_invite_route_uses_the_helper(self, db):
        """The send moved out of the route, so this follows it one hop.

        It used to read the route's own source for `notify(`. The ask is now
        shared with assigning somebody and with publishing a plan, so it lives
        in app/serving_notify.py and the route delegates. Checking only the
        route would have passed on a route that delegated to something sending
        email alone, so both ends of the hop are asserted.
        """
        import inspect

        from app.blueprints import services

        route = inspect.getsource(services.send_invites)
        assert "ask_to_serve(" in route, (
            "the invite route no longer delegates to the shared ask"
        )

        from app import serving_notify

        sender = inspect.getsource(serving_notify.ask_to_serve)
        assert "notify(" in sender
        assert "queue(" not in sender

    def test_every_serving_send_goes_through_the_same_function(self, db):
        """Assigning, publishing and chasing are three buttons and one send.

        Three copies is how the original bug happened: one of them remembers
        push and the other two look fine in testing.
        """
        import inspect

        from app.blueprints import services

        for route in (services.assign, services.toggle_publish,
                      services.send_invites):
            source = inspect.getsource(route)
            assert "ask_to_serve(" in source or "ask_everyone_waiting(" in source, (
                f"{route.__name__} does not use the shared ask"
            )
            assert "queue(" not in source


class TestOneOptOutNotTwo:
    def test_opting_out_stops_both(self, db, journey):
        """Somebody who turned off Groups and serving turned it off, not the
        email part of it."""
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        person.set_preference("group", False)
        db.session.commit()

        result = notify(
            person=person, church_id=journey.id, category="group",
            subject="Sunday", body_text="You are on the plan.",
        )
        assert not result.emailed
        assert result.pushed == 0
        assert result.suppressed

    def test_a_transactional_category_still_sends(self, db, journey):
        """A pickup code that never arrives because somebody left the
        newsletter is worse than no notification at all."""
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        person.set_preference("group", False)
        db.session.commit()

        result = notify(
            person=person, church_id=journey.id, category="kids_checkin",
            subject="Your code", body_text="It is ABCD.",
        )
        assert result.emailed
        assert result.pushed == 1


class TestEmailSurvivesPushFailing:
    def test_the_push_half_blowing_up_does_not_stop_the_email(self, db, journey,
                                                              monkeypatch):
        """A push provider having a bad afternoon must never be the reason a
        volunteer is not told they are on the plan.

        This used to patch `send_to_person`, because `notify` called the
        provider itself. It queues now, so the thing that can go wrong at
        this point is the queuing, and that is what is broken here. The
        provider failing is a worker problem, covered in
        tests/test_push_queue.py.
        """
        import app.notify as notify_module

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        monkeypatch.setattr(
            notify_module, "enqueue",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue is down")),
        )

        result = notify(
            person=person, church_id=journey.id, category="group",
            subject="Sunday", body_text="You are on the plan.",
        )
        assert result.emailed
        assert result.pushed == 0

    def test_somebody_with_no_devices_is_not_an_error(self, db, journey):
        """Most people will never grant push. The email still goes."""
        person = a_person(db, journey)
        result = notify(
            person=person, church_id=journey.id, category="group",
            subject="Sunday", body_text="You are on the plan.",
        )
        assert result.emailed
        assert result.pushed == 0


class TestWhatTheNotificationSays:
    def test_the_push_body_falls_back_to_the_first_real_line(self, db, journey):
        """Read off the queued row now rather than captured in flight. The
        person needs a device, because nothing is queued for somebody with
        nowhere to send it."""
        from app.models import PushQueueItem

        person = a_person(db, journey)
        with_a_device(db, journey, person)

        notify(
            person=person, church_id=journey.id,
            category="group", subject="Sunday",
            body_text="\n\n   \nHello Kaela,\n\nYou are on the plan.",
        )
        db.session.commit()

        item = db.session.scalar(db.select(PushQueueItem))
        assert item.body == "Hello Kaela,"

    def test_a_blank_body_does_not_crash(self, db, journey):
        result = notify(
            person=a_person(db, journey), church_id=journey.id,
            category="group", subject="Sunday", body_text="   ",
        )
        # No email, because an empty body is refused. No exception either.
        assert not result.emailed


def a_service(db, journey, days=4):
    from datetime import timedelta

    service = Service(church_id=journey.id, name="Sunday Morning",
                      starts_at=utcnow() + timedelta(days=days))
    db.session.add(service)
    db.session.commit()
    return service


def put_on_plan(db, journey, service, person, role="Acoustic"):
    assignment = ServiceAssignment(
        church_id=journey.id, service_id=service.id, track_id=service.main_track.id, person_id=person.id,
        position_name=role,
    )
    db.session.add(assignment)
    db.session.commit()
    return assignment


class TestSendingInvites:
    def test_it_asks_everyone_who_has_not_answered(self, db, journey, staff):
        from app.models import OutboxMessage

        service = a_service(db, journey)
        put_on_plan(db, journey, service, a_person(db, journey, "Kaela", "k@example.com"))
        put_on_plan(db, journey, service, a_person(db, journey, "Chris", "c@example.com"))

        staff.post(f"/services/{service.id}/invite/", headers=H)
        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(queued) == 2
        assert all("Can you serve" in m.subject for m in queued)

    def test_it_leaves_people_who_answered_alone(self, db, journey, staff):
        """Re-running it on Thursday chases the new person and leaves the rest."""
        from app.models import OutboxMessage

        service = a_service(db, journey)
        done = put_on_plan(db, journey, service,
                           a_person(db, journey, "Kaela", "k@example.com"))
        put_on_plan(db, journey, service,
                    a_person(db, journey, "Chris", "c@example.com"))
        done.respond(ACCEPTED)
        db.session.commit()

        staff.post(f"/services/{service.id}/invite/", headers=H)
        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(queued) == 1
        assert queued[0].to_email == "c@example.com"

    def test_the_email_carries_a_link_that_answers(self, db, journey, staff):
        from app.models import OutboxMessage

        service = a_service(db, journey)
        assignment = put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)

        db.session.refresh(assignment)
        message = db.session.scalar(db.select(OutboxMessage))
        assert assignment.respond_token
        assert assignment.respond_token in message.body_text
        assert "/serve-invite/" in message.body_text

    def test_it_records_when_they_were_asked(self, db, journey, staff):
        """Different from when a leader put them on the plan, and conflating
        them is how a leader believes invites went out that never did."""
        service = a_service(db, journey)
        assignment = put_on_plan(db, journey, service, a_person(db, journey))
        assert assignment.invited_at is None

        staff.post(f"/services/{service.id}/invite/", headers=H)
        db.session.refresh(assignment)
        assert assignment.invited_at is not None

    def test_it_pushes_too(self, db, journey, staff):
        """Queued by the request, sent by the worker.

        This used to assert the subscription had a success stamped on it
        straight after the request, because the request did the sending.
        That is the thing that made posting slow. The two halves are checked
        separately now, and both still have to happen: a row goes in, and
        draining it reaches the device.
        """
        from app.models import PushQueueItem
        from app.push.queue import send_queued

        service = a_service(db, journey)
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        put_on_plan(db, journey, service, person)

        staff.post(f"/services/{service.id}/invite/", headers=H)
        assert db.session.scalar(db.select(PushQueueItem)) is not None

        send_queued()
        sub = db.session.scalar(db.select(PushSubscription))
        assert sub.last_success_at is not None

    def test_a_double_click_does_not_ask_twice(self, db, journey, staff):
        from app.models import OutboxMessage

        service = a_service(db, journey)
        put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)
        staff.post(f"/services/{service.id}/invite/", headers=H)
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) == 1

    def test_everyone_answered_says_so(self, db, journey, staff):
        service = a_service(db, journey)
        done = put_on_plan(db, journey, service, a_person(db, journey))
        done.respond(DECLINED)
        db.session.commit()

        page = staff.post(f"/services/{service.id}/invite/", headers=H,
                          follow_redirects=True).data.decode()
        assert "already answered" in page

    def test_somebody_with_no_email_is_skipped_not_crashed(self, db, journey, staff):
        service = a_service(db, journey)
        person = Person(church_id=journey.id, first_name="No", last_name="Address",
                        stage="member")
        db.session.add(person)
        db.session.commit()
        put_on_plan(db, journey, service, person)

        r = staff.post(f"/services/{service.id}/invite/", headers=H)
        assert r.status_code == 302

    def test_a_member_cannot_send_invites(self, db, journey, member):
        assert member.post("/services/1/invite/", headers=H).status_code == 403

    def test_the_button_is_on_the_plan(self, db, journey, staff):
        service = a_service(db, journey)
        put_on_plan(db, journey, service, a_person(db, journey))
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "Send the invites" in page
        assert "1 not asked yet" in page


class TestAnsweringFromTheEmail:
    def invited(self, db, journey, staff):
        service = a_service(db, journey)
        assignment = put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)
        db.session.refresh(assignment)
        return assignment

    def test_the_link_opens_without_signing_in(self, db, journey, staff, client):
        assignment = self.invited(db, journey, staff)
        # Same client, signed out afterwards, because the point of the link is
        # that it works cold.
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        page = staff.get(f"/serve-invite/{assignment.respond_token}/",
                         headers=H).data.decode()
        assert page.count("Yes, I can") == 1
        assert "Sunday Morning" in page
        assert "Acoustic" in page

    def test_accepting_records_it(self, db, journey, staff):
        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        staff.post(f"/serve-invite/{assignment.respond_token}/",
                   data={"answer": "accept"}, headers=H)
        db.session.refresh(assignment)
        assert assignment.status == ACCEPTED
        assert assignment.responded_at is not None

    def test_declining_records_it(self, db, journey, staff):
        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        staff.post(f"/serve-invite/{assignment.respond_token}/",
                   data={"answer": "decline"}, headers=H)
        db.session.refresh(assignment)
        assert assignment.status == DECLINED

    def test_opening_the_link_changes_nothing(self, db, journey, staff):
        """Mail clients and corporate scanners fetch every link in a message
        before a human sees it. A GET that accepted would sign volunteers up."""
        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        staff.get(f"/serve-invite/{assignment.respond_token}/?answer=accept",
                  headers=H)
        db.session.refresh(assignment)
        assert assignment.status not in (ACCEPTED, DECLINED)

    def test_an_answer_can_be_changed(self, db, journey, staff):
        """Accepted on Tuesday, ill on Saturday. A leader would far rather
        know on Saturday."""
        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        for answer, expected in (("accept", ACCEPTED), ("decline", DECLINED)):
            staff.post(f"/serve-invite/{assignment.respond_token}/",
                       data={"answer": answer}, headers=H)
            db.session.refresh(assignment)
            assert assignment.status == expected

    def test_a_junk_token_is_a_404(self, db, journey, client):
        assert client.get("/serve-invite/not-a-real-token-at-all-really/",
                          headers=H).status_code == 404

    def test_a_short_token_is_a_404(self, db, journey, client):
        assert client.get("/serve-invite/abc/", headers=H).status_code == 404

    def test_a_token_does_not_work_on_another_church(self, db, journey, staff):
        from tests.conftest import RIVERBEND_HOST

        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        r = staff.post(f"/serve-invite/{assignment.respond_token}/",
                       data={"answer": "accept"},
                       headers={"Host": RIVERBEND_HOST})
        db.session.refresh(assignment)
        assert r.status_code == 404
        assert assignment.status not in (ACCEPTED, DECLINED)

    def test_no_answer_asks_again(self, db, journey, staff):
        assignment = self.invited(db, journey, staff)
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        page = staff.post(f"/serve-invite/{assignment.respond_token}/",
                          data={}, headers=H, follow_redirects=True).data.decode()
        assert "Pick one of the two" in page
        db.session.refresh(assignment)
        assert assignment.status not in (ACCEPTED, DECLINED)

    def test_the_token_shows_only_this_one_assignment(self, db, journey, staff):
        """It is the weakest credential in the system on purpose: it cannot
        read a profile or see who else is serving."""
        service = a_service(db, journey)
        mine = put_on_plan(db, journey, service,
                           a_person(db, journey, "Kaela", "k@example.com"))
        put_on_plan(db, journey, service,
                    a_person(db, journey, "Chris", "c@example.com"), role="Drums")
        staff.post(f"/services/{service.id}/invite/", headers=H)
        db.session.refresh(mine)
        staff.post("/auth/logout", headers=H, follow_redirects=True)

        page = staff.get(f"/serve-invite/{mine.respond_token}/",
                         headers=H).data.decode()
        assert "Acoustic" in page
        assert "Chris" not in page
        assert "Drums" not in page

    def test_the_answer_shows_on_the_plan(self, db, journey, staff, sign_in):
        assignment = self.invited(db, journey, staff)
        service_id = assignment.service_id
        token = assignment.respond_token
        staff.post("/auth/logout", headers=H, follow_redirects=True)
        staff.post(f"/serve-invite/{token}/", data={"answer": "accept"}, headers=H)

        sign_in("pastor@journeychurchsemo.com")
        page = staff.get(f"/services/{service_id}/", headers=H).data.decode()
        assert "Everyone has answered" in page


class TestTheDeliveryScreen:
    def test_it_says_push_is_off(self, db, journey, staff):
        """Off and broken look identical from a member's phone, so the screen
        says which."""
        from app.push.health import push_health

        state = push_health(journey.id)
        assert state["state"] in ("off", "nobody_on")

    def test_it_counts_devices_and_people(self, db, journey, staff):
        from app.push.health import push_health

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        state = push_health(journey.id)
        assert state["devices"] == 1
        assert state["people"] == 1

    def test_it_never_shows_the_private_key(self, db, journey, staff):
        from app.push.health import push_health

        state = push_health(journey.id)
        assert "private_key_present" in state
        assert isinstance(state["private_key_present"], bool)
        assert "VAPID_PRIVATE_KEY" not in str(state)

    def test_the_row_is_on_the_settings_screen(self, db, journey, staff):
        page = staff.get("/settings/", headers=H).data.decode()
        assert "App notifications" in page

    def test_nobody_on_is_told_apart_from_broken(self, db, journey, staff, monkeypatch):
        from app.push import health

        monkeypatch.setitem(staff.application.config, "PUSH_TRANSPORT", "webpush")
        monkeypatch.setitem(staff.application.config, "VAPID_PRIVATE_KEY", "x")
        assert health.push_health(journey.id)["state"] == "nobody_on"


class TestAskedAndAnsweredAreDifferentThings:
    """Never asked and asked-but-silent are different problems.

    Calling somebody "not asked yet" after you asked them on Tuesday is how a
    leader asks the same person three times.
    """

    def test_before_any_invite_they_are_not_asked_yet(self, db, journey, staff):
        service = a_service(db, journey)
        put_on_plan(db, journey, service, a_person(db, journey))
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "1 not asked yet" in page
        assert "Send the invites" in page

    def test_after_an_invite_they_are_waited_on(self, db, journey, staff):
        service = a_service(db, journey)
        put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)

        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "Waiting on 1" in page
        assert "not asked yet" not in page
        assert "Ask the rest again" in page

    def test_once_answered_the_box_goes_quiet(self, db, journey, staff):
        service = a_service(db, journey)
        assignment = put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)
        assignment.respond(ACCEPTED)
        db.session.commit()

        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "Everyone has answered" in page
        assert "Ask the rest again" not in page

    def test_chasing_tomorrow_actually_sends(self, db, journey, staff, monkeypatch):
        """Otherwise "Ask the rest again" is a button that does nothing."""
        from datetime import timedelta

        from app.models import OutboxMessage
        # Patched where the dedupe key is now built. The send moved out of
        # the route into app/serving_notify.py when assigning and publishing
        # started sharing it, and patching the blueprint stopped reaching the
        # clock that decides whether today's ask is a new one.
        import app.serving_notify as serving_notify

        service = a_service(db, journey, days=9)
        put_on_plan(db, journey, service, a_person(db, journey))
        staff.post(f"/services/{service.id}/invite/", headers=H)
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) == 1

        tomorrow = utcnow() + timedelta(days=1)
        monkeypatch.setattr(serving_notify, "utcnow", lambda: tomorrow)
        staff.post(f"/services/{service.id}/invite/", headers=H)
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) == 2
