"""Telling staff about something, on both channels.

Four screens grew their own copy of this and three of them got the push half
wrong, so the tests that matter most here are the ones about the branch that
kept being missed: a staff account with a roster record is a person, and a
person can be pushed to.

Two of the copies also forgot that a kiosk account holds staff role. The iPad
in the lobby is signed in as staff because the check-in screens need it to
be, so "every active staff account" includes a shared device facing a room
full of families unless somebody remembers to exclude it.
"""

from __future__ import annotations

import pytest

from app.alerts import staff_users, tell_staff
from app.models import Church, OutboxMessage, Person, PushSubscription, User
from app.models.base import utcnow


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def pastor(db, journey):
    return db.session.scalar(
        db.select(User).where(
            User.church_id == journey.id,
            User.email == "pastor@journeychurchsemo.com",
        )
    )


def a_person(db, journey, first, *, email=None):
    person = Person(church_id=journey.id, first_name=first, last_name="Reed",
                    stage="member", email=email, approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


def a_device(db, journey, user):
    """A registered push device, so a send has something to reach.

    Through `register` rather than the constructor: it derives the endpoint
    hash, which the column requires.
    """
    subscription = PushSubscription.register(
        journey.id, user, f"https://push.test/{user.id}",
        "k" * 20, "a" * 16, label="iPhone",
    )
    db.session.commit()
    return subscription


def alerts_for(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.category == "pastoral",
        )
    ).all()


def send(journey, **kwargs):
    defaults = dict(
        category="pastoral",
        subject="Somebody asked for support",
        body_text="Open the app.",
        key="support:1",
    )
    defaults.update(kwargs)
    return tell_staff(journey, **defaults)


class TestTheNamedAddressBranch:
    """A care team is addresses a church typed in, not accounts."""

    def test_each_named_address_is_emailed(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, named=["care@journey.test", "deb@journey.test"])
        db.session.commit()

        assert told.reached == 2
        assert {m.to_email for m in alerts_for(db, journey)} == {
            "care@journey.test", "deb@journey.test"
        }

    def test_nothing_is_pushed_to_an_address(self, app, db, journey):
        """There is no person behind a typed-in address, so there is nobody to
        push to. This is a property of the address, not a decision."""
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, named=["care@journey.test"])
        assert told.pushed == 0

    def test_a_named_list_does_not_also_tell_staff(self, app, db, journey, pastor):
        """A church that named a care team chose who hears about this. Telling
        staff as well would silently undo that choice."""
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey, named=["care@journey.test"])
        db.session.commit()

        assert pastor.email not in {m.to_email for m in alerts_for(db, journey)}

    def test_a_blank_entry_is_skipped_not_counted(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, named=["care@journey.test", "", None])
        assert told.reached == 1


class TestTheStaffAccountBranch:
    """The branch three hand-rolled copies got wrong."""

    def test_an_empty_named_list_falls_back_to_staff(self, app, db, journey, pastor):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, named=[])
        db.session.commit()

        assert told.reached >= 1
        assert pastor.email in {m.to_email for m in alerts_for(db, journey)}

    def test_a_staff_member_with_a_record_and_a_device_is_pushed(
        self, app, db, journey, pastor
    ):
        """The whole point. This is what was missing for pastoral requests,
        chat reports, and service plans."""
        person = a_person(db, journey, "Reed", email="pastor@journeychurchsemo.com")
        pastor.person_id = person.id
        db.session.commit()
        a_device(db, journey, pastor)

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, push_title="Support", push_body="Tap.")
        db.session.commit()

        assert told.pushed >= 1, "a staff member with a device was not pushed"

    def test_a_staff_member_with_no_record_still_gets_the_email(
        self, app, db, journey, pastor
    ):
        """No roster record means nothing to push to and no preferences to
        read. The login's own address is the route that remains."""
        assert pastor.person_id is None
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey)
        db.session.commit()

        assert told.emailed >= 1
        assert pastor.email in {m.to_email for m in alerts_for(db, journey)}

    def test_a_record_with_no_address_falls_back_to_the_login(
        self, app, db, journey, pastor
    ):
        """A staff account is created by email; linking it to a roster record
        is a separate step. A record imported without an address used to mean
        the email reached their login, and routing the alert through the
        person must not quietly lose that."""
        person = a_person(db, journey, "Reed", email=None)
        pastor.person_id = person.id
        db.session.commit()

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey)
        db.session.commit()

        assert pastor.email in {m.to_email for m in alerts_for(db, journey)}
        assert told.emailed >= 1

    def test_the_fallback_still_only_sends_once(self, app, db, journey, pastor):
        """A retried request must not produce two emails. The failed first
        attempt consumes no dedupe key, so the fallback's key is the one that
        has to hold."""
        person = a_person(db, journey, "Reed", email=None)
        pastor.person_id = person.id
        db.session.commit()

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
            db.session.commit()
            send(journey)
            db.session.commit()

        assert len(alerts_for(db, journey)) == 1

    def test_the_fallback_does_not_fire_when_the_record_has_an_address(
        self, app, db, journey, pastor
    ):
        """Otherwise a duplicate alert, which reports no email sent because it
        was already queued, would send a second copy to the login."""
        person = a_person(db, journey, "Reed", email="reed-record@journey.test")
        pastor.person_id = person.id
        db.session.commit()

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
            db.session.commit()
            send(journey)
            db.session.commit()

        addresses = [m.to_email for m in alerts_for(db, journey)]
        assert addresses == ["reed-record@journey.test"]

    def test_an_inactive_staff_account_is_not_told(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
        db.session.commit()

        assert "gone@journeychurchsemo.com" not in {
            m.to_email for m in alerts_for(db, journey)
        }

    def test_a_leader_is_not_told(self, app, db, journey):
        """Leaders run the check-in desk. A pastoral request is not their
        work and a reported message is not theirs to decide."""
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
        db.session.commit()

        assert "leader@journeychurchsemo.com" not in {
            m.to_email for m in alerts_for(db, journey)
        }

    def test_a_staff_member_with_a_record_but_no_address_is_still_reached(
        self, app, db, journey, pastor
    ):
        """A person with no email on their roster record can still be pushed,
        and `reached` has to count them or a flash message lies about it."""
        person = a_person(db, journey, "Reed", email=None)
        pastor.person_id = person.id
        db.session.commit()
        a_device(db, journey, pastor)

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, push_title="Support", push_body="Tap.")
        db.session.commit()

        assert told.pushed >= 1
        assert told.reached >= 1, (
            "somebody reached by push alone was counted as not reached"
        )


class TestTheKioskIsNotAPerson:
    """The lobby iPad holds staff role because check-in needs it to."""

    @pytest.fixture
    def kiosk(self, db, journey):
        user = User(church_id=journey.id, email="kiosk@journeychurchsemo.com",
                    name="Lobby iPad", role="staff", is_active_account=True,
                    is_kiosk=True)
        user.set_password("kiosk-device-password")
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.commit()
        return user

    def test_a_kiosk_account_is_not_in_the_staff_list(self, db, journey, kiosk):
        assert kiosk.id not in {u.id for u in staff_users(journey.id)}

    def test_a_pastoral_request_is_not_emailed_to_the_lobby_ipad(
        self, app, db, journey, kiosk
    ):
        """A shared device several volunteers use is not somewhere to send a
        church member's request for help."""
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
        db.session.commit()

        assert kiosk.email not in {m.to_email for m in alerts_for(db, journey)}

    def test_a_kiosk_device_is_not_pushed(self, app, db, journey, kiosk):
        """The worst version of this bug: somebody's worst week on a screen
        facing the lobby."""
        person = a_person(db, journey, "Lobby", email=None)
        kiosk.person_id = person.id
        db.session.commit()
        a_device(db, journey, kiosk)

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, push_title="Support", push_body="Tap.")
        db.session.commit()

        assert told.pushed == 0


class TestTenancy:
    def test_another_church_s_staff_are_not_told(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey)
        db.session.commit()

        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        leaked = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.church_id == riverbend.id)
        ).all()
        assert not leaked


class TestItNeverTakesDownTheForm:
    """Every caller is somebody's submission. An error page that says the
    form failed, when the form worked and only the alert did not, is worse
    than an alert nobody got."""

    def test_an_unknown_category_does_not_raise(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = tell_staff(journey, category="not-a-category", subject="X",
                              body_text="Y", key="k", named=["a@b.test"])
        assert told.reached == 0

    def test_an_empty_body_does_not_raise(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, body_text="   ", named=["a@b.test"])
        assert told.reached == 0

    def test_a_push_transport_failure_does_not_lose_the_email(
        self, app, db, journey, pastor, monkeypatch
    ):
        person = a_person(db, journey, "Reed", email="pastor@journeychurchsemo.com")
        pastor.person_id = person.id
        db.session.commit()

        def explode(*args, **kwargs):
            raise RuntimeError("the push provider is having an afternoon")

        # Was `app.notify.send_to_person`, back when the request sent the
        # push itself. It queues now, so the failure worth surviving at this
        # point is the queuing.
        monkeypatch.setattr("app.notify.enqueue", explode)

        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            told = send(journey, push_title="Support", push_body="Tap.")
        db.session.commit()

        assert told.emailed >= 1, "a push failure swallowed the email"


class TestDedupe:
    def test_the_same_alert_twice_tells_somebody_once(self, app, db, journey):
        """A double-submitted form, or a retried request."""
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey, named=["care@journey.test"])
            db.session.commit()
            send(journey, named=["care@journey.test"])
            db.session.commit()

        assert len(alerts_for(db, journey)) == 1

    def test_a_different_alert_still_goes(self, app, db, journey):
        with app.test_request_context(headers={"Host": "journey.dos.test"}):
            send(journey, key="support:1", named=["care@journey.test"])
            db.session.commit()
            send(journey, key="support:2", named=["care@journey.test"])
            db.session.commit()

        assert len(alerts_for(db, journey)) == 2
