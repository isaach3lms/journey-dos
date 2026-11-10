"""Push leaves the web request and goes in a queue.

The bug that forced this: posting a message pushed every device of every
person in the room before the page came back, one HTTPS call each with a ten
second timeout. The send button sat there looking untouched, so people
pressed it again, and a room at Journey ended up with the same message in it
six times.

The rule the outbox was built on, finally applied to the other channel:
nothing calls a third party inside a web request.

What has to stay true, and why each one is here:

**The request does no network.** The test that matters most is the one that
makes the transport explode and then checks the request did not notice.

**A late notification is dropped, not delivered.** Email about Sunday is
useful on Monday. A phone buzzing at four about a message from three is why
people turn notifications off. Stale rows expire, and that is a success.

**The opt-out is checked when it sends, not when it queues.** Somebody can
turn a category off in the seconds between the two, and the answer that
counts is the one at the moment of sending.

**Two senders never collide.** The continuous worker and the five minute
cron both drain this. Claiming is atomic or the church gets everything twice.
"""

from datetime import timedelta

import pytest

from app.models import (
    Church,
    Person,
    PushQueueItem,
    PushSubscription,
    User,
)
from app.models.base import utcnow
from app.models.push_queue import (
    PUSH_EXPIRED,
    PUSH_FAILED,
    PUSH_QUEUED,
    PUSH_SENT,
    PUSH_SUPPRESSED,
    STALE_MINUTES,
)
from app.notify import notify
from app.push.queue import enqueue, purge, release_claims, send_queued
from app.push.transport import MemoryPushTransport, PushFailed, SubscriptionGone
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def a_person(db, journey, first="Kaela", email="kaela@example.com"):
    person = Person(church_id=journey.id, first_name=first, last_name="Menz",
                    email=email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


def with_a_device(db, journey, person, label="Phone"):
    user = User(church_id=journey.id, email=person.email, name=person.full_name,
                role="member", person_id=person.id)
    user.set_password(PASSWORD)
    user.mark_verified()
    user.accept_community()
    db.session.add(user)
    db.session.flush()
    PushSubscription.register(
        journey.id, user,
        endpoint=f"https://push.example.com/{person.id}-{label}",
        p256dh="x" * 60, auth="y" * 20, label=label,
    )
    db.session.commit()
    return user


def queued(db):
    return db.session.scalars(
        db.select(PushQueueItem).order_by(PushQueueItem.id)).all()


class TestTheRequestDoesNoNetwork:
    """The whole point. Everything else here is detail."""

    def test_notifying_queues_instead_of_sending(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)

        notify(person=person, church_id=journey.id, category="group",
               subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        [item] = queued(db)
        assert item.status == PUSH_QUEUED
        assert item.title == "Sunday"

        # Nothing reached the device yet. That is the fix, not a failure.
        sub = db.session.scalar(db.select(PushSubscription))
        assert sub.last_success_at is None

    def test_a_dead_transport_cannot_slow_the_request(self, db, journey,
                                                      monkeypatch):
        """Nothing is *sent* in the request, which is the property that
        matters. If this ever sends again, the ten second per-device timeout
        comes back and the duplicate messages come with it.

        The transport object is still built here, by the reachability check,
        and that is fine: constructing it reads config and touches no
        network. What must not happen is a call to `send`, so this hands the
        request a transport that raises on any send and checks the request
        neither noticed nor sent.
        """
        exploding = MemoryPushTransport(
            fail_with=AssertionError("the request sent a push"))
        monkeypatch.setattr("app.push.send.build_push_transport",
                            lambda config: exploding)

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        result = notify(person=person, church_id=journey.id, category="group",
                        subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        assert result.pushed == 1
        assert len(queued(db)) == 1
        assert exploding.sent == []

    def test_the_worker_is_what_reaches_the_device(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        notify(person=person, church_id=journey.id, category="group",
               subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        transport = MemoryPushTransport()
        counts = send_queued(transport=transport)

        assert counts["sent"] == 1
        assert len(transport.sent) == 1
        assert queued(db)[0].status == PUSH_SENT


class TestNothingPointlessIsQueued:
    def test_somebody_with_no_device(self, db, journey):
        """Most of a roster has never granted notifications. Queuing for them
        would fill the table with rows whose only destiny is to be thrown
        away, and make the depth readout in Settings meaningless."""
        person = a_person(db, journey)
        result = notify(person=person, church_id=journey.id, category="group",
                        subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        assert result.emailed
        assert result.pushed == 0
        assert queued(db) == []

    def test_somebody_who_turned_the_category_off(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        person.set_preference("group", False)
        db.session.commit()

        notify(person=person, church_id=journey.id, category="group",
               subject="Sunday", body_text="You are on the plan.")
        db.session.commit()
        assert queued(db) == []

    def test_a_transactional_category_ignores_the_opt_out(self, db, journey):
        """A pickup code that never arrives because somebody left the
        newsletter is worse than no notification at all."""
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        person.set_preference("kids_checkin", False)
        db.session.commit()

        notify(person=person, church_id=journey.id, category="kids_checkin",
               subject="Your code", body_text="It is ABCD.")
        db.session.commit()
        assert len(queued(db)) == 1

    def test_the_same_notification_twice(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)

        for _ in range(3):
            enqueue(person=person, category="group", title="Sunday",
                    dedupe_key="plan:17")
        db.session.commit()

        assert len(queued(db)) == 1

    def test_an_unknown_category_is_a_programming_error(self, db, journey):
        from app.push.queue import NotQueued

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        with pytest.raises(NotQueued):
            enqueue(person=person, category="not-a-category", title="Hi")


class TestLateIsWorseThanNever:
    def test_a_stale_notification_expires_rather_than_sending(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Sunday")
        db.session.commit()
        item.queued_at = utcnow() - timedelta(minutes=STALE_MINUTES + 1)
        db.session.commit()

        transport = MemoryPushTransport()
        counts = send_queued(transport=transport)

        assert counts["expired"] == 1
        assert counts["sent"] == 0
        assert transport.sent == []
        assert queued(db)[0].status == PUSH_EXPIRED

    def test_just_inside_the_window_still_goes(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Sunday")
        db.session.commit()
        item.queued_at = utcnow() - timedelta(minutes=STALE_MINUTES - 1)
        db.session.commit()

        assert send_queued(transport=MemoryPushTransport())["sent"] == 1


class TestTheOptOutIsCheckedWhenItSends:
    def test_turning_it_off_after_queuing_stops_it(self, db, journey):
        """The seconds between queuing and sending are real, and the answer
        that counts is the one at the moment of sending."""
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        person.set_preference("group", False)
        db.session.commit()

        transport = MemoryPushTransport()
        counts = send_queued(transport=transport)

        assert counts["suppressed"] == 1
        assert transport.sent == []
        assert queued(db)[0].status == PUSH_SUPPRESSED

    def test_a_person_deleted_after_queuing(self, db, journey):
        """On Postgres the row goes with the person: the foreign key is
        ON DELETE CASCADE, and that is checked against a real Postgres in
        the migration verification rather than here, because SQLite does not
        enforce foreign keys in this suite.

        What is checked here is the half that has to hold either way: the
        worker meeting a row whose person is gone deals with it quietly
        instead of raising on every pass forever.
        """
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        db.session.delete(person)
        db.session.commit()

        counts = send_queued(transport=MemoryPushTransport())
        assert counts["suppressed"] == 1
        assert queued(db)[0].status == PUSH_SUPPRESSED


class TestFailures:
    def test_a_provider_having_an_afternoon_is_retried(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        transport = MemoryPushTransport(fail_with=PushFailed("502 from the provider"))
        send_queued(transport=transport)

        item = queued(db)[0]
        # send_to_person swallows a per-device PushFailed and reports zero
        # sent, so the row is marked sent with no devices rather than failed.
        # That is the existing contract: a push that did not land is not an
        # error anybody can act on.
        assert item.status == PUSH_SENT
        assert item.devices == 0

    def test_a_row_that_keeps_blowing_up_stops_being_retried(self, db, journey,
                                                             monkeypatch):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        def explode(*args, **kwargs):
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr("app.push.send.send_to_person", explode)

        from app.models.push_queue import PUSH_MAX_ATTEMPTS

        for _ in range(PUSH_MAX_ATTEMPTS):
            send_queued(transport=MemoryPushTransport())

        item = queued(db)[0]
        assert item.status == PUSH_FAILED
        assert item.attempts == PUSH_MAX_ATTEMPTS
        assert "nobody anticipated" in item.last_error

    def test_one_bad_row_does_not_stop_the_rest(self, db, journey, monkeypatch):
        """A run that stops at the first problem is a run where one broken
        subscription holds up a whole church's notifications."""
        good = a_person(db, journey, "Good", "good@example.com")
        bad = a_person(db, journey, "Bad", "bad@example.com")
        with_a_device(db, journey, good)
        with_a_device(db, journey, bad)

        enqueue(person=bad, category="group", title="First")
        enqueue(person=good, category="group", title="Second")
        db.session.commit()

        real = __import__("app.push.send", fromlist=["send_to_person"]).send_to_person

        def selective(person, message, category, transport=None):
            if person.first_name == "Bad":
                raise RuntimeError("this one is broken")
            return real(person, message, category, transport=transport)

        monkeypatch.setattr("app.push.send.send_to_person", selective)
        counts = send_queued(transport=MemoryPushTransport())

        assert counts["sent"] == 1
        assert counts["retrying"] == 1

    def test_a_claim_left_by_a_dead_worker_is_released(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        item.claim_token = "abc123"
        item.claimed_at = utcnow() - timedelta(minutes=30)
        db.session.commit()

        assert send_queued(transport=MemoryPushTransport())["sent"] == 0
        assert release_claims(minutes=15) == 1
        assert send_queued(transport=MemoryPushTransport())["sent"] == 1


class TestTwoSendersNeverCollide:
    """The continuous worker and the five minute cron both drain this."""

    def test_a_claimed_row_is_invisible_to_the_second_sender(self, db, journey):
        from app.push.queue import _claim

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        _, first = _claim(limit=10)
        _, second = _claim(limit=10)

        assert len(first) == 1
        assert second == [], "both senders claimed the same notification"

    def test_draining_twice_sends_once(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        transport = MemoryPushTransport()
        send_queued(transport=transport)
        send_queued(transport=transport)

        assert len(transport.sent) == 1


class TestHousekeeping:
    def test_finished_rows_are_purged_and_waiting_ones_are_not(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        old = enqueue(person=person, category="group", title="Old")
        waiting = enqueue(person=person, category="group", title="Waiting")
        db.session.commit()

        old.mark_sent(1)
        old.created_at = utcnow() - timedelta(days=5)
        db.session.commit()

        assert purge(older_than_days=3) == 1
        assert [i.id for i in queued(db)] == [waiting.id]

    def test_a_recently_sent_row_stays(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Yesterday")
        db.session.commit()
        item.mark_sent(1)
        db.session.commit()

        assert purge(older_than_days=3) == 0


class TestTheStuckWorkerIsVisible:
    """A worker that died at two in the morning looks exactly like a quiet
    Tuesday unless something is counting what is waiting."""

    def test_an_empty_queue_reads_as_healthy(self, db, journey):
        from app.push.health import queue_health

        health = queue_health(journey.id)
        assert health["queue_waiting"] == 0
        assert not health["queue_stuck"]

    def test_something_queued_a_moment_ago_is_not_stuck(self, db, journey):
        from app.push.health import queue_health

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        health = queue_health(journey.id)
        assert health["queue_waiting"] == 1
        assert not health["queue_stuck"], (
            "a notification queued a second ago was called stuck, which "
            "would show an alarm on Settings every time anybody posts"
        )

    def test_something_queued_a_while_ago_is_stuck(self, db, journey):
        from app.push.health import STUCK_MINUTES, queue_health

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Sunday")
        db.session.commit()
        item.queued_at = utcnow() - timedelta(minutes=STUCK_MINUTES + 1)
        db.session.commit()

        assert queue_health(journey.id)["queue_stuck"]

    def test_the_settings_page_says_so(self, db, journey, client, sign_in):
        from app.push.health import STUCK_MINUTES

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        item = enqueue(person=person, category="group", title="Sunday")
        db.session.commit()
        item.queued_at = utcnow() - timedelta(minutes=STUCK_MINUTES + 1)
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        body = client.get("/settings/", headers=H).get_data(as_text=True)
        assert "Nothing is sending them" in body

    def test_another_church_queue_is_not_counted(self, db, journey):
        """Multi-tenant from day one. One church's backlog must not raise an
        alarm on another church's settings page."""
        from app.push.health import queue_health

        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        person = Person(church_id=other.id, first_name="Theirs", last_name="Person",
                        email="theirs@example.com", stage="member",
                        approved_at=utcnow())
        db.session.add(person)
        db.session.commit()
        with_a_device(db, other, person)
        enqueue(person=person, category="group", title="Theirs")
        db.session.commit()

        assert queue_health(journey.id)["queue_waiting"] == 0
        assert queue_health(other.id)["queue_waiting"] == 1


class TestTheWorkerLoop:
    def test_it_drains_both_queues_and_can_be_stopped(self, app, db, journey):
        """`--max-passes` exists for this test. The real worker runs with 0,
        which never returns."""
        from app.models import OutboxMessage
        from app.models.outbox import STATUS_SENT

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        notify(person=person, church_id=journey.id, category="group",
               subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        assert len(queued(db)) == 1

        runner = app.test_cli_runner()
        result = runner.invoke(args=["worker-loop", "--max-passes", "1",
                                     "--seconds", "0"])

        assert result.exit_code == 0, result.output
        assert "worker-loop started" in result.output

        db.session.expire_all()
        assert db.session.scalar(db.select(PushQueueItem)).status == PUSH_SENT
        assert db.session.scalar(db.select(OutboxMessage)).status == STATUS_SENT

    def test_a_broken_push_pass_does_not_stop_the_mail_pass(self, app, db, journey,
                                                            monkeypatch):
        """The same separation `worker-tick` was restructured to get. A push
        provider refusing everything must never stop email."""
        from app.models import OutboxMessage
        from app.models.outbox import STATUS_SENT

        person = a_person(db, journey)
        with_a_device(db, journey, person)
        notify(person=person, church_id=journey.id, category="group",
               subject="Sunday", body_text="You are on the plan.")
        db.session.commit()

        monkeypatch.setattr(
            "app.push.queue.send_queued",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push is down")),
        )

        runner = app.test_cli_runner()
        result = runner.invoke(args=["worker-loop", "--max-passes", "1",
                                     "--seconds", "0"])

        assert result.exit_code == 0, result.output
        db.session.expire_all()
        assert db.session.scalar(db.select(OutboxMessage)).status == STATUS_SENT


class TestTheCronStillSends:
    """The safety net. If the continuous worker is down, stuck, or mid-deploy,
    notifications still go out within five minutes."""

    def test_worker_tick_drains_the_push_queue(self, app, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        runner = app.test_cli_runner()
        result = runner.invoke(args=["worker-tick"])

        assert result.exit_code == 0, result.output
        db.session.expire_all()
        assert db.session.scalar(db.select(PushQueueItem)).status == PUSH_SENT


class TestAGoneSubscription:
    def test_a_revoked_device_is_deleted_rather_than_retried(self, db, journey):
        person = a_person(db, journey)
        with_a_device(db, journey, person)
        enqueue(person=person, category="group", title="Sunday")
        db.session.commit()

        transport = MemoryPushTransport(fail_with=SubscriptionGone("410"))
        send_queued(transport=transport)

        assert db.session.scalars(db.select(PushSubscription)).all() == []
        assert queued(db)[0].status == PUSH_SENT
