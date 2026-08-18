"""Increment 3: the stuck engine, next steps, and the contact log.

The engine is the part worth testing hardest, because it is the one feature
here that can be wrong without ever raising an error. A flag that fires on
everyone and a flag that never fires both look like working software.
"""

from datetime import date, timedelta

import pytest

from app.models import (
    STATUS_DONE,
    STATUS_OPEN,
    Church,
    ContactLog,
    NextStep,
    Person,
    PersonEvent,
    User,
)
from app.models.base import utcnow
from app.stages import CONTACT_WINDOW_DAYS, STAGES, TRANSITIONAL_STAGES
from tests.conftest import JOURNEY_HOST, PASSWORD

STAFF = "pastor@journeychurchsemo.com"


def add_person(db, slug, first, last, stage, days_in_stage, days_since_contact=None):
    church = db.session.scalar(db.select(Church).where(Church.slug == slug))
    person = Person(
        church_id=church.id,
        first_name=first,
        last_name=last,
        stage=stage,
        stage_since=utcnow() - timedelta(days=days_in_stage),
        last_contact_at=(
            None if days_since_contact is None
            else utcnow() - timedelta(days=days_since_contact)
        ),
    )
    db.session.add(person)
    db.session.commit()
    return person


class TestWhatCountsAsStuck:
    """Both conditions, and only on stages people should be moving out of."""

    def test_overdue_and_silent_is_stuck(self, db):
        person = add_person(db, "journey", "Stuck", "Guest", "guest", 60, None)
        assert person.is_overdue_in_stage
        assert person.is_out_of_contact
        assert person.is_stuck

    def test_overdue_but_recently_contacted_is_not_stuck(self, db):
        person = add_person(db, "journey", "Talked", "To", "guest", 60, 2)
        assert person.is_overdue_in_stage
        assert not person.is_out_of_contact
        assert not person.is_stuck

    def test_silent_but_not_overdue_is_not_stuck(self, db):
        person = add_person(db, "journey", "New", "Guest", "guest", 5, None)
        assert not person.is_overdue_in_stage
        assert person.is_out_of_contact
        assert not person.is_stuck

    def test_a_long_standing_member_is_not_stuck(self, db):
        """The bug that made the first version of this engine useless.

        Run against Journey's real roster, an expectation on every stage
        flagged 39 of 54 people, because a Member of three years read as
        overdue. A Member of three years is exactly where the church wants
        them. Only Visitor, Guest, and Attender can produce a stage flag.
        """
        person = add_person(db, "journey", "Faithful", "Member", "member", 1200, None)
        assert not person.is_overdue_in_stage
        assert not person.is_stuck

    @pytest.mark.parametrize("code", [s.code for s in STAGES if not s.is_transitional])
    def test_no_destination_stage_can_ever_flag(self, db, code):
        person = add_person(db, "journey", "Long", "Timer", code, 5000, None)
        assert not person.is_stuck

    @pytest.mark.parametrize("stage", TRANSITIONAL_STAGES)
    def test_every_transitional_stage_can_flag(self, db, stage):
        person = add_person(
            db, "journey", "T", stage.code.title(), stage.code,
            stage.expected_days + 5, None,
        )
        assert person.is_stuck

    def test_the_boundary_is_strictly_past_the_expectation(self, db):
        exactly = add_person(db, "journey", "Exactly", "OnIt", "guest", 42, None)
        assert not exactly.is_stuck
        one_more = add_person(db, "journey", "One", "Over", "guest", 43, None)
        assert one_more.is_stuck

    def test_the_contact_window_boundary(self, db):
        inside = add_person(
            db, "journey", "Just", "Inside", "guest", 60, CONTACT_WINDOW_DAYS
        )
        assert not inside.is_stuck
        outside = add_person(
            db, "journey", "Just", "Outside", "guest", 60, CONTACT_WINDOW_DAYS + 1
        )
        assert outside.is_stuck


class TestTheQueryMatchesTheProperty:
    """The SQL and the Python must agree, or the dashboard lies."""

    def test_every_person_agrees(self, db):
        add_person(db, "journey", "A", "Stuck", "guest", 60, None)
        add_person(db, "journey", "B", "Contacted", "guest", 60, 3)
        add_person(db, "journey", "C", "Fresh", "visitor", 2, None)
        add_person(db, "journey", "D", "Member", "member", 2000, None)
        add_person(db, "journey", "E", "Attender", "attender", 200, 40)

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        by_query = {p.id for p in db.session.scalars(Person.stuck(church.id))}
        by_property = {
            p.id
            for p in db.session.scalars(Person.for_church(church.id))
            if p.is_stuck
        }
        assert by_query == by_property

    def test_the_count_matches_the_list(self, db):
        for i in range(4):
            add_person(db, "journey", f"P{i}", "Stuck", "guest", 60, None)
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert Person.stuck_count(church.id) == len(
            db.session.scalars(Person.stuck(church.id)).all()
        )

    def test_archived_people_never_flag(self, db):
        person = add_person(db, "journey", "Gone", "Away", "guest", 60, None)
        church = person.church_id
        assert Person.stuck_count(church) == 1
        person.is_archived = True
        db.session.commit()
        assert Person.stuck_count(church) == 0

    def test_the_flag_is_scoped_to_one_church(self, db):
        add_person(db, "journey", "J", "Stuck", "guest", 60, None)
        add_person(db, "riverbend", "R", "Stuck", "guest", 60, None)
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        assert Person.stuck_count(journey.id) == 1
        assert Person.stuck_count(riverbend.id) == 1
        assert {p.first_name for p in db.session.scalars(Person.stuck(journey.id))} == {"J"}


class TestLoggingContactClearsTheFlag:
    """The demo's promise: log the call, watch the flag clear."""

    def test_end_to_end(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        assert person.is_stuck

        staff.post(
            f"/people/{person.id}/contact/",
            data={"method": "call", "summary": "Called after service."},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert not person.is_stuck
        assert person.days_since_contact == 0

    def test_a_note_does_not_clear_the_flag(self, db, staff):
        """Writing that someone should be called is not calling them."""
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/note/",
            data={"body": "Should call Marcus this week."},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.last_contact_at is None
        assert person.is_stuck

    def test_contact_lands_on_the_timeline_and_the_log(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/contact/",
            data={"method": "text", "summary": "Texted about the lunch."},
            headers={"Host": JOURNEY_HOST},
        )
        contacts = db.session.scalars(
            ContactLog.for_person(person.church_id, person.id)
        ).all()
        assert len(contacts) == 1
        assert contacts[0].method == "text"
        assert contacts[0].logged_by_name == "Pastor Reed"

        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert any(e.kind == "contact" for e in events)

    def test_an_unknown_method_is_rejected(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        r = staff.post(
            f"/people/{person.id}/contact/",
            data={"method": "telepathy", "summary": "hi"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        db.session.refresh(person)
        assert person.is_stuck

    def test_an_empty_summary_logs_nothing(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/contact/",
            data={"method": "call", "summary": "  "},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.last_contact_at is None

    def test_last_contact_only_moves_forward(self, db, staff):
        """Backfilling an old call must not make someone look freshly contacted."""
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, 2)
        recent = person.last_contact_at

        older = ContactLog(
            church_id=person.church_id,
            person_id=person.id,
            method="call",
            summary="An older call, entered late",
            occurred_at=utcnow() - timedelta(days=90),
        )
        db.session.add(older)
        db.session.commit()
        db.session.refresh(person)
        assert person.last_contact_at == recent

    def test_contact_cannot_be_logged_across_churches(self, db, staff):
        stranger = add_person(db, "riverbend", "Other", "Person", "guest", 60, None)
        r = staff.post(
            f"/people/{stranger.id}/contact/",
            data={"method": "call", "summary": "should not land"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        db.session.refresh(stranger)
        assert stranger.last_contact_at is None


class TestDenormalizedColumnStaysHonest:
    def test_recompute_rebuilds_from_the_log(self, app, db):
        person = add_person(db, "journey", "Drift", "Case", "guest", 60, None)
        when = utcnow() - timedelta(days=4)
        db.session.add(
            ContactLog(
                church_id=person.church_id,
                person_id=person.id,
                method="call",
                summary="Logged directly, bypassing the route",
                occurred_at=when,
            )
        )
        db.session.commit()
        assert person.last_contact_at is None  # drifted

        result = app.test_cli_runner().invoke(
            args=["recompute-contact", "--church", "journey"]
        )
        assert result.exit_code == 0
        db.session.expire_all()
        rebuilt = Person.get_for_church(person.church_id, person.id)
        assert rebuilt.last_contact_at is not None
        assert abs((rebuilt.last_contact_at - when).total_seconds()) < 2

    def test_recompute_clears_a_value_with_no_log_behind_it(self, app, db):
        person = add_person(db, "journey", "Phantom", "Contact", "guest", 60, 3)
        assert person.last_contact_at is not None

        app.test_cli_runner().invoke(args=["recompute-contact", "--church", "journey"])
        db.session.expire_all()
        rebuilt = Person.get_for_church(person.church_id, person.id)
        assert rebuilt.last_contact_at is None


class TestNextSteps:
    def _staff_user(self, db):
        return db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )

    def test_assigning_a_step_records_an_owner(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        owner = self._staff_user(db)

        staff.post(
            f"/people/{person.id}/step/",
            data={"title": "Invite to the lunch", "owner_user_id": owner.id,
                  "due_on": (date.today() + timedelta(days=7)).isoformat()},
            headers={"Host": JOURNEY_HOST},
        )
        steps = db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).all()
        assert len(steps) == 1
        assert steps[0].owner_name == "Pastor Reed"
        assert steps[0].status == STATUS_OPEN

    def test_a_step_can_be_left_unassigned(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/step/",
            data={"title": "Someone should invite him", "owner_user_id": ""},
            headers={"Host": JOURNEY_HOST},
        )
        step = db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).one()
        assert step.owner_user_id is None

    def test_an_owner_from_another_church_is_refused(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        foreign = db.session.scalar(
            db.select(User).where(User.name == "Other Pastor")
        )
        r = staff.post(
            f"/people/{person.id}/step/",
            data={"title": "x", "owner_user_id": foreign.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        assert db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).all() == []

    def test_an_empty_title_assigns_nothing(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/step/",
            data={"title": "   "},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).all() == []

    def test_a_bad_date_is_refused(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        r = staff.post(
            f"/people/{person.id}/step/",
            data={"title": "x", "due_on": "next tuesday"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_closing_a_step_takes_it_off_the_open_list(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        staff.post(
            f"/people/{person.id}/step/",
            data={"title": "Invite to the lunch"},
            headers={"Host": JOURNEY_HOST},
        )
        step = db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).one()

        staff.post(
            f"/people/{person.id}/step/{step.id}/close/",
            data={"status": STATUS_DONE},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(step)
        assert step.status == STATUS_DONE
        assert step.completed_at is not None
        assert db.session.scalars(
            NextStep.open_for_person(person.church_id, person.id)
        ).all() == []

    def test_a_step_from_another_church_cannot_be_closed(self, db, staff):
        stranger = add_person(db, "riverbend", "Other", "Person", "guest", 60, None)
        step = NextStep(
            church_id=stranger.church_id,
            person_id=stranger.id,
            title="Theirs, not ours",
        )
        db.session.add(step)
        db.session.commit()

        r = staff.post(
            f"/people/{stranger.id}/step/{step.id}/close/",
            data={"status": STATUS_DONE},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        db.session.refresh(step)
        assert step.status == STATUS_OPEN

    def test_overdue_is_computed_from_the_due_date(self, db):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        past = NextStep(
            church_id=person.church_id, person_id=person.id, title="Late",
            due_on=date.today() - timedelta(days=3),
        )
        future = NextStep(
            church_id=person.church_id, person_id=person.id, title="Soon",
            due_on=date.today() + timedelta(days=3),
        )
        undated = NextStep(
            church_id=person.church_id, person_id=person.id, title="Someday"
        )
        db.session.add_all([past, future, undated])
        db.session.commit()

        assert past.is_overdue
        assert not future.is_overdue
        assert not undated.is_overdue

    def test_a_closed_step_is_never_overdue(self, db):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        step = NextStep(
            church_id=person.church_id, person_id=person.id, title="Late",
            due_on=date.today() - timedelta(days=10),
        )
        db.session.add(step)
        db.session.commit()
        assert step.is_overdue

        step.close()
        db.session.commit()
        assert not step.is_overdue


class TestOwnership:
    def test_taking_ownership(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        owner = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        staff.post(
            f"/people/{person.id}/owner/",
            data={"owner_user_id": owner.id},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.owner_name == "Pastor Reed"

    def test_releasing_ownership(self, db, staff):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        owner = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        person.owner_user_id, person.owner_name = owner.id, owner.name
        db.session.commit()

        staff.post(
            f"/people/{person.id}/owner/",
            data={"owner_user_id": ""},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.owner_user_id is None

    def test_unowned_count(self, db):
        person = add_person(db, "journey", "Marcus", "Webb", "guest", 60, None)
        add_person(db, "journey", "Dana", "Webb", "member", 400, 5)
        church = person.church_id
        assert Person.unowned_count(church) == 2

        owner = db.session.scalar(db.select(User).where(User.role == "staff"))
        person.owner_user_id = owner.id
        db.session.commit()
        assert Person.unowned_count(church) == 1


class TestDashboard:
    def test_the_flagged_card_shows_stuck_people(self, db, staff):
        add_person(db, "journey", "Camila", "Reyes", "guest", 60, None)
        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert b"Camila" in r.data
        assert b"Needs a person" in r.data

    def test_the_card_says_so_when_nobody_is_stuck(self, db, staff):
        add_person(db, "journey", "Fine", "Person", "guest", 60, 2)
        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert b"Nobody is stuck" in r.data

    def test_members_never_see_the_flagged_card(self, db, member):
        add_person(db, "journey", "Camila", "Reyes", "guest", 60, None)
        r = member.get("/", headers={"Host": JOURNEY_HOST})
        assert b"Needs a person" not in r.data

    def test_the_stuck_page_requires_a_leader(self, member):
        assert member.get("/people/stuck/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_contacted_in_the_last_week(self, db):
        person = add_person(db, "journey", "Recent", "Contact", "member", 400, 3)
        add_person(db, "journey", "Old", "Contact", "member", 400, 40)
        assert Person.contacted_since(person.church_id, 7) == 1
