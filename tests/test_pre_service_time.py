"""The team arrives before the service starts.

A church publishes a service at 9:00 and the band is there at 8:00. The only
way to get that hour into the running order used to be to enter the service
itself as 8:00, which then said 8:00 on the member app, in every invitation,
and on every report. The published start time was wrong in order to make the
plan right.

A line marked `pre_service` is clocked BACKWARDS from the service start rather
than forwards from the top of the plan. That direction is the whole feature:
it means the pre-service lines add up to the time the team arrives, and the
service still starts when the church says it starts. A call time stored as a
number of minutes would have the opposite property, drifting the service off
its own start whenever the setup lines did not happen to add up to it, which
is the bug this is written to make impossible.

What is deliberately NOT tested here as a fact about minutes: nothing asserts
that pre-service minutes equal any particular total. The assertions are about
where the clock lands, because that is what somebody reads off the page.
"""

from datetime import timedelta

import pytest

from app.models import (
    Church,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    ServiceTemplateItem,
    ServiceType,
    add_track,
    build_from_type,
    copy_plan,
)
from app.models.base import utcnow
from app.timeutil import from_local
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_sunday(db, journey, at=None, name="Sunday"):
    """A service at a known local time, so the assertions can name a clock."""
    from datetime import datetime

    local = at or datetime(2026, 11, 1, 9, 0)
    service = Service(church_id=journey.id, name=name,
                      starts_at=from_local(local, journey))
    db.session.add(service)
    db.session.commit()
    return service


def put(db, journey, track, title, minutes, position, pre=False):
    item = ServiceItem(
        church_id=journey.id, service_id=track.service_id, track_id=track.id,
        position=position, kind="element", title=title, minutes=minutes,
        pre_service=pre,
    )
    db.session.add(item)
    db.session.commit()
    return item


def at_of(track, title):
    """The moment one line starts, by its title."""
    return next(at for item, at in track.timed_items if item.title == title)


class TestTheClockRunsBothWays:
    def test_the_service_still_starts_when_it_says_it_does(self, db, journey):
        """The assertion this whole feature exists for. A plan with an hour of
        setting up in it must not move the time the church publishes."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Team arrives", 30, 1, pre=True)
        put(db, journey, track, "Sound check", 30, 2, pre=True)
        put(db, journey, track, "Welcome", 5, 3)

        assert at_of(track, "Welcome") == service.starts_at

    def test_setting_up_ends_as_the_service_begins(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Team arrives", 30, 1, pre=True)
        put(db, journey, track, "Sound check", 30, 2, pre=True)

        assert at_of(track, "Team arrives") == service.starts_at - timedelta(hours=1)
        assert at_of(track, "Sound check") == service.starts_at - timedelta(minutes=30)

    def test_adding_to_the_block_moves_the_arrival_not_the_start(self, db, journey):
        """The property a stored call time would not have.

        A church that adds a ten minute prayer to the pre-service block means
        the team comes ten minutes earlier. It does not mean the service now
        starts at ten to nine.
        """
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Sound check", 30, 1, pre=True)
        put(db, journey, track, "Welcome", 5, 2)
        before = track.call_at

        put(db, journey, track, "Prayer", 10, 3, pre=True)
        db.session.expire(track, ["items"])

        assert track.call_at == before - timedelta(minutes=10)
        assert at_of(track, "Welcome") == service.starts_at

    def test_a_plan_with_nothing_before_it_is_unchanged(self, db, journey):
        """Every Sunday already in the system. The column defaults to False,
        so these must clock exactly as they did before it existed."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Song", 20, 2)

        assert track.call_at is None
        assert at_of(track, "Welcome") == service.starts_at
        assert at_of(track, "Song") == service.starts_at + timedelta(minutes=5)


class TestWhatTheServiceIsSaidToBe:
    def test_the_length_of_a_service_is_not_the_length_of_the_morning(self, db, journey):
        """Folding setup into the running time would say a Sunday that starts
        at 9:00 and ends at 10:10 runs until 11:10 because the band came at
        eight."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Sound check", 60, 1, pre=True)
        put(db, journey, track, "Welcome", 70, 2)

        assert track.total_minutes == 70
        assert track.pre_minutes == 60
        assert service.ends_at == service.starts_at + timedelta(minutes=70)

    def test_the_sundays_call_time_is_the_earliest_strand(self, db, journey):
        """Kids check-in opens before the band finishes setting up, and the
        Sunday's call time is whichever comes first."""
        service = a_sunday(db, journey)
        band = service.main_track
        kids = add_track(service, None, name="Kids")
        db.session.commit()

        put(db, journey, band, "Sound check", 45, 1, pre=True)
        put(db, journey, kids, "Check-in opens", 60, 1, pre=True)

        assert service.call_at == service.starts_at - timedelta(minutes=60)

    def test_a_sunday_with_no_block_anywhere_has_no_call_time(self, db, journey):
        service = a_sunday(db, journey)
        put(db, journey, service.main_track, "Welcome", 5, 1)
        assert service.call_at is None


class TestTheTwoBlocksStayApart:
    def test_the_order_reads_setting_up_first(self, db, journey):
        """Position is what a drag writes, and a pre-service line dragged into
        the middle of the songs would otherwise produce a plan whose times ran
        8:40, 9:00, 8:50 down the page."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Sound check", 30, 2, pre=True)
        put(db, journey, track, "Song", 20, 3)

        assert [i.title for i in track.ordered_items] == [
            "Sound check", "Welcome", "Song",
        ]

    def test_the_times_never_run_backwards_down_the_page(self, db, journey):
        """The assertion behind the one above: whatever the positions say, the
        times a reader sees only ever increase."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Sound check", 30, 2, pre=True)
        put(db, journey, track, "Prayer", 10, 3, pre=True)
        put(db, journey, track, "Song", 20, 4)

        times = [at for _, at in track.timed_items if at is not None]
        assert times == sorted(times)

    def test_a_header_takes_no_time_from_the_block(self, db, journey):
        """A section heading inside the pre-service block must not shift the
        team's arrival."""
        service = a_sunday(db, journey)
        track = service.main_track
        header = ServiceItem(
            church_id=journey.id, service_id=service.id, track_id=track.id,
            position=1, kind="header", title="Setup", pre_service=True,
        )
        db.session.add(header)
        db.session.commit()
        put(db, journey, track, "Sound check", 30, 2, pre=True)

        assert track.call_at == service.starts_at - timedelta(minutes=30)
        assert at_of(track, "Setup") is None


class TestMovingALineBetweenBlocks:
    def test_up_and_down_do_not_cross_the_start(self, db, journey):
        """The arrows move a line within its own block. Crossing changes what
        a line means, and the only visible effect of doing it by nudging would
        be its time jumping an hour, so the switch on the row does that."""
        service = a_sunday(db, journey)
        track = service.main_track
        pre = put(db, journey, track, "Sound check", 30, 1, pre=True)
        first = put(db, journey, track, "Welcome", 5, 2)

        assert track.move_item(pre, +1) is False
        assert track.move_item(first, -1) is False

    def test_they_still_work_inside_a_block(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        a = put(db, journey, track, "Arrive", 10, 1, pre=True)
        put(db, journey, track, "Sound check", 20, 2, pre=True)
        put(db, journey, track, "Welcome", 5, 3)

        assert track.move_item(a, +1) is True
        db.session.commit()
        db.session.expire(track, ["items"])
        assert [i.title for i in track.ordered_items][:2] == ["Sound check", "Arrive"]

    def test_the_switch_renumbers_so_the_arrows_agree(self, db, staff, journey):
        """`ordered_items` already makes the screen right whatever the stored
        positions say. This is about the arrows, which work on position: after
        a line changes block, the two have to be the same thing again."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Song", 20, 2)
        late = put(db, journey, track, "Sound check", 30, 3)

        page = staff.post(
            f"/services/{service.id}/items/{late.id}/pre/",
            data={"track": track.id}, headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        db.session.expire(track, ["items"])

        assert [i.title for i in track.items] == ["Sound check", "Welcome", "Song"]
        assert track.items == track.ordered_items

    def test_the_switch_goes_both_ways(self, db, staff, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        item = put(db, journey, track, "Sound check", 30, 1, pre=True)

        staff.post(f"/services/{service.id}/items/{item.id}/pre/",
                   data={"track": track.id}, headers=H, follow_redirects=True)
        db.session.refresh(item)
        assert item.pre_service is False
        assert track.call_at is None


class TestItSurvivesTheWaysAPlanIsBuilt:
    def test_a_type_carries_its_pre_service_block(self, db, journey):
        """The point of a template is that the block is not rebuilt by hand
        every week. Dropping the flag here would do exactly that, silently."""
        service_type = ServiceType(church_id=journey.id, name="Sunday Service")
        db.session.add(service_type)
        db.session.flush()
        db.session.add(ServiceTemplateItem(
            church_id=journey.id, service_type_id=service_type.id, position=1,
            kind="element", title="Sound check", minutes=45, pre_service=True,
        ))
        db.session.add(ServiceTemplateItem(
            church_id=journey.id, service_type_id=service_type.id, position=2,
            kind="element", title="Welcome", minutes=5,
        ))
        db.session.commit()

        service = build_from_type(journey.id, service_type, "Sunday", utcnow())
        db.session.commit()

        track = service.main_track
        assert track.pre_minutes == 45
        assert at_of(track, "Welcome") == service.starts_at

    def test_copying_last_week_copies_the_block(self, db, journey):
        from datetime import datetime

        source = a_sunday(db, journey)
        put(db, journey, source.main_track, "Sound check", 45, 1, pre=True)
        put(db, journey, source.main_track, "Welcome", 5, 2)

        target = a_sunday(db, journey, at=datetime(2026, 11, 8, 9, 0))
        copy_plan(source, target)
        db.session.commit()

        assert target.main_track.pre_minutes == 45
        assert at_of(target.main_track, "Welcome") == target.starts_at

    def test_adding_one_through_the_form_lands_in_the_block(self, db, staff, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)

        staff.post(f"/services/{service.id}/items/", headers=H, follow_redirects=True,
                   data={"track": track.id, "kind": "element",
                         "title": "Sound check", "minutes": "30",
                         "pre_service": "1"})
        db.session.expire(track, ["items"])

        assert track.pre_minutes == 30
        # At the bottom of the pre-service block, not the bottom of the plan.
        assert [i.title for i in track.items] == ["Sound check", "Welcome"]

    def test_the_plain_add_form_does_not_set_it(self, db, staff, journey):
        """Three add forms post to one route. A missing checkbox must mean
        "in the service", not "whatever the last request said"."""
        service = a_sunday(db, journey)
        track = service.main_track

        staff.post(f"/services/{service.id}/items/", headers=H, follow_redirects=True,
                   data={"track": track.id, "kind": "element", "title": "Welcome",
                         "minutes": "5"})
        db.session.expire(track, ["items"])

        assert track.pre_minutes == 0
        assert track.call_at is None


class TestWhatAVolunteerIsTold:
    def _assignment(self, db, journey, track, person_email="member@journeychurchsemo.com"):
        from app.models import User

        user = db.session.scalar(db.select(User).where(User.email == person_email))
        person = Person(church_id=journey.id, first_name="Alicia", last_name="Romero",
                        email=person_email, stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        assignment = ServiceAssignment(
            church_id=journey.id, service_id=track.service_id, track_id=track.id,
            person_id=person.id, position_name="Drums", status="invited",
        )
        db.session.add(assignment)
        db.session.commit()
        return assignment

    def test_the_invitation_says_when_to_arrive(self, db, journey):
        from app.serving_notify import _arrive_line

        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Sound check", 60, 1, pre=True)
        assignment = self._assignment(db, journey, track)

        assert "8:00" in _arrive_line(assignment, journey)

    def test_it_says_nothing_when_the_church_has_not_said_anything(self, db, journey):
        """A blank line, not a guess. Falling back to the service time would
        tell somebody to be there at a time nobody chose."""
        from app.serving_notify import _arrive_line

        service = a_sunday(db, journey)
        assignment = self._assignment(db, journey, service.main_track)

        assert _arrive_line(assignment, journey) == ""

    def test_it_is_the_time_for_their_own_strand(self, db, journey):
        """Somebody in kids is told when kids opens, not when the band sets
        up. The two are different times on the same Sunday and this is the
        assertion that keeps them apart."""
        from app.serving_notify import _arrive_line

        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()
        put(db, journey, service.main_track, "Sound check", 90, 1, pre=True)
        put(db, journey, kids, "Check-in opens", 15, 1, pre=True)
        assignment = self._assignment(db, journey, kids)

        assert "8:45" in _arrive_line(assignment, journey)

    def test_the_serve_screen_shows_it(self, db, journey, client, sign_in):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Sound check", 60, 1, pre=True)
        self._assignment(db, journey, track)
        service.publish()
        db.session.commit()

        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/serve/", headers=H)
        assert page.status_code == 200
        assert "8:00" in page.get_data(as_text=True)
