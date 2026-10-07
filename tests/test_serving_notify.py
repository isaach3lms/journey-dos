"""Being scheduled to serve.

This notified nobody. A leader added twelve people to a Sunday, the screen
said "asked to play drums", and not one of the twelve had been asked
anything: the only thing that ever sent was a button somebody had to
remember to press.

The tests split three ways, matching the three decisions:

1. **Assigning is asking**, so adding somebody to a published plan sends.
2. **A draft sends nothing**, and publishing releases what the draft built
   up. A leader moving people around on Thursday is not twelve events in
   somebody's week.
3. **Nobody is asked twice.** Assigning and then pressing the button in the
   same minute is one notification, because all three paths share one dedupe
   key. That shared key is the thing most likely to be broken by a later
   change, so it is tested from both directions.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import (
    Church,
    OutboxMessage,
    Person,
    PushSubscription,
    Service,
    ServiceAssignment,
    ServiceItem,
    User,
)
from app.models.base import utcnow
from app.models.service import add_track
from app.serving_notify import ask_everyone_waiting, ask_to_serve, tell_removed
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def volunteer(db, journey):
    person = Person(church_id=journey.id, first_name="Mara", last_name="Quinn",
                    stage="member", email="mara@journey.test",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def service(db, journey):
    """A Sunday with one strand and one item on it.

    The item matters: publishing refuses an empty plan, and half of these
    tests are about publishing.
    """
    made = Service(church_id=journey.id, name="Sunday Morning",
                   starts_at=utcnow() + timedelta(days=4))
    db.session.add(made)
    db.session.flush()
    track = add_track(made)
    db.session.add(ServiceItem(church_id=journey.id, service_id=made.id,
                               track_id=track.id, title="Welcome", position=1))
    db.session.commit()
    return made


def an_assignment(db, journey, service, person, *, invited=False):
    assignment = ServiceAssignment(
        church_id=journey.id, service_id=service.id,
        track_id=service.tracks[0].id,
        person_id=person.id, position_name="Drums", status="invited",
        invited_at=utcnow() if invited else None,
    )
    db.session.add(assignment)
    db.session.commit()
    return assignment


def serving_mail(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.category == "group",
        )
    ).all()


class TestAssigningIsAsking:
    def test_an_ask_reaches_the_person(self, app, db, journey, service, volunteer):
        assignment = an_assignment(db, journey, service, volunteer)
        with app.test_request_context(headers=H):
            assert ask_to_serve(service, assignment, journey)
        db.session.commit()

        assert [m.to_email for m in serving_mail(db, journey)] == ["mara@journey.test"]

    def test_the_ask_records_that_it_went(self, app, db, journey, service, volunteer):
        """`invited_at` is what the plan screen reads to know who to chase."""
        assignment = an_assignment(db, journey, service, volunteer)
        with app.test_request_context(headers=H):
            ask_to_serve(service, assignment, journey)
        db.session.commit()

        assert assignment.invited_at is not None

    def test_it_carries_a_link_that_answers_without_signing_in(
        self, app, db, journey, service, volunteer
    ):
        assignment = an_assignment(db, journey, service, volunteer)
        with app.test_request_context(headers=H):
            ask_to_serve(service, assignment, journey)
        db.session.commit()

        token = assignment.respond_token
        assert token
        assert token in serving_mail(db, journey)[0].body_text

    def test_somebody_with_no_email_is_still_pushed(
        self, app, db, journey, service, volunteer
    ):
        """The gate that used to be here was `if not person.email: continue`,
        which threw away the notification along with the email. On an
        app-first youth team that is most of the team."""
        volunteer.email = None
        user = User(church_id=journey.id, email="mara-login@journey.test",
                    name="Mara Quinn", role="member", is_active_account=True,
                    person_id=volunteer.id)
        user.set_password("a-long-enough-password")
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.commit()
        PushSubscription.register(journey.id, user, "https://push.test/mara",
                                  "k" * 20, "a" * 16, label="iPhone")
        db.session.commit()

        assignment = an_assignment(db, journey, service, volunteer)
        with app.test_request_context(headers=H):
            assert ask_to_serve(service, assignment, journey), (
                "a volunteer with no email address was told nothing"
            )
        db.session.commit()

        assert not serving_mail(db, journey), "there was no address to email"
        assert assignment.invited_at is not None


class TestADraftSendsNothing:
    def test_publishing_asks_everybody_waiting(
        self, app, db, journey, service, volunteer
    ):
        an_assignment(db, journey, service, volunteer)
        service.publish()
        db.session.commit()

        with app.test_request_context(headers=H):
            assert ask_everyone_waiting(service, journey) == 1

    def test_an_unpublished_plan_asks_nobody(
        self, app, db, journey, service, volunteer
    ):
        an_assignment(db, journey, service, volunteer)
        assert not service.is_published

        with app.test_request_context(headers=H):
            assert ask_everyone_waiting(service, journey) == 0
        db.session.commit()
        assert not serving_mail(db, journey)

    def test_somebody_who_already_answered_is_not_asked_again(
        self, app, db, journey, service, volunteer
    ):
        assignment = an_assignment(db, journey, service, volunteer)
        assignment.respond("accepted")
        service.publish()
        db.session.commit()

        with app.test_request_context(headers=H):
            assert ask_everyone_waiting(service, journey) == 0


class TestNobodyIsAskedTwice:
    def test_the_same_slot_twice_in_a_day_sends_once(
        self, app, db, journey, service, volunteer
    ):
        """Assigning and then pressing "Ask the rest again" in the same
        minute. The two paths share one dedupe key, which is the only reason
        this works."""
        assignment = an_assignment(db, journey, service, volunteer)
        with app.test_request_context(headers=H):
            ask_to_serve(service, assignment, journey)
            db.session.commit()
            ask_to_serve(service, assignment, journey)
            db.session.commit()

        assert len(serving_mail(db, journey)) == 1

    def test_two_different_people_both_get_asked(
        self, app, db, journey, service, volunteer
    ):
        """A dedupe key too broad would collapse a whole team into one send."""
        other = Person(church_id=journey.id, first_name="Theo", last_name="Brandt",
                       stage="member", email="theo@journey.test",
                       approved_at=utcnow())
        db.session.add(other)
        db.session.commit()
        first = an_assignment(db, journey, service, volunteer)
        second = an_assignment(db, journey, service, other)

        with app.test_request_context(headers=H):
            ask_to_serve(service, first, journey)
            ask_to_serve(service, second, journey)
        db.session.commit()

        assert len(serving_mail(db, journey)) == 2


class TestBeingTakenOff:
    def test_somebody_who_was_asked_is_told(
        self, app, db, journey, service, volunteer
    ):
        assignment = an_assignment(db, journey, service, volunteer, invited=True)
        with app.test_request_context(headers=H):
            assert tell_removed(service, assignment, journey)
        db.session.commit()

        assert any("off the plan" in m.subject.lower()
                   for m in serving_mail(db, journey))

    def test_somebody_who_was_never_asked_is_not_told(
        self, app, db, journey, service, volunteer
    ):
        """A leader moving people around a draft on Thursday is not an event
        in anybody's week, and a notification for one would teach a team that
        these mean nothing."""
        assignment = an_assignment(db, journey, service, volunteer, invited=False)
        with app.test_request_context(headers=H):
            assert not tell_removed(service, assignment, journey)
        db.session.commit()
        assert not serving_mail(db, journey)


class TestTheRoutes:
    """Through the screens a leader actually uses."""

    def _plan(self, db, journey, leader, service, person, position=None):
        return leader.post(
            f"/services/{service.id}/assignments/",
            data={"person_id": person.id},
            headers=H, follow_redirects=True,
        )

    def test_assigning_to_a_published_plan_asks_them(
        self, app, db, journey, leader, service, volunteer
    ):
        service.publish()
        db.session.commit()

        page = self._plan(db, journey, leader, service, volunteer)
        assert page.status_code == 200
        assert serving_mail(db, journey), (
            "adding somebody to a published plan told them nothing"
        )

    def test_assigning_to_a_draft_says_so_rather_than_claiming_it_asked(
        self, app, db, journey, leader, service, volunteer
    ):
        """The flash message used to say "asked to play drums" for a plan that
        sent nothing, so a leader believed the team knew."""
        page = self._plan(db, journey, leader, service, volunteer)
        body = page.get_data(as_text=True)

        assert not serving_mail(db, journey)
        assert "Publish it to ask them" in body

    def test_publishing_from_the_screen_asks_the_team(
        self, app, db, journey, leader, service, volunteer
    ):
        an_assignment(db, journey, service, volunteer)
        page = leader.post(f"/services/{service.id}/publish/", headers=H,
                           follow_redirects=True)
        assert page.status_code == 200
        assert serving_mail(db, journey)

    def test_unpublishing_asks_nobody(
        self, app, db, journey, leader, service, volunteer
    ):
        an_assignment(db, journey, service, volunteer)
        service.publish()
        db.session.commit()
        before = len(serving_mail(db, journey))

        leader.post(f"/services/{service.id}/publish/", headers=H,
                    follow_redirects=True)
        assert len(serving_mail(db, journey)) == before

    def test_removing_somebody_who_was_asked_tells_them(
        self, app, db, journey, leader, service, volunteer
    ):
        assignment = an_assignment(db, journey, service, volunteer, invited=True)
        page = leader.post(
            f"/services/{service.id}/assignments/{assignment.id}/delete/",
            headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        assert any("off the plan" in m.subject.lower()
                   for m in serving_mail(db, journey))

    def test_sending_the_plan_reaches_somebody_with_no_address_by_push(
        self, app, db, journey, leader, service, volunteer
    ):
        """"Send it" is the Sunday morning message with the running order in
        it, and it was the one that never reached a phone."""
        volunteer.email = None
        user = User(church_id=journey.id, email="mara-login@journey.test",
                    name="Mara Quinn", role="member", is_active_account=True,
                    person_id=volunteer.id)
        user.set_password("a-long-enough-password")
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.commit()
        PushSubscription.register(journey.id, user, "https://push.test/mara",
                                  "k" * 20, "a" * 16, label="iPhone")
        an_assignment(db, journey, service, volunteer)
        db.session.commit()

        page = leader.post(f"/services/{service.id}/send/", headers=H,
                           follow_redirects=True)
        assert page.status_code == 200
        # Nothing to email, so the count the screen reports has to come from
        # the push or it reports zero and a leader thinks it failed.
        assert "1" in page.get_data(as_text=True)
