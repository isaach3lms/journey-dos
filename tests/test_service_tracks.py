"""One Sunday, several strands.

A church does not run three services on a Sunday morning. It runs one Sunday,
with the main service, kids, and the operations team happening inside it.
Modelling those as three separate services meant three things to create, three
to publish, three to send, and three rows on a dashboard that should have shown
one.

So the date, the name, the publish state and the headcount belong to the
Sunday, and the running order, the volunteers and the open roles belong to a
track. The tests that matter most here are the ones about strands not leaking
into each other: a drag in the kids plan must not reorder the main service, and
a code posted against one strand must not move rows on another.
"""

import pytest

from app.models import (
    ACCEPTED,
    Church,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    ServiceNeed,
    ServiceTrack,
    ServiceType,
    ServiceTypeNeed,
    ServiceTemplateItem,
    Team,
    TeamPosition,
    add_track,
    build_from_type,
    copy_plan,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_sunday(db, journey, days=4, name="Sunday"):
    from datetime import timedelta

    service = Service(church_id=journey.id, name=name,
                      starts_at=utcnow() + timedelta(days=days))
    db.session.add(service)
    db.session.commit()
    return service


def a_type(db, journey, name, items=(), needs=()):
    service_type = ServiceType(church_id=journey.id, name=name)
    db.session.add(service_type)
    db.session.flush()
    for index, title in enumerate(items, start=1):
        db.session.add(ServiceTemplateItem(
            church_id=journey.id, service_type_id=service_type.id,
            position=index, kind="element", title=title, minutes=5,
        ))
    for position, wanted in needs:
        db.session.add(ServiceTypeNeed(
            church_id=journey.id, service_type_id=service_type.id,
            position_id=position.id, wanted=wanted,
        ))
    db.session.commit()
    return service_type


def put_item(db, journey, track, title, position):
    item = ServiceItem(
        church_id=journey.id, service_id=track.service_id, track_id=track.id,
        position=position, kind="element", title=title, minutes=5,
    )
    db.session.add(item)
    db.session.commit()
    return item


class TestASundayAlwaysHasSomewhereToPutAPlan:
    """An invariant, enforced where the row is made.

    Deleting the last strand is refused because a Sunday with none has nowhere
    to hold a running order and no way to add one. A Sunday created without one
    would be in exactly that state from birth.
    """

    def test_a_new_sunday_gets_a_strand(self, db, journey):
        service = a_sunday(db, journey)
        assert len(service.tracks) == 1

    def test_the_strand_is_named_after_the_sunday(self, db, journey):
        service = a_sunday(db, journey, name="Christmas Eve")
        assert service.tracks[0].name == "Christmas Eve"

    def test_a_sunday_built_from_types_gets_one_each_and_no_spare(self, db, journey):
        """The guarantee must not turn a request for three strands into four."""
        types = [a_type(db, journey, n) for n in ("Sunday Service", "Kids", "Operations")]
        service = build_from_type(journey.id, types[0], "Sunday", utcnow(), types=types)
        db.session.commit()
        assert [t.name for t in service.tracks] == ["Sunday Service", "Kids", "Operations"]

    def test_the_last_strand_cannot_be_deleted(self, db, journey, staff):
        service = a_sunday(db, journey)
        track = service.main_track
        page = staff.post(
            f"/services/{service.id}/tracks/{track.id}/delete/",
            data={"confirm": "on"}, headers=H, follow_redirects=True,
        ).data.decode()
        assert "only strand" in page
        assert db.session.get(ServiceTrack, track.id) is not None


class TestWhatBelongsToTheSundayAndWhatBelongsToAStrand:
    def test_the_sunday_sees_every_strands_plan(self, db, journey):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, service.main_track, "Welcome", 1)
        put_item(db, journey, kids, "Story", 1)
        db.session.refresh(service)
        assert {i.title for i in service.items} == {"Welcome", "Story"}

    def test_each_strand_sees_only_its_own(self, db, journey):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, service.main_track, "Welcome", 1)
        put_item(db, journey, kids, "Story", 1)
        db.session.refresh(service)
        assert [i.title for i in service.main_track.items] == ["Welcome"]
        assert [i.title for i in kids.items] == ["Story"]

    def test_the_sunday_runs_as_long_as_its_longest_strand(self, db, journey):
        """They run alongside each other, not one after another. Adding them
        would say a 70 minute service plus 70 minutes of kids takes two hours
        and twenty."""
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, service.main_track, "Welcome", 1)
        put_item(db, journey, service.main_track, "Sermon", 2)
        put_item(db, journey, kids, "Story", 1)
        db.session.refresh(service)
        assert service.total_minutes == 10

    def test_every_strand_clocks_from_the_sunday(self, db, journey):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, service.main_track, "Welcome", 1)
        put_item(db, journey, kids, "Story", 1)
        db.session.refresh(service)
        assert service.main_track.timed_items[0][1] == service.starts_at
        assert kids.timed_items[0][1] == service.starts_at

    def test_one_headcount_for_the_whole_sunday(self, db, journey, staff):
        service = a_sunday(db, journey)
        add_track(service, None, name="Kids")
        db.session.commit()
        staff.post(f"/services/{service.id}/headcount/", data={"headcount": "184"},
                   headers=H)
        db.session.refresh(service)
        assert service.headcount == 184

    def test_publishing_covers_the_whole_sunday(self, db, journey, staff):
        service = a_sunday(db, journey)
        add_track(service, None, name="Kids")
        put_item(db, journey, service.main_track, "Welcome", 1)
        staff.post(f"/services/{service.id}/publish/", headers=H)
        db.session.refresh(service)
        assert service.is_published


class TestStrandsDoNotLeakIntoEachOther:
    """The failures this model exists to prevent."""

    def setup_two(self, db, journey):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        main = service.main_track
        for index, title in enumerate(["A", "B", "C"], start=1):
            put_item(db, journey, main, title, index)
        for index, title in enumerate(["X", "Y"], start=1):
            put_item(db, journey, kids, title, index)
        db.session.refresh(service)
        return service, main, kids

    def test_reordering_one_leaves_the_other_alone(self, db, journey):
        service, main, kids = self.setup_two(db, journey)
        ids = [i.id for i in kids.items]
        assert kids.reorder_items(list(reversed(ids)))
        db.session.commit()
        db.session.refresh(service)
        assert [i.title for i in kids.items] == ["Y", "X"]
        assert [i.title for i in main.items] == ["A", "B", "C"]

    def test_a_strand_refuses_ids_from_another(self, db, journey):
        """A drag in the kids plan naming a row from the main service is not a
        reorder, it is a bug, and it must change nothing."""
        service, main, kids = self.setup_two(db, journey)
        borrowed = [i.id for i in kids.items] + [main.items[0].id]
        assert kids.reorder_items(borrowed) is False
        db.session.refresh(service)
        assert [i.title for i in kids.items] == ["X", "Y"]

    def test_moving_an_item_stays_in_its_own_strand(self, db, journey):
        service, main, kids = self.setup_two(db, journey)
        assert kids.move_item(kids.items[0], 1)
        db.session.commit()
        db.session.refresh(service)
        assert [i.title for i in kids.items] == ["Y", "X"]
        assert [i.title for i in main.items] == ["A", "B", "C"]

    def test_two_strands_can_both_have_a_first_item(self, db, journey):
        """Positions are unique within a strand now, not within a Sunday."""
        service, main, kids = self.setup_two(db, journey)
        assert main.items[0].position == 1
        assert kids.items[0].position == 1

    def test_posting_against_another_sundays_strand_is_a_404(self, db, journey, staff):
        service, _, _ = self.setup_two(db, journey)
        other = a_sunday(db, journey, days=11)
        r = staff.post(f"/services/{service.id}/items/",
                       data={"track": other.main_track.id, "kind": "element",
                             "title": "Sneaky"}, headers=H)
        assert r.status_code == 404

    def test_another_church_cannot_reach_a_strand(self, db, journey, staff):
        service, _, kids = self.setup_two(db, journey)
        r = staff.post(f"/services/{service.id}/tracks/{kids.id}/delete/",
                       data={"confirm": "on"}, headers={"Host": RIVERBEND_HOST})
        assert r.status_code in (302, 404)
        assert db.session.get(ServiceTrack, kids.id) is not None


class TestServingOnMoreThanOneStrand:
    def test_the_same_person_can_serve_on_two(self, db, journey, staff):
        """Somebody can run the sound desk and help in kids. That is not a
        double booking."""
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        person = Person(church_id=journey.id, first_name="Kaela", last_name="Menz",
                        stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.commit()

        for track in (service.main_track, kids):
            staff.post(f"/services/{service.id}/assignments/",
                       data={"track": track.id, "person_id": person.id}, headers=H)
        db.session.refresh(service)
        assert len(service.assignments) == 2

    def test_asking_twice_on_one_strand_is_still_refused(self, db, journey, staff):
        service = a_sunday(db, journey)
        person = Person(church_id=journey.id, first_name="Kaela", last_name="Menz",
                        stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.commit()

        track = service.main_track
        staff.post(f"/services/{service.id}/assignments/",
                   data={"track": track.id, "person_id": person.id}, headers=H)
        page = staff.post(f"/services/{service.id}/assignments/",
                          data={"track": track.id, "person_id": person.id},
                          headers=H, follow_redirects=True).data.decode()
        db.session.refresh(service)
        assert len(service.assignments) == 1
        assert "already" in page.lower()

    def test_open_roles_are_counted_per_strand(self, db, journey):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        team = Team(church_id=journey.id, name="Band")
        db.session.add(team)
        db.session.flush()
        spot = TeamPosition(church_id=journey.id, team_id=team.id, name="Drums")
        db.session.add(spot)
        db.session.flush()
        db.session.add(ServiceNeed(
            church_id=journey.id, service_id=service.id,
            track_id=service.main_track.id, position_id=spot.id,
            position_name="Drums", wanted=2,
        ))
        db.session.commit()
        db.session.refresh(service)

        assert service.main_track.unfilled_count == 2
        assert kids.unfilled_count == 0
        # And the Sunday still answers for itself, which is what the dashboard
        # and the week strip read.
        assert service.unfilled_count == 2


class TestManagingTheStrands:
    def test_a_strand_can_be_added_by_name(self, db, journey, staff):
        service = a_sunday(db, journey)
        staff.post(f"/services/{service.id}/tracks/",
                   data={"name": "Operations"}, headers=H)
        db.session.refresh(service)
        assert [t.name for t in service.tracks][-1] == "Operations"

    def test_a_strand_can_be_added_from_a_type(self, db, journey, staff):
        service = a_sunday(db, journey)
        kids_type = a_type(db, journey, "Kids Service", items=("Welcome", "Story"))
        staff.post(f"/services/{service.id}/tracks/",
                   data={"service_type_id": kids_type.id}, headers=H)
        db.session.refresh(service)
        added = service.tracks[-1]
        assert added.name == "Kids Service"
        assert [i.title for i in added.items] == ["Welcome", "Story"]

    def test_the_template_is_copied_not_referenced(self, db, journey, staff):
        """Editing this week cannot rewrite the template."""
        service = a_sunday(db, journey)
        kids_type = a_type(db, journey, "Kids Service", items=("Welcome",))
        staff.post(f"/services/{service.id}/tracks/",
                   data={"service_type_id": kids_type.id}, headers=H)
        db.session.refresh(service)
        service.tracks[-1].items[0].title = "Changed"
        db.session.commit()
        assert kids_type.template_items[0].title == "Welcome"

    def test_two_strands_cannot_share_a_name(self, db, journey, staff):
        service = a_sunday(db, journey)
        staff.post(f"/services/{service.id}/tracks/", data={"name": "Kids"}, headers=H)
        page = staff.post(f"/services/{service.id}/tracks/", data={"name": "Kids"},
                          headers=H, follow_redirects=True).data.decode()
        db.session.refresh(service)
        assert len(service.tracks) == 2
        assert "already has a strand" in page

    def test_a_strand_can_be_renamed(self, db, journey, staff):
        service = a_sunday(db, journey)
        track = add_track(service, None, name="Kids")
        db.session.commit()
        staff.post(f"/services/{service.id}/tracks/{track.id}/rename/",
                   data={"name": "Kids Church"}, headers=H)
        db.session.refresh(track)
        assert track.name == "Kids Church"

    def test_removing_one_takes_its_plan_and_its_people(self, db, journey, staff):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        item = put_item(db, journey, kids, "Story", 1)
        kept = put_item(db, journey, service.main_track, "Welcome", 1)

        staff.post(f"/services/{service.id}/tracks/{kids.id}/delete/",
                   data={"confirm": "on"}, headers=H)
        assert db.session.get(ServiceItem, item.id) is None
        assert db.session.get(ServiceItem, kept.id) is not None

    def test_removing_one_needs_the_tick_box(self, db, journey, staff):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        staff.post(f"/services/{service.id}/tracks/{kids.id}/delete/", headers=H)
        assert db.session.get(ServiceTrack, kids.id) is not None

    def test_a_member_cannot_add_one(self, db, journey, member):
        assert member.post("/services/1/tracks/", data={"name": "X"},
                           headers=H).status_code == 403


class TestCopyingLastWeek:
    def test_it_matches_strands_by_name(self, db, journey):
        """Last week's kids plan copies onto this week's kids plan."""
        last = a_sunday(db, journey, days=-3, name="Sunday")
        last_kids = add_track(last, None, name="Kids")
        db.session.commit()
        put_item(db, journey, last.main_track, "Welcome", 1)
        put_item(db, journey, last_kids, "Story", 1)

        this = a_sunday(db, journey, days=4, name="Sunday")
        this_kids = add_track(this, None, name="Kids")
        db.session.commit()

        copy_plan(last, this)
        db.session.commit()
        db.session.refresh(this)
        assert [i.title for i in this.main_track.items] == ["Welcome"]
        assert [i.title for i in this.track_named("Kids").items] == ["Story"]

    def test_a_strand_the_source_lacks_is_created(self, db, journey):
        last = a_sunday(db, journey, days=-3)
        last_kids = add_track(last, None, name="Kids")
        db.session.commit()
        put_item(db, journey, last_kids, "Story", 1)

        this = a_sunday(db, journey, days=4)
        copy_plan(last, this)
        db.session.commit()
        db.session.refresh(this)
        assert this.track_named("Kids") is not None

    def test_a_strand_the_source_lacks_is_emptied_not_left(self, db, journey):
        """Otherwise "copy last week" quietly keeps something from a week
        nobody chose."""
        last = a_sunday(db, journey, days=-3)
        db.session.commit()
        put_item(db, journey, last.main_track, "Welcome", 1)

        this = a_sunday(db, journey, days=4)
        ops = add_track(this, None, name="Operations")
        db.session.commit()
        put_item(db, journey, ops, "Stale", 1)

        copy_plan(last, this)
        db.session.commit()
        db.session.refresh(this)
        assert this.track_named("Operations").items == []

    def test_people_are_not_copied(self, db, journey):
        """Who served last week is not who is available this week."""
        last = a_sunday(db, journey, days=-3)
        person = Person(church_id=journey.id, first_name="Kaela", last_name="Menz",
                        stage="member")
        db.session.add(person)
        db.session.commit()
        db.session.add(ServiceAssignment(
            church_id=journey.id, service_id=last.id,
            track_id=last.main_track.id, person_id=person.id,
        ))
        db.session.commit()

        this = a_sunday(db, journey, days=4)
        copy_plan(last, this)
        db.session.commit()
        db.session.refresh(this)
        assert this.assignments == []


class TestTheScreen:
    def test_one_strand_shows_no_tabs(self, db, journey, staff):
        """A church running a single strand should never meet the idea."""
        service = a_sunday(db, journey)
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "tracktab on" not in page

    def test_two_strands_show_tabs(self, db, journey, staff):
        service = a_sunday(db, journey)
        add_track(service, None, name="Kids")
        db.session.commit()
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "tracktab" in page
        assert "Kids" in page

    def test_the_open_strand_is_the_one_asked_for(self, db, journey, staff):
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, kids, "Story", 1)
        put_item(db, journey, service.main_track, "Welcome", 1)

        page = staff.get(f"/services/{service.id}/?track={kids.id}",
                         headers=H).data.decode()
        plan = page[page.index("data-runsheet"):page.index("data-drag-hint")]
        assert "Story" in plan
        assert "Welcome" not in plan

    def test_it_falls_back_to_the_first_strand(self, db, journey, staff):
        service = a_sunday(db, journey)
        add_track(service, None, name="Kids")
        db.session.commit()
        put_item(db, journey, service.main_track, "Welcome", 1)
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "Welcome" in page

    def test_a_strand_from_another_sunday_is_a_404(self, db, journey, staff):
        service = a_sunday(db, journey)
        other = a_sunday(db, journey, days=11)
        r = staff.get(f"/services/{service.id}/?track={other.main_track.id}",
                      headers=H)
        assert r.status_code == 404

    def test_renaming_a_one_strand_sunday_renames_its_strand(self, db, journey, staff):
        """So adding a second strand later does not reveal a name from before
        the rename."""
        service = a_sunday(db, journey, name="Sunday")
        staff.post(f"/services/{service.id}/details/",
                   data={"name": "Christmas Eve", "date": "2026-12-24", "time": "18:00"},
                   headers=H)
        db.session.refresh(service)
        assert service.main_track.name == "Christmas Eve"

    def test_renaming_leaves_a_multi_strand_sunday_alone(self, db, journey, staff):
        service = a_sunday(db, journey, name="Sunday")
        add_track(service, None, name="Kids")
        db.session.commit()
        first = service.main_track.name
        staff.post(f"/services/{service.id}/details/",
                   data={"name": "Christmas Eve", "date": "2026-12-24", "time": "18:00"},
                   headers=H)
        db.session.refresh(service)
        assert service.main_track.name == first
