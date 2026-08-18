"""Increment 2: people, households, and stages.

`TestTenantIsolation` is the part that matters. Everything reachable in this
increment takes an id from a URL, and an id is just a number: nothing about it
says which church it belongs to. Those tests are what prove the WHERE clause is
actually there.
"""

import pytest

from app.models import Church, Household, KIND_NOTE, KIND_STAGE_CHANGE, Person, PersonEvent
from app.models.base import utcnow
from app.stages import STAGE_CODES, STAGES, is_forward, next_stage, stage_order
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST


def make_person(db, church_slug, first, last, stage="visitor", **kwargs):
    church = db.session.scalar(db.select(Church).where(Church.slug == church_slug))
    person = Person(
        church_id=church.id, first_name=first, last_name=last, stage=stage, **kwargs
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def roster(db):
    """A small roster at Journey and one person at Riverbend."""
    people = {
        "marcus": make_person(db, "journey", "Marcus", "Webb", "guest",
                              email="marcus@example.com"),
        "dana": make_person(db, "journey", "Dana", "Webb", "volunteer",
                            email="dana@example.com"),
        "chris": make_person(db, "journey", "Chris", "Vaughn", "attender"),
        "other": make_person(db, "riverbend", "Someone", "Else", "member",
                             email="someone@example.com"),
    }
    return people


class TestStages:
    def test_the_rail_is_ordered_and_unique(self):
        orders = [stage.order for stage in STAGES]
        assert orders == sorted(orders) == list(range(len(STAGES)))
        assert len(set(STAGE_CODES)) == len(STAGE_CODES)

    def test_forward_and_backward_are_distinguishable(self):
        assert is_forward("visitor", "member")
        assert not is_forward("member", "visitor")
        assert not is_forward("member", "member")

    def test_next_stage_runs_out_at_the_end(self):
        assert next_stage("visitor").code == "guest"
        assert next_stage(STAGES[-1].code) is None

    def test_an_unknown_code_does_not_crash_ordering(self):
        assert stage_order("not-a-stage") == -1

    def test_the_database_rejects_a_stage_that_does_not_exist(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        db.session.add(
            Person(church_id=church.id, first_name="A", last_name="B", stage="superfan")
        )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


class TestRailCounts:
    def test_counts_include_stages_with_nobody_in_them(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        counts = Person.stage_counts(church.id)
        assert set(counts) == set(STAGE_CODES)
        assert counts["guest"] == 1
        assert counts["leader"] == 0

    def test_counts_are_scoped_to_one_church(self, db, roster):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        assert Person.total_for_church(journey.id) == 3
        assert Person.total_for_church(riverbend.id) == 1
        assert Person.stage_counts(riverbend.id)["member"] == 1
        assert Person.stage_counts(journey.id)["member"] == 0

    def test_archived_people_leave_the_rail(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        roster["marcus"].is_archived = True
        db.session.commit()
        assert Person.stage_counts(church.id)["guest"] == 0
        assert Person.total_for_church(church.id) == 2


class TestTenantIsolation:
    """A person id in a URL says nothing about which church it belongs to."""

    def test_one_church_cannot_open_another_church_person(self, db, roster, staff):
        stranger = roster["other"].id
        r = staff.get(f"/people/{stranger}/", headers={"Host": JOURNEY_HOST})
        # 404, not 403. A 403 would confirm the id exists somewhere.
        assert r.status_code == 404

    def test_the_roster_only_lists_this_church(self, db, roster, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Marcus" in r.data
        assert b"Someone" not in r.data

    def test_get_for_church_refuses_a_foreign_id(self, db, roster):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert Person.get_for_church(journey.id, roster["other"].id) is None
        assert Person.get_for_church(journey.id, roster["marcus"].id) is not None

    def test_a_stage_move_across_churches_is_refused(self, db, roster, staff):
        stranger = roster["other"]
        before = stranger.stage
        r = staff.post(
            f"/people/{stranger.id}/stage/",
            data={"stage": "leader"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        db.session.refresh(stranger)
        assert stranger.stage == before

    def test_a_note_across_churches_is_refused(self, db, roster, staff):
        stranger = roster["other"]
        r = staff.post(
            f"/people/{stranger.id}/note/",
            data={"body": "should not land"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        assert db.session.scalars(
            PersonEvent.for_person(stranger.church_id, stranger.id)
        ).all() == []

    def test_search_cannot_reach_across_churches(self, db, roster):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        results = db.session.scalars(Person.search(journey.id, term="Someone")).all()
        assert results == []

    def test_an_event_files_against_the_person_s_own_church(self, db, roster):
        """`record` takes the church from the person so it cannot be passed wrong."""
        person = roster["other"]
        event = PersonEvent.record(person, KIND_NOTE, "note")
        db.session.commit()
        assert event.church_id == person.church_id


class TestRoster:
    def test_search_matches_first_last_and_full_name(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        for term in ["marcus", "webb", "Marcus Webb", "MARCUS"]:
            found = db.session.scalars(Person.search(church.id, term=term)).all()
            assert any(p.first_name == "Marcus" for p in found), term

    def test_search_matches_email(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        found = db.session.scalars(Person.search(church.id, term="dana@example")).all()
        assert len(found) == 1

    def test_stage_filter_narrows_the_list(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        found = db.session.scalars(Person.search(church.id, stage="guest")).all()
        assert [p.first_name for p in found] == ["Marcus"]

    def test_an_unknown_stage_in_the_url_is_a_404_not_a_silent_full_list(self, staff):
        r = staff.get("/people/?stage=superfan", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_sorted_by_last_name(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        names = [p.last_name for p in db.session.scalars(Person.search(church.id))]
        assert names == sorted(names)


class TestStageMoves:
    def test_moving_records_an_event(self, db, roster, staff):
        person = roster["marcus"]
        staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "attender"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.stage == "attender"

        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert events[0].kind == KIND_STAGE_CHANGE
        assert "Guest" in events[0].summary and "Attender" in events[0].summary

    def test_moving_restarts_the_clock(self, db, roster, staff):
        """Increment 3's stuck engine measures from stage_since."""
        from datetime import timedelta

        person = roster["marcus"]
        person.stage_since = utcnow() - timedelta(days=200)
        db.session.commit()
        assert person.days_in_stage >= 199

        staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "attender"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.days_in_stage == 0

    def test_the_event_records_who_did_it(self, db, roster, staff):
        person = roster["marcus"]
        staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "attender"},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert events[0].actor_name == "Pastor Reed"

    def test_moving_backwards_is_allowed_and_labelled(self, db, roster, staff):
        """People do go backwards, and a system that refuses to say so lies."""
        person = roster["dana"]
        staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "attender"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.stage == "attender"
        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert "back" in events[0].detail

    def test_an_invalid_target_stage_is_rejected(self, db, roster, staff):
        person = roster["marcus"]
        r = staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "superfan"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400
        db.session.refresh(person)
        assert person.stage == "guest"

    def test_moving_to_the_same_stage_records_nothing(self, db, roster, staff):
        person = roster["marcus"]
        staff.post(
            f"/people/{person.id}/stage/",
            data={"stage": "guest"},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert events == []


class TestTimeline:
    def test_newest_first(self, db, roster):
        from datetime import timedelta

        person = roster["marcus"]
        old = PersonEvent.record(person, KIND_NOTE, "older",
                                 occurred_at=utcnow() - timedelta(days=5))
        new = PersonEvent.record(person, KIND_NOTE, "newer")
        db.session.commit()

        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert [e.id for e in events] == [new.id, old.id]

    def test_a_note_lands_on_the_timeline(self, db, roster, staff):
        person = roster["marcus"]
        staff.post(
            f"/people/{person.id}/note/",
            data={"body": "Called after service. Coming to the next lunch."},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert events[0].kind == KIND_NOTE
        assert "next lunch" in events[0].summary

    def test_an_empty_note_is_refused(self, db, roster, staff):
        person = roster["marcus"]
        staff.post(
            f"/people/{person.id}/note/",
            data={"body": "   "},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all() == []

    def test_timestamps_come_back_aware(self, db, roster):
        """The comparison in days_in_stage would raise on a naive value."""
        person = roster["marcus"]
        PersonEvent.record(person, KIND_NOTE, "x")
        db.session.commit()
        db.session.expire_all()

        event = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).first()
        assert event.occurred_at.tzinfo is not None
        assert event.occurred_at <= utcnow()

    def test_deleting_a_person_takes_the_timeline_with_them(self, db, roster):
        person = roster["marcus"]
        PersonEvent.record(person, KIND_NOTE, "x")
        db.session.commit()
        church_id, person_id = person.church_id, person.id

        db.session.delete(person)
        db.session.commit()
        assert db.session.scalars(
            PersonEvent.for_person(church_id, person_id)
        ).all() == []


class TestHouseholds:
    def test_find_or_create_does_not_duplicate(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        a = Household.find_or_create(church.id, "The Webbs")
        db.session.commit()
        b = Household.find_or_create(church.id, "The Webbs")
        db.session.commit()
        assert a.id == b.id

    def test_the_same_household_name_at_two_churches_is_two_households(self, db):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        a = Household.find_or_create(journey.id, "The Smiths")
        b = Household.find_or_create(riverbend.id, "The Smiths")
        db.session.commit()
        assert a.id != b.id

    def test_deleting_a_household_does_not_delete_its_people(self, db, roster):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        household = Household.find_or_create(church.id, "The Webbs")
        db.session.commit()
        roster["marcus"].household_id = household.id
        db.session.commit()

        db.session.delete(household)
        db.session.commit()
        db.session.refresh(roster["marcus"])
        assert roster["marcus"].household_id is None


class TestAccess:
    def test_a_member_cannot_reach_the_roster(self, member):
        assert member.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_member_cannot_open_a_person(self, roster, member):
        r = member.get(f"/people/{roster['marcus'].id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 403

    def test_a_leader_can(self, roster, leader):
        assert leader.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 200

    def test_signed_out_goes_to_login(self, client):
        r = client.get("/people/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_dashboard_rail_is_hidden_from_members(self, member, roster):
        """Asserting on the rail markup, not on words that also appear in copy.

        The roadmap card lists "People, households, stages" for everyone, so a
        naive search for "stages" matches on every page.
        """
        r = member.get("/", headers={"Host": JOURNEY_HOST})
        assert b'class="stages"' not in r.data

    def test_the_dashboard_rail_is_shown_to_staff(self, staff, roster):
        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert b'class="stages"' in r.data


class TestElapsedTime:
    def test_days_in_stage_never_goes_negative(self, db, roster):
        from datetime import timedelta

        person = roster["marcus"]
        person.stage_since = utcnow() + timedelta(days=3)
        db.session.commit()
        assert person.days_in_stage == 0

    def test_days_in_stage_counts_whole_days(self, db, roster):
        from datetime import timedelta

        person = roster["marcus"]
        person.stage_since = utcnow() - timedelta(days=43, hours=2)
        db.session.commit()
        assert person.days_in_stage == 43


class TestImport:
    """A partial import is worse than a failed one."""

    def _write(self, tmp_path, rows, header=None):
        import csv

        header = header or ["first_name", "last_name", "email", "phone",
                            "stage", "household", "first_seen_on"]
        path = tmp_path / "roster.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def test_one_bad_row_writes_nothing(self, app, db, tmp_path):
        runner = app.test_cli_runner()
        path = self._write(tmp_path, [
            {"first_name": "Good", "last_name": "Row", "email": "g@example.com",
             "phone": "", "stage": "guest", "household": "", "first_seen_on": ""},
            {"first_name": "Bad", "last_name": "Row", "email": "b@example.com",
             "phone": "", "stage": "superfan", "household": "", "first_seen_on": ""},
        ])
        result = runner.invoke(args=["import-people", "--church", "journey", "--file", path])
        assert result.exit_code != 0
        assert "superfan" in result.output
        # The valid row must not have landed either.
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert db.session.scalars(Person.search(church.id, term="Good")).all() == []

    def test_a_clean_file_imports_and_sets_stage_since_from_first_seen(
        self, app, db, tmp_path
    ):
        runner = app.test_cli_runner()
        path = self._write(tmp_path, [
            {"first_name": "Nina", "last_name": "Ibarra", "email": "n@example.com",
             "phone": "", "stage": "visitor", "household": "Nina Ibarra",
             "first_seen_on": "2026-06-01"},
        ])
        result = runner.invoke(args=["import-people", "--church", "journey", "--file", path])
        assert result.exit_code == 0

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        person = db.session.scalars(Person.search(church.id, term="Nina")).one()
        # Not the import timestamp: that would tell a pastor everyone arrived
        # today and blind the stuck engine for months.
        assert person.stage_since.date().isoformat() == "2026-06-01"
        assert person.days_in_stage > 0
        assert person.household.name == "Nina Ibarra"

    def test_a_dry_run_writes_nothing(self, app, db, tmp_path):
        runner = app.test_cli_runner()
        path = self._write(tmp_path, [
            {"first_name": "Ghost", "last_name": "Row", "email": "gh@example.com",
             "phone": "", "stage": "guest", "household": "", "first_seen_on": ""},
        ])
        result = runner.invoke(
            args=["import-people", "--church", "journey", "--file", path, "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Nothing written" in result.output

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert db.session.scalars(Person.search(church.id, term="Ghost")).all() == []

    def test_a_duplicate_email_inside_one_file_is_caught(self, app, tmp_path):
        runner = app.test_cli_runner()
        path = self._write(tmp_path, [
            {"first_name": "A", "last_name": "One", "email": "same@example.com",
             "phone": "", "stage": "guest", "household": "", "first_seen_on": ""},
            {"first_name": "B", "last_name": "Two", "email": "same@example.com",
             "phone": "", "stage": "guest", "household": "", "first_seen_on": ""},
        ])
        result = runner.invoke(args=["import-people", "--church", "journey", "--file", path])
        assert result.exit_code != 0
        assert "twice" in result.output

    def test_importing_twice_updates_rather_than_duplicates(self, app, db, tmp_path):
        runner = app.test_cli_runner()
        path = self._write(tmp_path, [
            {"first_name": "Marcus", "last_name": "Webb", "email": "mw@example.com",
             "phone": "573-555-0000", "stage": "guest", "household": "The Webbs",
             "first_seen_on": "2026-05-01"},
        ])
        runner.invoke(args=["import-people", "--church", "journey", "--file", path])
        result = runner.invoke(args=["import-people", "--church", "journey", "--file", path])
        assert "updated 1" in result.output

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert len(db.session.scalars(Person.search(church.id, term="Marcus")).all()) == 1
