"""Adding one person from the roster.

The roster had only ever been filled by a spreadsheet import, by somebody
signing themselves up, or from a terminal. A leader holding a connection card
on a Sunday had no way in at all.
"""

import pytest

from app.models import AuditEvent, Church, OutboxMessage, Person, SequenceEnrollment, User
from tests.conftest import JOURNEY_HOST


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


def add(client, **extra):
    data = {"first_name": "Nina", "last_name": "Ibarra", "stage": "visitor"}
    data.update(extra)
    return client.post("/people/add/", data=data, headers={"Host": JOURNEY_HOST})


class TestAddingSomebody:
    def test_it_lands_on_the_roster(self, db, journey, staff):
        add(staff)
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        assert person is not None
        assert person.stage == "visitor"

    def test_it_records_the_details(self, db, journey, staff):
        add(staff, email="Nina@Example.com", phone="573-555-4085")
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        assert person.email == "nina@example.com"
        assert person.phone == "573-555-4085"

    def test_a_first_name_is_the_only_thing_required(self, db, journey, staff):
        add(staff, last_name="", email="", phone="")
        assert db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        ) is not None

    def test_no_first_name_is_refused(self, db, journey, staff):
        r = add(staff, first_name="  ", follow_redirects=True) if False else staff.post(
            "/people/add/",
            data={"first_name": "  ", "stage": "visitor"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"first name is the one thing" in r.data
        assert db.session.scalars(db.select(Person)).all() == []

    def test_an_unknown_stage_is_refused(self, db, journey, staff):
        r = add(staff, stage="archbishop")
        assert r.status_code == 400

    def test_they_are_approved_on_arrival(self, db, journey, staff):
        """Entered by a human with roster access, so there is nobody to
        approve."""
        add(staff)
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        assert person.is_approved
        assert not person.is_waiting_for_approval
        assert not person.self_registered

    def test_it_lands_on_their_timeline(self, db, journey, staff):
        from app.models import PersonEvent

        add(staff)
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        events = db.session.scalars(
            PersonEvent.for_person(journey.id, person.id)
        ).all()
        assert any("Added by" in e.summary for e in events)

    def test_the_welcome_sequence_starts(self, db, journey, staff):
        add(staff, stage="visitor")
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        enrollments = db.session.scalars(
            SequenceEnrollment.for_person(journey.id, person.id)
        ).all()
        assert enrollments

    def test_it_opens_their_record(self, db, journey, staff):
        r = add(staff)
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        assert r.headers["Location"].endswith(f"/people/{person.id}/")

    def test_a_leader_can_add_people(self, db, journey, leader):
        add(leader)
        assert db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        ) is not None

    def test_a_member_cannot(self, db, journey, member):
        r = add(member)
        assert r.status_code == 403
        assert db.session.scalars(db.select(Person)).all() == []


class TestGivingThemALoginAtTheSameTime:
    def test_staff_can(self, db, journey, staff):
        add(staff, email="nina@example.com", with_login="1", role="leader")
        user = User.by_email(journey.id, "nina@example.com")
        assert user is not None
        assert user.role == "leader"

    def test_the_account_is_linked_to_the_record(self, db, journey, staff):
        add(staff, email="nina@example.com", with_login="1")
        person = db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        )
        assert User.by_email(journey.id, "nina@example.com").person_id == person.id

    def test_they_are_emailed_a_link_to_set_a_password(self, db, journey, staff):
        """Staff never type one."""
        add(staff, email="nina@example.com", with_login="1")
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        assert message.to_email == "nina@example.com"
        assert "/auth/reset/" in message.body_text

    def test_no_email_means_no_account_and_it_says_so(self, db, journey, staff):
        r = staff.post(
            "/people/add/",
            data={"first_name": "Nina", "stage": "visitor", "with_login": "1"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"account needs an email address" in r.data
        assert db.session.scalars(db.select(Person)).all() != []

    def test_an_address_that_already_signs_in_is_reported(self, db, journey, staff):
        add(staff, email="pastor@journeychurchsemo.com", with_login="1")
        r = staff.post(
            "/people/add/",
            data={"first_name": "Someone", "email": "pastor@journeychurchsemo.com",
                  "stage": "visitor", "with_login": "1"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"already has an account" in r.data
        # The person is still added: the roster and the login are separate
        # things and failing one should not discard the other.
        assert db.session.scalar(
            db.select(Person).where(Person.first_name == "Someone")
        ) is not None

    def test_a_leader_cannot_hand_out_a_login(self, db, journey, leader):
        """Adding people is one level of trust. Deciding who can sign in is
        another."""
        add(leader, email="nina@example.com", with_login="1", role="staff")
        assert User.by_email(journey.id, "nina@example.com") is None
        assert db.session.scalar(
            db.select(Person).where(Person.first_name == "Nina")
        ) is not None

    def test_it_is_audited(self, db, journey, staff):
        add(staff, email="nina@example.com", with_login="1")
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("account" in e.summary for e in events)


class TestTheButton:
    def test_the_roster_offers_it(self, db, journey, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Add someone" in r.data
        assert b'action="/people/add/"' in r.data

    def test_a_leader_sees_no_login_checkbox(self, db, journey, leader):
        r = leader.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Add someone" in r.data
        assert b'name="with_login"' not in r.data

    def test_staff_see_it(self, db, journey, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b'name="with_login"' in r.data
