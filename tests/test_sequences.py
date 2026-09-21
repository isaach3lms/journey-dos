"""Increment 14: sequences and automations.

The two hard stops carry almost all the value. A welcome series that keeps
emailing somebody the pastor already phoned is worse than no automation,
because it tells the person nobody is paying attention.
"""

from datetime import timedelta

import pytest

from app.automation import enroll_for_stage, on_contact_logged, on_stage_changed, run_due
from app.models import (
    Church,
    OutboxMessage,
    Person,
    PersonEvent,
    SequenceEnrollment,
)
from app.models.base import utcnow
from app.models.sequence import (
    REASON_CONTACT,
    REASON_MANUAL,
    REASON_NO_EMAIL,
    REASON_OPTED_OUT,
    REASON_STAGE_LEFT,
    REASON_TARGET_STAGE,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_STOPPED,
)
from app.sequences import MAX_CATCHUP_DAYS, SEQUENCES, WELCOME, get, triggered_by
from tests.conftest import JOURNEY_HOST


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


def make_due(db, enrollment, days_overdue=0):
    enrollment.next_due_at = utcnow() - timedelta(days=days_overdue)
    db.session.commit()


class TestDefinitions:
    def test_sequences_are_python_not_rows(self):
        """Shape, not content. Changing a sentence is a diff, not a migration."""
        assert len(SEQUENCES) >= 1
        assert all(s.steps for s in SEQUENCES)

    def test_every_step_uses_a_real_notification_category(self):
        from app.categories import CATEGORY_BY_CODE

        for sequence in SEQUENCES:
            for step in sequence.steps:
                assert step.category in CATEGORY_BY_CODE, sequence.code

    def test_every_sequence_points_at_a_real_stage(self):
        from app.stages import STAGE_CODES

        for sequence in SEQUENCES:
            assert sequence.trigger_stage in STAGE_CODES
            assert sequence.target_stage in STAGE_CODES

    def test_the_target_is_ahead_of_the_trigger(self):
        """A sequence pushing backwards would never be able to complete."""
        from app.stages import stage_order

        for sequence in SEQUENCES:
            assert stage_order(sequence.target_stage) > stage_order(sequence.trigger_stage)

    def test_steps_are_in_ascending_day_order(self):
        for sequence in SEQUENCES:
            days = [step.day for step in sequence.steps]
            assert days == sorted(days), sequence.code

    def test_templates_only_use_fields_that_exist(self, db, journey, visitor):
        from app.sequences import render

        for sequence in SEQUENCES:
            for step in sequence.steps:
                assert render(step.subject, visitor, journey)
                assert render(step.body, visitor, journey)

    def test_no_step_uses_a_transactional_category(self):
        """Marketing dressed as transactional is how a church earns complaints."""
        from app.categories import TRANSACTIONAL_CODES

        for sequence in SEQUENCES:
            for step in sequence.steps:
                assert step.category not in TRANSACTIONAL_CODES


class TestEnrollment:
    def test_arriving_at_a_stage_starts_its_sequence(self, db, visitor):
        enrollment = enrol(db, visitor)
        assert enrollment is not None
        assert enrollment.sequence_code == WELCOME.code
        assert enrollment.status == STATUS_ACTIVE

    def test_the_first_step_is_due_immediately(self, db, visitor):
        enrollment = enrol(db, visitor)
        assert enrollment.next_due_at <= utcnow() + timedelta(seconds=5)

    def test_a_stage_with_no_sequence_enrols_nobody(self, db, journey):
        person = Person(
            church_id=journey.id, first_name="A", last_name="Leader", stage="leader"
        )
        db.session.add(person)
        db.session.commit()
        assert enroll_for_stage(person) == []

    def test_nobody_is_enrolled_twice(self, db, visitor):
        """Moving back and forth across a boundary must not restart it."""
        enrol(db, visitor)
        assert enroll_for_stage(visitor) == []
        db.session.commit()
        assert len(db.session.scalars(db.select(SequenceEnrollment)).all()) == 1

    def test_enrolling_lands_on_the_timeline(self, db, visitor):
        enrol(db, visitor)
        events = db.session.scalars(
            PersonEvent.for_person(visitor.church_id, visitor.id)
        ).all()
        assert any("Started" in e.summary for e in events)

    def test_triggered_by(self):
        assert WELCOME in triggered_by("visitor")
        assert triggered_by("leader") == []


class TestHardStopContact:
    """The most important behaviour in this increment."""

    def test_a_logged_call_stops_it(self, db, visitor):
        enrollment = enrol(db, visitor)
        on_contact_logged(visitor)
        db.session.commit()

        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED
        assert enrollment.end_reason == REASON_CONTACT

    def test_logging_contact_through_the_route_stops_it(self, db, visitor, staff):
        enrollment = enrol(db, visitor)
        staff.post(
            f"/people/{visitor.id}/contact/",
            data={"method": "call", "summary": "Called her after the service."},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED
        assert enrollment.end_reason == REASON_CONTACT

    def test_a_note_does_not_stop_it(self, db, visitor, staff):
        """Writing that somebody should be called is not calling them."""
        enrollment = enrol(db, visitor)
        staff.post(
            f"/people/{visitor.id}/note/",
            data={"body": "Should call Nina this week."},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_ACTIVE

    def test_contact_between_due_and_send_still_stops_it(self, db, visitor):
        """The race the send-time check exists to close.

        Somebody can be phoned in the hour between a step falling due and the
        worker waking up.
        """
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)

        visitor.last_contact_at = utcnow()
        db.session.commit()

        counts = run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert counts["sent"] == 0
        assert enrollment.end_reason == REASON_CONTACT
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_contact_before_enrollment_does_not_stop_it(self, db, visitor):
        """An old conversation is not a reason to skip a welcome series."""
        visitor.last_contact_at = utcnow() - timedelta(days=60)
        db.session.commit()

        enrollment = enrol(db, visitor)
        make_due(db, enrollment)
        counts = run_due(church_id=visitor.church_id)
        assert counts["sent"] == 1


class TestHardStopTargetStage:
    def test_reaching_the_target_completes_it(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.stage = WELCOME.target_stage
        on_stage_changed(visitor, "visitor")
        db.session.commit()

        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_COMPLETED
        assert enrollment.end_reason == REASON_TARGET_STAGE

    def test_going_past_the_target_also_completes_it(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.stage = "leader"
        on_stage_changed(visitor, "visitor")
        db.session.commit()

        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_COMPLETED

    def test_moving_to_another_stage_short_of_the_target_stops_it(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.stage = "guest"
        on_stage_changed(visitor, "visitor")
        db.session.commit()

        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED
        assert enrollment.end_reason == REASON_STAGE_LEFT

    def test_moving_stage_may_start_the_next_sequence(self, db, visitor):
        enrol(db, visitor)
        visitor.stage = "guest"
        on_stage_changed(visitor, "visitor")
        db.session.commit()

        codes = {
            e.sequence_code
            for e in db.session.scalars(db.select(SequenceEnrollment))
        }
        assert "guest_follow_up" in codes

    def test_moving_through_the_route_stops_it(self, db, visitor, staff):
        enrollment = enrol(db, visitor)
        staff.post(
            f"/people/{visitor.id}/stage/",
            data={"stage": "attender"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_COMPLETED

    def test_reaching_the_target_between_due_and_send_stops_it(self, db, visitor):
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)
        visitor.stage = "attender"
        db.session.commit()

        counts = run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert counts["sent"] == 0
        assert enrollment.end_reason == REASON_TARGET_STAGE


class TestRunning:
    def test_a_due_step_is_queued(self, db, visitor):
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)

        counts = run_due(church_id=visitor.church_id)
        assert counts["sent"] == 1

        message = db.session.scalars(db.select(OutboxMessage)).one()
        assert "Nina" in message.subject
        assert message.category == WELCOME.steps[0].category

    def test_it_queues_rather_than_sends(self, db, visitor):
        """The worker must not be slowed or broken by the mail provider."""
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)
        run_due(church_id=visitor.church_id)
        assert db.session.scalars(db.select(OutboxMessage)).one().status == "queued"

    def test_a_step_that_is_not_due_is_left_alone(self, db, visitor):
        enrollment = enrol(db, visitor)
        enrollment.next_due_at = utcnow() + timedelta(days=3)
        db.session.commit()

        assert run_due(church_id=visitor.church_id)["sent"] == 0

    def test_running_twice_does_not_send_twice(self, db, visitor):
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)

        run_due(church_id=visitor.church_id)
        make_due(db, enrollment)
        run_due(church_id=visitor.church_id)

        subjects = [m.subject for m in db.session.scalars(db.select(OutboxMessage))]
        assert len(subjects) == len(set(subjects))

    def test_the_series_advances_one_step_at_a_time(self, db, visitor):
        enrollment = enrol(db, visitor)
        for _ in range(len(WELCOME.steps)):
            make_due(db, enrollment)
            run_due(church_id=visitor.church_id)
            db.session.refresh(enrollment)

        assert enrollment.steps_sent == len(WELCOME.steps)
        assert enrollment.status == STATUS_COMPLETED

    def test_offsets_are_measured_from_enrollment_not_the_last_send(self, db, visitor):
        """A worker down for a day must not push the whole series a day later."""
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)
        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)

        expected = enrollment.enrolled_at + timedelta(days=WELCOME.steps[1].day)
        assert abs((enrollment.next_due_at - expected).total_seconds()) < 2

    def test_a_badly_overdue_step_is_skipped_not_sent(self, db, visitor):
        """If the worker was off for a month, nobody wants the day 2 email in
        September. They want to be left alone."""
        enrollment = enrol(db, visitor)
        make_due(db, enrollment, days_overdue=MAX_CATCHUP_DAYS + 2)

        counts = run_due(church_id=visitor.church_id)
        assert counts["skipped"] == 1
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_a_person_who_opted_out_is_stopped(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.set_preference(WELCOME.steps[0].category, False)
        db.session.commit()
        make_due(db, enrollment)

        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED
        assert enrollment.end_reason == REASON_OPTED_OUT

    def test_a_person_with_no_email_is_stopped(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.email = None
        db.session.commit()
        make_due(db, enrollment)

        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert enrollment.end_reason == REASON_NO_EMAIL

    def test_an_archived_person_is_stopped(self, db, visitor):
        enrollment = enrol(db, visitor)
        visitor.is_archived = True
        db.session.commit()
        make_due(db, enrollment)

        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED

    def test_running_is_scoped_to_one_church(self, db, journey, visitor):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(
            church_id=riverbend.id, first_name="Their", last_name="Visitor",
            email="theirs@example.com", stage="visitor",
        )
        db.session.add(theirs)
        db.session.commit()

        mine = enrol(db, visitor)
        yours = enrol(db, theirs)
        make_due(db, mine)
        make_due(db, yours)

        run_due(church_id=journey.id)
        assert {m.to_email for m in db.session.scalars(db.select(OutboxMessage))} == {
            "nina@example.com"
        }


class TestEditingASequence:
    def test_a_shortened_sequence_completes_rather_than_erroring(self, db, visitor):
        """A sequence edited between sends must not strand an enrollment."""
        enrollment = enrol(db, visitor)
        enrollment.step_index = 99
        enrollment.next_due_at = utcnow()
        db.session.commit()

        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_COMPLETED

    def test_an_unknown_sequence_code_does_not_crash(self, db, visitor):
        enrollment = enrol(db, visitor)
        enrollment.sequence_code = "removed_last_year"
        enrollment.next_due_at = utcnow()
        db.session.commit()

        run_due(church_id=visitor.church_id)
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_COMPLETED

    def test_the_name_falls_back_to_the_code(self, db, visitor):
        enrollment = enrol(db, visitor)
        enrollment.sequence_code = "removed_last_year"
        assert enrollment.sequence_name == "removed_last_year"


class TestStaffOverride:
    def test_staff_can_stop_a_sequence_by_hand(self, db, visitor, staff):
        """Without having to fake a phone call to do it."""
        enrollment = enrol(db, visitor)
        staff.post(
            f"/people/{visitor.id}/sequence/{enrollment.id}/stop/",
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(enrollment)
        assert enrollment.status == STATUS_STOPPED
        assert enrollment.end_reason == REASON_MANUAL

    def test_an_enrollment_from_another_church_is_a_404(self, db, journey, visitor, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(
            church_id=riverbend.id, first_name="Their", last_name="Visitor",
            stage="visitor",
        )
        db.session.add(theirs)
        db.session.flush()
        enrollment = SequenceEnrollment(
            church_id=riverbend.id, person_id=theirs.id, sequence_code=WELCOME.code
        )
        db.session.add(enrollment)
        db.session.commit()

        r = staff.post(
            f"/people/{visitor.id}/sequence/{enrollment.id}/stop/",
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404

    def test_the_person_page_shows_what_is_running(self, db, visitor, staff):
        enrol(db, visitor)
        r = staff.get(f"/people/{visitor.id}/", headers={"Host": JOURNEY_HOST})
        assert b"First visit welcome" in r.data


class TestDashboardCard:
    def test_it_counts_what_ran_and_what_a_human_stopped(self, db, journey, visitor, staff):
        enrollment = enrol(db, visitor)
        make_due(db, enrollment)
        run_due(church_id=journey.id)

        second = Person(
            church_id=journey.id, first_name="Andre", last_name="Bright",
            email="andre@example.com", stage="visitor",
        )
        db.session.add(second)
        db.session.commit()
        enrol(db, second)
        on_contact_logged(second)
        db.session.commit()

        assert SequenceEnrollment.sent_last_days(journey.id, 7) == 1
        assert SequenceEnrollment.stopped_by_contact_count(journey.id) == 1

        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert b"Running without staff time" in r.data

    def test_members_never_see_it(self, db, visitor, member):
        r = member.get("/", headers={"Host": JOURNEY_HOST})
        assert b"Running without staff time" not in r.data
