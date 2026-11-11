"""Taking old contacts off the roster in bulk.

Archive, never delete. A person row is referenced by check-in history, matched
giving, service assignments, and messages. Deleting one erases a child's
check-in record, which a church has to keep, and rewrites who said what in a
room.
"""

from datetime import timedelta

import pytest

from app.models import (
    AuditEvent,
    Checkin,
    CheckinSession,
    Church,
    ExternalGift,
    Household,
    Person,
    SequenceEnrollment,
    User,
)
from app.models.base import utcnow
from app.models.giving_mirror import PROVIDER_TITHELY
from tests.conftest import JOURNEY_HOST


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def roster(db, journey):
    people = [
        Person(
            church_id=journey.id, first_name=f"Old{i}", last_name="Contact",
            email=f"old{i}@example.com", stage="visitor",
        )
        for i in range(4)
    ]
    db.session.add_all(people)
    db.session.commit()
    return people


class TestArchivingInBulk:
    def test_several_people_at_once(self, db, journey, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster[:3]]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert [p.is_archived for p in roster] == [True, True, True, False]

    def test_they_drop_off_the_roster(self, db, journey, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster]},
            headers={"Host": JOURNEY_HOST},
        )
        listed = db.session.scalars(Person.for_church(journey.id)).all()
        assert not any(p.id in {r.id for r in roster} for p in listed)

    def test_they_drop_out_of_the_counts(self, db, journey, roster, staff):
        before = Person.total_for_church(journey.id)
        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster]},
            headers={"Host": JOURNEY_HOST},
        )
        assert Person.total_for_church(journey.id) == before - len(roster)

    def test_they_stop_being_flagged_as_stuck(self, db, journey, roster, staff):
        for person in roster:
            person.stage_since = utcnow() - timedelta(days=400)
        db.session.commit()
        assert Person.stuck_count(journey.id) >= len(roster)

        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        stuck = {p.id for p in db.session.scalars(Person.stuck(journey.id))}
        assert not stuck & {p.id for p in roster}

    def test_nothing_ticked_says_so(self, db, staff):
        r = staff.post(
            "/people/archive/", headers={"Host": JOURNEY_HOST}, follow_redirects=True
        )
        assert b"Tick somebody first" in r.data

    def test_archiving_twice_is_harmless(self, db, roster, staff):
        for _ in range(2):
            staff.post(
                "/people/archive/",
                data={"person_id": [roster[0].id]},
                headers={"Host": JOURNEY_HOST},
            )
        db.session.expire_all()
        assert roster[0].is_archived

    def test_it_is_audited_with_the_names(self, db, journey, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster[:2]]},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        archived = [e for e in events if "archived" in e.summary]
        assert archived
        assert "Old0 Contact" in archived[0].detail

    def test_it_lands_on_each_timeline(self, db, journey, roster, staff):
        from app.models import PersonEvent

        staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(
            PersonEvent.for_person(journey.id, roster[0].id)
        ).all()
        assert any("Archived" in e.summary for e in events)


class TestNothingIsDestroyed:
    """The reason this archives rather than deletes."""

    def test_a_childs_check_in_history_survives(self, db, journey, staff):
        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add(household)
        db.session.flush()
        child = Person(
            church_id=journey.id, first_name="Ellie", last_name="Webb",
            stage="member", household_id=household.id, is_child=True,
        )
        session = CheckinSession(
            church_id=journey.id, name="Sunday", starts_at=utcnow()
        )
        db.session.add_all([child, session])
        db.session.flush()
        db.session.add(
            Checkin(
                church_id=journey.id, session_id=session.id, person_id=child.id,
                household_id=household.id, pickup_code="ABCD",
            )
        )
        db.session.commit()

        staff.post(
            "/people/archive/",
            data={"person_id": [child.id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert db.session.scalars(db.select(Checkin)).all() != []
        assert db.session.get(Person, child.id) is not None

    def test_matched_giving_survives(self, db, journey, roster, staff):
        from datetime import date

        db.session.add(
            ExternalGift(
                church_id=journey.id, provider=PROVIDER_TITHELY,
                provider_txn_id="t1", amount_cents=4000,
                received_on=date(2026, 8, 2), person_id=roster[0].id,
                match_status="matched",
            )
        )
        db.session.commit()

        staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        gift = db.session.scalars(db.select(ExternalGift)).one()
        assert gift.person_id == roster[0].id

    def test_a_running_sequence_stops_at_the_next_send(self, db, journey, roster, staff):
        from app.automation import enroll_for_stage, run_due

        enrollment = enroll_for_stage(roster[0])[0]
        db.session.commit()

        staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        enrollment.next_due_at = utcnow()
        db.session.commit()

        run_due(church_id=journey.id)
        db.session.refresh(enrollment)
        assert enrollment.status == "stopped"


class TestGuards:
    def test_somebody_from_another_church_is_silently_dropped(self, db, staff):
        """A bulk action is exactly where a stray id would slip through."""
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add(theirs)
        db.session.commit()

        staff.post(
            "/people/archive/",
            data={"person_id": [theirs.id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(theirs)
        assert not theirs.is_archived

    def test_nobody_archives_their_own_record(self, db, journey, staff):
        """It hides you from the roster you are standing on."""
        person = Person(
            church_id=journey.id, first_name="Pastor", last_name="Reed",
            email="pastor@journeychurchsemo.com", stage="leader",
        )
        db.session.add(person)
        db.session.flush()
        user = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == journey.id,
                User.role == "staff",
            )
        )
        user.person_id = person.id
        db.session.commit()

        r = staff.post(
            "/people/archive/",
            data={"person_id": [person.id]},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"cannot archive your own record" in r.data
        db.session.refresh(person)
        assert not person.is_archived

    def test_a_member_cannot_archive_anyone(self, db, roster, member):
        r = member.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        db.session.refresh(roster[0])
        assert not roster[0].is_archived

    def test_the_filter_survives_the_redirect(self, db, roster, staff):
        """Landing on an unfiltered page means finding your place again, which
        is how somebody archives the wrong batch next."""
        r = staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id], "stage": "visitor", "q": "Old"},
            headers={"Host": JOURNEY_HOST},
        )
        assert "stage=visitor" in r.headers["Location"]
        assert "q=Old" in r.headers["Location"]


class TestRestoring:
    def test_archived_people_can_come_back(self, db, journey, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [p.id for p in roster[:2]]},
            headers={"Host": JOURNEY_HOST},
        )
        staff.post(
            "/people/archived/restore/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert not roster[0].is_archived
        assert roster[1].is_archived

    def test_the_archived_screen_lists_them(self, db, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        r = staff.get("/people/archived/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Old0 Contact" in r.data

    def test_it_only_shows_this_church(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(
            church_id=riverbend.id, first_name="Their", last_name="Archived",
            stage="member", is_archived=True,
        )
        db.session.add(theirs)
        db.session.commit()

        r = staff.get("/people/archived/", headers={"Host": JOURNEY_HOST})
        assert b"Their Archived" not in r.data

    def test_the_roster_links_to_it(self, db, roster, staff):
        staff.post(
            "/people/archive/",
            data={"person_id": [roster[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"/people/archived/" in r.data
        assert b"Archived (1)" in r.data

    def test_a_member_cannot_restore_anyone(self, db, roster, member):
        assert member.get(
            "/people/archived/", headers={"Host": JOURNEY_HOST}
        ).status_code == 403


class TestTheRosterOffersIt:
    def test_checkboxes_are_on_the_roster(self, db, roster, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b'class="rowpick"' in r.data
        assert b'name="person_id"' in r.data

    def test_the_bar_explains_what_archiving_does(self, db, roster, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Nothing is deleted" in r.data
