"""The Services redesign: types, templates, a timed run sheet, and staffing.

The point of the rebuild is that a worship leader should not retype last week.
A type holds the shape, a new service arrives as a real plan, and the screen
answers the two questions actually being asked: when does each thing happen,
and who is still missing.
"""

from datetime import timedelta

import pytest

from app.models import (
    ACCEPTED,
    DECLINED,
    INVITED,
    ITEM_HEADER,
    Church,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    ServiceNeed,
    ServiceTemplateItem,
    ServiceType,
    ServiceTypeNeed,
    Song,
    Team,
    TeamPosition,
    build_from_type,
    copy_plan,
)
from app.models.base import utcnow
from app.timeutil import to_local
from tests.conftest import JOURNEY_HOST


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def positions(db, journey):
    team = Team(church_id=journey.id, name="Worship")
    db.session.add(team)
    db.session.flush()
    made = {}
    for name in ("Acoustic", "Vocals", "Drums"):
        position = TeamPosition(church_id=journey.id, team_id=team.id, name=name)
        db.session.add(position)
        made[name] = position
    db.session.commit()
    return made


@pytest.fixture
def sunday_type(db, journey, positions):
    service_type = ServiceType(church_id=journey.id, name="Sunday Morning")
    db.session.add(service_type)
    db.session.flush()

    plan = [
        ("header", "Pre-service", None),
        ("element", "Welcome and announcements", 5),
        ("header", "Worship", None),
        ("element", "Song slot", 5),
        ("header", "Word", None),
        ("element", "Sermon", 32),
    ]
    for index, (kind, title, minutes) in enumerate(plan, start=1):
        db.session.add(
            ServiceTemplateItem(
                church_id=journey.id, service_type_id=service_type.id,
                position=index, kind=kind, title=title, minutes=minutes,
            )
        )
    db.session.add_all([
        ServiceTypeNeed(church_id=journey.id, service_type_id=service_type.id,
                        position_id=positions["Vocals"].id, wanted=2),
        ServiceTypeNeed(church_id=journey.id, service_type_id=service_type.id,
                        position_id=positions["Drums"].id, wanted=1),
    ])
    db.session.commit()
    return service_type


def a_service(db, journey, days=5, name="Sunday"):
    service = Service(
        church_id=journey.id, name=name, starts_at=utcnow() + timedelta(days=days)
    )
    db.session.add(service)
    db.session.commit()
    return service


class TestServiceTypes:
    def test_a_new_service_arrives_as_a_real_plan(self, db, journey, sunday_type):
        """The single biggest thing this saves every week."""
        service = build_from_type(
            journey.id, sunday_type, "Sunday", utcnow() + timedelta(days=5)
        )
        db.session.commit()

        assert len(service.items) == len(sunday_type.template_items)
        assert [i.title for i in service.items][:2] == ["Pre-service", "Welcome and announcements"]

    def test_the_staffing_comes_across_too(self, db, journey, sunday_type):
        service = build_from_type(journey.id, sunday_type, "Sunday", utcnow())
        db.session.commit()
        assert {n.position_name: n.wanted for n in service.needs} == {
            "Vocals": 2, "Drums": 1
        }

    def test_it_is_a_copy_not_a_reference(self, db, journey, sunday_type):
        """Editing this week cannot rewrite the template, and editing the
        template cannot rewrite a service that already went out."""
        service = build_from_type(journey.id, sunday_type, "Sunday", utcnow())
        db.session.commit()

        service.items[1].title = "Changed this week only"
        db.session.commit()
        assert sunday_type.template_items[1].title == "Welcome and announcements"

        sunday_type.template_items[1].title = "Changed the template"
        db.session.commit()
        db.session.refresh(service)
        assert service.items[1].title == "Changed this week only"

    def test_a_one_off_service_needs_no_type(self, db, journey):
        """Churches genuinely have these."""
        service = build_from_type(journey.id, None, "Funeral", utcnow())
        db.session.commit()
        assert service.service_type_id is None
        assert service.items == []

    def test_creating_one_through_the_screen(self, db, journey, sunday_type, staff):
        staff.post(
            "/services/",
            data={"service_type_id": sunday_type.id, "starts_at": "2026-06-14T09:00"},
            headers={"Host": JOURNEY_HOST},
        )
        service = db.session.scalars(db.select(Service)).one()
        assert service.service_type_id == sunday_type.id
        assert service.items

    def test_a_type_from_another_church_is_ignored(self, db, journey, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = ServiceType(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()

        staff.post(
            "/services/",
            data={"service_type_id": theirs.id, "starts_at": "2026-06-14T09:00"},
            headers={"Host": JOURNEY_HOST},
        )
        service = db.session.scalars(db.select(Service)).one()
        assert service.service_type_id is None

    def test_type_names_are_unique_per_church(self, db, journey):
        for _ in range(2):
            db.session.add(ServiceType(church_id=journey.id, name="Sunday Morning"))
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_the_types_screen_lists_them(self, db, sunday_type, staff):
        r = staff.get("/services/types/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Sunday Morning" in r.data

    def test_a_member_cannot_reach_it(self, member):
        assert member.get(
            "/services/types/", headers={"Host": JOURNEY_HOST}
        ).status_code == 403


class TestTheRunSheet:
    def _plan(self, db, journey, service, rows):
        for index, (kind, title, minutes) in enumerate(rows, start=1):
            db.session.add(
                ServiceItem(
                    church_id=journey.id, service_id=service.id, position=index,
                    kind=kind, title=title, minutes=minutes,
                )
            )
        db.session.commit()

    def test_each_item_knows_when_it_starts(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, [
            ("element", "Welcome", 5),
            ("element", "Offering", 6),
            ("element", "Sermon", 32),
        ])
        db.session.refresh(service)

        times = [at for _, at in service.timed_items]
        assert (times[1] - times[0]).total_seconds() == 5 * 60
        assert (times[2] - times[1]).total_seconds() == 6 * 60

    def test_the_first_item_starts_when_the_service_does(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, [("element", "Welcome", 5)])
        db.session.refresh(service)
        assert service.timed_items[0][1] == service.starts_at

    def test_a_header_takes_no_time(self, db, journey):
        """It labels what follows rather than happening itself."""
        service = a_service(db, journey)
        self._plan(db, journey, service, [
            ("header", "Worship", None),
            ("element", "Welcome", 5),
            ("element", "Offering", 6),
        ])
        db.session.refresh(service)

        items = service.timed_items
        assert items[0][1] is None
        assert items[1][1] == service.starts_at

    def test_times_reflow_when_a_length_changes(self, db, journey):
        """A stored time would go stale the first time somebody added two
        minutes to the welcome."""
        service = a_service(db, journey)
        self._plan(db, journey, service, [
            ("element", "Welcome", 5),
            ("element", "Sermon", 30),
        ])
        db.session.refresh(service)
        before = service.timed_items[1][1]

        service.items[0].minutes = 10
        db.session.commit()
        db.session.refresh(service)
        assert (service.timed_items[1][1] - before).total_seconds() == 5 * 60

    def test_times_reflow_when_the_service_moves(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, [("element", "Welcome", 5)])
        db.session.refresh(service)

        service.starts_at = service.starts_at + timedelta(hours=1)
        db.session.commit()
        db.session.refresh(service)
        assert service.timed_items[0][1] == service.starts_at

    def test_the_end_time(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, [
            ("element", "Welcome", 5), ("element", "Sermon", 30),
        ])
        db.session.refresh(service)
        assert (service.ends_at - service.starts_at).total_seconds() == 35 * 60

    def test_it_renders_in_the_church_zone(self, db, journey, staff):
        service = a_service(db, journey)
        self._plan(db, journey, service, [("element", "Welcome", 5)])
        r = staff.get(f"/services/{service.id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        expected = to_local(service.starts_at, journey).strftime("%-I:%M")
        assert expected.encode() in r.data


class TestReordering:
    def _plan(self, db, journey, service, titles):
        for index, title in enumerate(titles, start=1):
            db.session.add(
                ServiceItem(
                    church_id=journey.id, service_id=service.id, position=index,
                    kind="element", title=title, minutes=5,
                )
            )
        db.session.commit()
        db.session.refresh(service)

    def test_moving_an_item_up(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B", "C"])

        assert service.move_item(service.items[1], -1)
        db.session.commit()
        db.session.refresh(service)
        assert [i.title for i in service.items] == ["B", "A", "C"]

    def test_moving_an_item_down(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B", "C"])

        assert service.move_item(service.items[0], 1)
        db.session.commit()
        db.session.refresh(service)
        assert [i.title for i in service.items] == ["B", "A", "C"]

    def test_the_first_item_cannot_move_up(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B"])
        assert service.move_item(service.items[0], -1) is False

    def test_the_last_item_cannot_move_down(self, db, journey):
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B"])
        assert service.move_item(service.items[-1], 1) is False

    def test_a_swap_does_not_trip_the_unique_constraint(self, db, journey):
        """Two swaps of a unique column need a gap to pass through."""
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B", "C", "D"])

        # Track A by identity: after the first swap it is no longer first,
        # so re-reading items[0] would move a different item each time.
        target = next(i for i in service.items if i.title == "A")
        for _ in range(3):
            service.move_item(target, 1)
            service.renumber()
            db.session.commit()
            db.session.refresh(service)

        assert [i.title for i in service.items] == ["B", "C", "D", "A"]
        assert [i.position for i in service.items] == [1, 2, 3, 4]

    def test_moving_through_the_screen(self, db, journey, staff):
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B"])

        staff.post(
            f"/services/{service.id}/items/{service.items[0].id}/move/",
            data={"direction": "down"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert [i.title for i in service.items] == ["B", "A"]

    def test_deleting_closes_the_gap(self, db, journey, staff):
        """Otherwise positions drift and the next insert lands oddly."""
        service = a_service(db, journey)
        self._plan(db, journey, service, ["A", "B", "C"])

        staff.post(
            f"/services/{service.id}/items/{service.items[1].id}/delete/",
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert [i.position for i in service.items] == [1, 2]

    def test_an_item_from_another_service_is_a_404(self, db, journey, staff):
        first = a_service(db, journey, days=5)
        second = a_service(db, journey, days=12, name="Other")
        self._plan(db, journey, second, ["Theirs"])

        r = staff.post(
            f"/services/{first.id}/items/{second.items[0].id}/move/",
            data={"direction": "up"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404


class TestStaffing:
    def _need(self, db, journey, service, position, wanted):
        need = ServiceNeed(
            church_id=journey.id, service_id=service.id,
            position_id=position.id, position_name=position.name, wanted=wanted,
        )
        db.session.add(need)
        db.session.commit()
        return need

    def _assign(self, db, journey, service, position, status=INVITED, name="A"):
        person = Person(
            church_id=journey.id, first_name=name, last_name="Person", stage="member"
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=service.id, person_id=person.id,
                position_id=position.id, position_name=position.name, status=status,
            )
        )
        db.session.commit()

    def test_an_empty_position_reads_as_short(self, db, journey, positions):
        service = a_service(db, journey)
        self._need(db, journey, service, positions["Vocals"], 2)
        db.session.refresh(service)

        row = service.needs_summary[0]
        assert (row["wanted"], row["filled"], row["short"]) == (2, 0, 2)
        assert service.unfilled_count == 2

    def test_an_invited_person_counts_as_filled_but_not_accepted(
        self, db, journey, positions
    ):
        """Somebody who has not answered is not a gap, and treating them alike
        either panics a leader or hides a real hole."""
        service = a_service(db, journey)
        self._need(db, journey, service, positions["Vocals"], 2)
        self._assign(db, journey, service, positions["Vocals"], INVITED)
        db.session.refresh(service)

        row = service.needs_summary[0]
        assert (row["filled"], row["accepted"], row["short"]) == (1, 0, 1)

    def test_a_decline_reopens_the_slot(self, db, journey, positions):
        service = a_service(db, journey)
        self._need(db, journey, service, positions["Drums"], 1)
        self._assign(db, journey, service, positions["Drums"], DECLINED)
        db.session.refresh(service)

        assert service.needs_summary[0]["short"] == 1
        assert not service.is_fully_staffed

    def test_fully_staffed(self, db, journey, positions):
        service = a_service(db, journey)
        self._need(db, journey, service, positions["Drums"], 1)
        self._assign(db, journey, service, positions["Drums"], ACCEPTED)
        db.session.refresh(service)

        assert service.is_fully_staffed
        assert service.unfilled_count == 0

    def test_a_service_with_no_needs_is_not_reported_as_short(self, db, journey):
        service = a_service(db, journey)
        db.session.refresh(service)
        assert service.unfilled_count == 0
        assert service.is_fully_staffed

    def test_the_need_name_survives_the_position_being_deleted(
        self, db, journey, positions
    ):
        service = a_service(db, journey)
        need = self._need(db, journey, service, positions["Drums"], 1)

        db.session.delete(positions["Drums"])
        db.session.commit()
        db.session.refresh(need)
        assert need.position_name == "Drums"

    def test_the_plan_screen_shows_what_is_short(self, db, journey, positions, staff):
        service = a_service(db, journey)
        self._need(db, journey, service, positions["Vocals"], 2)

        r = staff.get(f"/services/{service.id}/", headers={"Host": JOURNEY_HOST})
        # The panel a leader acts on, now in the sidebar rather than a section
        # further down the page.
        assert b"Open roles" in r.data
        assert b"Vocals" in r.data

    def test_a_need_is_unique_per_position(self, db, journey, positions):
        service = a_service(db, journey)
        for _ in range(2):
            db.session.add(
                ServiceNeed(
                    church_id=journey.id, service_id=service.id,
                    position_id=positions["Drums"].id, position_name="Drums", wanted=1,
                )
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


class TestCopyingAPlan:
    def _plan(self, db, journey, service, titles):
        for index, title in enumerate(titles, start=1):
            db.session.add(
                ServiceItem(
                    church_id=journey.id, service_id=service.id, position=index,
                    kind="element", title=title, minutes=5,
                )
            )
        db.session.commit()
        db.session.refresh(service)

    def test_it_copies_the_running_order(self, db, journey):
        source = a_service(db, journey, days=-7, name="Last week")
        target = a_service(db, journey, days=5)
        self._plan(db, journey, source, ["Welcome", "Song", "Sermon"])

        assert copy_plan(source, target) == 3
        db.session.commit()
        db.session.refresh(target)
        assert [i.title for i in target.items] == ["Welcome", "Song", "Sermon"]

    def test_it_replaces_rather_than_appends(self, db, journey):
        """"Copy last week" means this week looks like last week, not both."""
        source = a_service(db, journey, days=-7, name="Last week")
        target = a_service(db, journey, days=5)
        self._plan(db, journey, source, ["A", "B"])
        self._plan(db, journey, target, ["Old"])

        copy_plan(source, target)
        db.session.commit()
        db.session.refresh(target)
        assert [i.title for i in target.items] == ["A", "B"]

    def test_it_never_copies_who_was_serving(self, db, journey, positions):
        """Last week's team is not this week's, and nobody should find out
        they are playing by reading it."""
        source = a_service(db, journey, days=-7, name="Last week")
        target = a_service(db, journey, days=5)
        self._plan(db, journey, source, ["Welcome"])

        person = Person(
            church_id=journey.id, first_name="Ellie", last_name="Webb", stage="member"
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=source.id, person_id=person.id,
                position_name="Acoustic", status=ACCEPTED,
            )
        )
        db.session.commit()

        copy_plan(source, target)
        db.session.commit()
        db.session.refresh(target)
        assert target.assignments == []

    def test_it_carries_keys_and_notes(self, db, journey):
        source = a_service(db, journey, days=-7, name="Last week")
        target = a_service(db, journey, days=5)
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.flush()
        db.session.add(
            ServiceItem(
                church_id=journey.id, service_id=source.id, position=1, kind="song",
                title="Known", song_id=song.id, key_override="A", notes="Capo 2",
            )
        )
        db.session.commit()
        db.session.refresh(source)

        copy_plan(source, target)
        db.session.commit()
        db.session.refresh(target)
        assert target.items[0].key == "A"
        assert target.items[0].notes == "Capo 2"

    def test_copying_through_the_screen(self, db, journey, staff):
        source = a_service(db, journey, days=-7, name="Last week")
        target = a_service(db, journey, days=5)
        self._plan(db, journey, source, ["Welcome", "Sermon"])

        staff.post(
            f"/services/{target.id}/copy/",
            data={"source_id": source.id},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(target)
        assert len(target.items) == 2

    def test_a_service_cannot_copy_itself(self, db, journey, staff):
        service = a_service(db, journey)
        r = staff.post(
            f"/services/{service.id}/copy/",
            data={"source_id": service.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_a_service_from_another_church_cannot_be_the_source(
        self, db, journey, staff
    ):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Service(
            church_id=riverbend.id, name="Theirs", starts_at=utcnow() - timedelta(days=3)
        )
        db.session.add(theirs)
        db.session.commit()
        target = a_service(db, journey)

        r = staff.post(
            f"/services/{target.id}/copy/",
            data={"source_id": theirs.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400


class TestSections:
    def test_a_header_can_be_added(self, db, journey, staff):
        service = a_service(db, journey)
        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "header", "title": "Worship"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items[0].kind == ITEM_HEADER

    def test_a_header_needs_a_title(self, db, journey, staff):
        service = a_service(db, journey)
        staff.post(
            f"/services/{service.id}/items/",
            data={"kind": "header", "title": "  "},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(service)
        assert service.items == []

    def test_a_header_does_not_add_to_the_running_time(self, db, journey, staff):
        service = a_service(db, journey)
        for kind, title, minutes in (
            ("header", "Worship", None), ("element", "Song", 5),
        ):
            staff.post(
                f"/services/{service.id}/items/",
                data={"kind": kind, "title": title, "minutes": minutes or ""},
                headers={"Host": JOURNEY_HOST},
            )
        db.session.refresh(service)
        assert service.total_minutes == 5


class TestCapoSuggestionsAreWorthPrinting:
    def test_a_capo_of_zero_is_not_shown(self, db, journey):
        """"Key G, capo 0, play G" is noise on the one screen a musician
        reads while setting up."""
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.commit()
        assert all(option.capo > 0 for option in song.capo_options)

    def test_an_awkward_key_still_gets_help(self, db, journey):
        song = Song(church_id=journey.id, title="Known", default_key="Bb")
        db.session.add(song)
        db.session.commit()
        assert any(o.capo == 3 and o.shape == "G" for o in song.capo_options)

    def test_a_plan_item_follows_the_same_rule(self, db, journey):
        service = a_service(db, journey)
        db.session.add(
            ServiceItem(
                church_id=journey.id, service_id=service.id, position=1,
                kind="song", title="Known", key_override="G",
            )
        )
        db.session.commit()
        db.session.refresh(service)
        assert all(option.capo > 0 for option in service.items[0].capo_options)


class TestHowASundayReadsAtAGlance:
    """The week strip and the status pill are what a leader scans first."""

    def _need_and_fill(self, db, journey, service, position, wanted, statuses):
        db.session.add(
            ServiceNeed(
                church_id=journey.id, service_id=service.id,
                position_id=position.id, position_name=position.name, wanted=wanted,
            )
        )
        for index, status in enumerate(statuses):
            person = Person(
                church_id=journey.id, first_name=f"P{index}", last_name="X", stage="member"
            )
            db.session.add(person)
            db.session.flush()
            db.session.add(
                ServiceAssignment(
                    church_id=journey.id, service_id=service.id, person_id=person.id,
                    position_id=position.id, position_name=position.name, status=status,
                )
            )
        db.session.commit()
        db.session.refresh(service)

    def test_an_empty_plan_is_a_draft(self, db, journey):
        service = a_service(db, journey)
        db.session.refresh(service)
        assert service.readiness == "draft"

    def test_a_short_roster_needs_a_team(self, db, journey, positions):
        service = a_service(db, journey)
        self._need_and_fill(db, journey, service, positions["Vocals"], 2, [ACCEPTED])
        assert service.readiness == "needs_team"
        assert service.readiness_label == "Needs team"

    def test_full_but_unanswered_is_not_ready(self, db, journey, positions):
        """A plan where half the team has not replied is not ready, and calling
        it ready is how a leader finds out on Saturday night."""
        service = a_service(db, journey)
        self._need_and_fill(
            db, journey, service, positions["Vocals"], 2, [ACCEPTED, INVITED]
        )
        assert service.readiness == "draft"

    def test_full_and_accepted_is_ready(self, db, journey, positions):
        service = a_service(db, journey)
        self._need_and_fill(
            db, journey, service, positions["Vocals"], 2, [ACCEPTED, ACCEPTED]
        )
        assert service.readiness == "ready"

    def test_a_decline_does_not_count_as_filled(self, db, journey, positions):
        service = a_service(db, journey)
        self._need_and_fill(
            db, journey, service, positions["Vocals"], 2, [ACCEPTED, DECLINED]
        )
        assert service.roles_filled == 1
        assert service.readiness == "needs_team"

    def test_somebody_asked_with_no_listed_position_still_counts(
        self, db, journey
    ):
        """Somebody invited to help with no formal slot is still a role."""
        service = a_service(db, journey)
        person = Person(
            church_id=journey.id, first_name="Helper", last_name="X", stage="member"
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            ServiceAssignment(
                church_id=journey.id, service_id=service.id, person_id=person.id,
                position_name=None, status=ACCEPTED,
            )
        )
        db.session.commit()
        db.session.refresh(service)
        assert service.roles_total == 1
        assert service.readiness == "ready"

    def test_open_roles_lead_with_the_biggest_gap(self, db, journey, positions):
        """The one missing two people matters more than the one missing one."""
        service = a_service(db, journey)
        for name, wanted in (("Vocals", 3), ("Drums", 2)):
            db.session.add(
                ServiceNeed(
                    church_id=journey.id, service_id=service.id,
                    position_id=positions[name].id, position_name=name, wanted=wanted,
                )
            )
        db.session.commit()
        db.session.refresh(service)
        assert [row["position"] for row in service.open_roles] == ["Vocals", "Drums"]

    def test_the_strip_offers_the_next_few_sundays(self, db, journey, staff):
        for week in range(1, 4):
            a_service(db, journey, days=7 * week, name=f"Week {week}")
        service = a_service(db, journey, days=2)

        r = staff.get(f"/services/{service.id}/", headers={"Host": JOURNEY_HOST})
        assert b"Plan ahead" in r.data
        for week in range(1, 4):
            assert f"Week {week}".encode() in r.data

    def test_the_open_service_appears_in_its_own_strip(self, db, journey, staff):
        """Even a service in the past, so opening one never shows a strip it
        is missing from."""
        service = a_service(db, journey, days=-3, name="Last Sunday")
        r = staff.get(f"/services/{service.id}/", headers={"Host": JOURNEY_HOST})
        assert b"Last Sunday" in r.data


class TestChangingAKeyFromThePlan:
    def _song_item(self, db, journey, service, key=None):
        song = Song(church_id=journey.id, title="Known", default_key="G")
        db.session.add(song)
        db.session.flush()
        item = ServiceItem(
            church_id=journey.id, service_id=service.id, position=1,
            kind="song", title="Known", song_id=song.id, key_override=key,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def test_it_changes_this_week_only(self, db, journey, staff):
        """The same song does not sit in the same key every week."""
        service = a_service(db, journey)
        item = self._song_item(db, journey, service)

        staff.post(
            f"/services/{service.id}/items/{item.id}/key/",
            data={"key_override": "A"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(item)
        assert item.key == "A"
        assert item.song.default_key == "G"

    def test_clearing_it_falls_back_to_the_song(self, db, journey, staff):
        service = a_service(db, journey)
        item = self._song_item(db, journey, service, key="A")

        staff.post(
            f"/services/{service.id}/items/{item.id}/key/",
            data={"key_override": ""},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(item)
        assert item.key_override is None
        assert item.key == "G"

    def test_a_nonsense_key_is_refused(self, db, journey, staff):
        service = a_service(db, journey)
        item = self._song_item(db, journey, service)

        r = staff.post(
            f"/services/{service.id}/items/{item.id}/key/",
            data={"key_override": "H"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"is not a key" in r.data
        db.session.refresh(item)
        assert item.key_override is None

    def test_an_item_from_another_service_is_a_404(self, db, journey, staff):
        first = a_service(db, journey, days=5)
        second = a_service(db, journey, days=12, name="Other")
        item = self._song_item(db, journey, second)

        r = staff.post(
            f"/services/{first.id}/items/{item.id}/key/",
            data={"key_override": "A"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404

    def test_charts_are_linked_not_stored(self, db, journey, staff):
        """Charts stay in SongSelect under the church's own CCLI licence."""
        service = a_service(db, journey)
        song = Song(
            church_id=journey.id, title="Known", default_key="G", ccli_number="7070345"
        )
        db.session.add(song)
        db.session.flush()
        db.session.add(
            ServiceItem(
                church_id=journey.id, service_id=service.id, position=1,
                kind="song", title="Known", song_id=song.id,
            )
        )
        db.session.commit()

        r = staff.get(f"/services/{service.id}/", headers={"Host": JOURNEY_HOST})
        assert b"songselect.ccli.com" in r.data
        assert b'rel="noopener noreferrer"' in r.data
        assert b"never store the words" in r.data
