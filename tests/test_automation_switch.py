"""The switch that stops the system writing to people on its own.

A church that would rather send a note somebody actually typed is making a
decision about how it wants to sound. The welcome series is what it switches
off to do that, and the thing it must be able to trust is that switching off
is reversible: nobody is cancelled, nobody is re-started from the top, and
nothing queues up a month of catch-up when it goes back on.

What is deliberately *not* paused is transactional mail. A password reset is
not the system writing to somebody, it is the system answering them.
"""

from datetime import timedelta

import pytest

from app.automation import enroll_for_stage, run_due
from app.models import Church, Person, SequenceEnrollment
from app.models.base import utcnow
from app.models.sequence import STATUS_ACTIVE
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def visitor(db, journey):
    person = Person(
        church_id=journey.id, first_name="Nina", last_name="Ibarra",
        email="nina@example.com", stage="visitor",
    )
    db.session.add(person)
    db.session.commit()
    return person


def enrol(db, person):
    enrollments = enroll_for_stage(person)
    db.session.commit()
    return enrollments[0] if enrollments else None


class TestTheDefault:
    def test_a_new_church_has_them_on(self, db):
        """On, because a church that set up a welcome series wants it."""
        church = Church(slug="newone", name="New One", palette_key="journey")
        db.session.add(church)
        db.session.commit()
        assert church.automations_on is True

    def test_the_churches_already_here_have_them_on(self, journey):
        """The migration's server default. Switching this feature on must not
        silently stop the emails a church is already sending."""
        assert journey.automations_on is True


class TestNobodyIsEnrolledWhileItIsOff:
    def test_arriving_at_a_stage_starts_nothing(self, db, journey, visitor):
        journey.automations_on = False
        db.session.commit()
        assert enroll_for_stage(visitor) == []

    def test_and_starts_it_again_once_it_is_back_on(self, db, journey, visitor):
        journey.automations_on = False
        db.session.commit()
        enroll_for_stage(visitor)
        journey.automations_on = True
        db.session.commit()
        assert enrol(db, visitor) is not None

    def test_the_other_church_is_unaffected(self, db, journey):
        """Multi-tenant: one church's decision is one church's decision."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        journey.automations_on = False
        db.session.commit()

        person = Person(
            church_id=riverbend.id, first_name="Sam", last_name="Okafor",
            email="sam@example.com", stage="visitor",
        )
        db.session.add(person)
        db.session.commit()

        assert enroll_for_stage(person) != []


class TestARunPausesRatherThanCancels:
    def test_a_due_step_is_not_sent(self, db, journey, visitor):
        enrol(db, visitor)
        journey.automations_on = False
        db.session.commit()

        counts = run_due(church_id=journey.id)
        assert counts["sent"] == 0
        assert counts["paused"] == 1

    def test_the_enrollment_is_left_exactly_as_it_was(self, db, journey, visitor):
        enrollment = enrol(db, visitor)
        was_due, was_step = enrollment.next_due_at, enrollment.step_index
        journey.automations_on = False
        db.session.commit()

        run_due(church_id=journey.id)
        db.session.refresh(enrollment)

        assert enrollment.status == STATUS_ACTIVE
        assert enrollment.step_index == was_step
        assert enrollment.next_due_at == was_due

    def test_turning_it_back_on_resumes_the_same_step(self, db, journey, visitor):
        enrollment = enrol(db, visitor)
        journey.automations_on = False
        db.session.commit()
        run_due(church_id=journey.id)

        journey.automations_on = True
        db.session.commit()
        counts = run_due(church_id=journey.id)

        assert counts["sent"] == 1
        db.session.refresh(enrollment)
        assert enrollment.step_index == 1

    def test_a_long_pause_does_not_empty_itself_into_an_inbox(
        self, db, journey, visitor
    ):
        """The point of pausing rather than cancelling is that it is safe to
        leave off. A month later, the overdue guard means the backlog is
        dropped rather than delivered."""
        from app.sequences import MAX_CATCHUP_DAYS

        enrollment = enrol(db, visitor)
        journey.automations_on = False
        db.session.commit()

        enrollment.next_due_at = utcnow() - timedelta(days=MAX_CATCHUP_DAYS + 5)
        db.session.commit()

        journey.automations_on = True
        db.session.commit()
        counts = run_due(church_id=journey.id)

        assert counts["sent"] == 0
        assert counts["skipped"] == 1

    def test_a_paused_church_does_not_hold_up_another_one(self, db, journey):
        """`run_due` with no church id is the worker's own call. One church
        pausing must not stop the next church's queue."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        for church, name in ((journey, "Nina"), (riverbend, "Sam")):
            person = Person(
                church_id=church.id, first_name=name, last_name="Visitor",
                email=f"{name.lower()}@example.com", stage="visitor",
            )
            db.session.add(person)
            db.session.commit()
            enrol(db, person)

        journey.automations_on = False
        db.session.commit()

        counts = run_due()
        assert counts["paused"] == 1
        assert counts["sent"] == 1


class TestTransactionalMailIsNotPaused:
    def test_a_password_reset_still_goes_out(self, db, journey, client):
        """The switch is about the church writing to people. Somebody asking
        to get back into their account is the system answering them, and
        pausing that would lock people out with no sign of why."""
        from app.models import OutboxMessage

        journey.automations_on = False
        db.session.commit()

        before = db.session.scalars(db.select(OutboxMessage)).all()
        client.post(
            "/auth/forgot",
            data={"email": "member@journeychurchsemo.com"},
            headers=H, follow_redirects=True,
        )
        after = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(after) == len(before) + 1

    def test_a_staff_member_can_still_send_by_hand(self, db, journey, visitor, staff):
        """The whole reason for switching off: sending them yourself."""
        from app.models import OutboxMessage

        journey.automations_on = False
        db.session.commit()

        staff.post(
            f"/people/{visitor.id}/email/",
            data={"category": "account", "subject": "Hello",
                  "message_body": "Thanks for being with us."},
            headers=H, follow_redirects=True,
        )
        queued = db.session.scalars(
            OutboxMessage.for_person(journey.id, visitor.id)
        ).all()
        assert len(queued) == 1


class TestTheSwitchItself:
    def test_staff_can_turn_it_off(self, db, journey, staff):
        staff.post("/settings/automations/", headers=H, follow_redirects=True)
        db.session.refresh(journey)
        assert journey.automations_on is False

    def test_and_on_again(self, db, journey, staff):
        journey.automations_on = False
        db.session.commit()
        staff.post("/settings/automations/", headers=H, follow_redirects=True)
        db.session.refresh(journey)
        assert journey.automations_on is True

    def test_a_leader_cannot(self, db, journey, leader):
        response = leader.post("/settings/automations/", headers=H)
        assert response.status_code in (302, 403)
        db.session.refresh(journey)
        assert journey.automations_on is True

    def test_a_member_cannot(self, db, journey, member):
        response = member.post("/settings/automations/", headers=H)
        assert response.status_code in (302, 403)
        db.session.refresh(journey)
        assert journey.automations_on is True

    def test_signed_out_cannot(self, db, journey, client):
        client.post("/settings/automations/", headers=H)
        db.session.refresh(journey)
        assert journey.automations_on is True

    def test_it_is_written_down(self, db, journey, staff):
        """Who turned off the church's follow-up, and when. This is the kind
        of change somebody notices three weeks later and asks about."""
        from app.models import AuditEvent

        staff.post("/settings/automations/", headers=H, follow_redirects=True)
        events = db.session.scalars(
            db.select(AuditEvent).where(AuditEvent.church_id == journey.id)
        ).all()
        assert any("turned off" in (e.summary or "") for e in events)

    def test_the_row_reopens_where_it_was_pressed(self, db, journey, staff):
        """`_back` validates the row name against ROWS, so a name missing
        from that tuple sends staff back to a closed accordion."""
        from app.blueprints.settings import ROWS

        assert "automations" in ROWS
        body = staff.post("/settings/automations/", headers=H,
                          follow_redirects=True).data.decode()
        assert 'id="automations" open>' in body

    def test_the_row_says_which_way_it_is_set(self, db, journey, staff):
        """Without opening it. A switch whose state you have to click to see
        is a switch people press twice."""
        on = staff.get("/settings/", headers=H).data.decode()
        journey.automations_on = False
        db.session.commit()
        off = staff.get("/settings/", headers=H).data.decode()
        assert on != off


class TestAPausedChurchStillShowsItsEnrollments:
    def test_the_profile_does_not_pretend_nothing_is_running(
        self, db, journey, visitor, staff
    ):
        """Paused is not cancelled, so somebody part-way through a series is
        still part-way through it. Hiding that would make turning it back on
        a surprise."""
        enrol(db, visitor)
        journey.automations_on = False
        db.session.commit()

        body = staff.get(f"/people/{visitor.id}/", headers=H).data.decode()
        assert "First visit" in body or "welcome" in body.lower()

        still_there = db.session.scalars(
            SequenceEnrollment.for_person(journey.id, visitor.id)
        ).all()
        assert len(still_there) == 1

    def test_it_says_paused_instead_of_promising_a_date(
        self, db, journey, visitor, staff
    ):
        """"Next on November 2" is a promise nothing is going to keep while
        the switch is off."""
        enrol(db, visitor)
        running = staff.get(f"/people/{visitor.id}/", headers=H).data.decode()
        assert "next on" in running.lower()

        journey.automations_on = False
        db.session.commit()

        paused = staff.get(f"/people/{visitor.id}/", headers=H).data.decode()
        assert "paused while automatic emails are off" in paused
        assert "next on" not in paused.lower()
