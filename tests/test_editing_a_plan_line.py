"""Changing a line that is already in the running order.

Two things a leader could not do, reported from a plan that read
"9:00 Greeting From Team, 1 min":

- **Change it.** Minutes could only be set while adding a line, so turning a
  one minute greeting into a five minute one meant deleting the line and
  adding it again, which lost its place in the order and any notes on it.
- **Pin it.** Every time was calculated, start plus the minutes of everything
  above. A sermon that always begins at 9:45 had no way to say so.

The pin is stored as minutes from the service start rather than a time of
day. Two reasons, both tested here: moving the Sunday moves the pinned line
with it, which is what somebody shifting the whole morning meant, and minutes
carry no timezone, so a pin entered in one half of the year cannot land an
hour out in the other.

A pin cannot be made to agree with the lines above it in every case, and
nothing here tries. When a pin sits before the previous line finishes, both
statements are on screen and the clash is marked. Picking a winner silently
would mean either a stated time quietly moving or a stated length quietly
shrinking, and the person who typed them is the one who knows which is wrong.
"""

from datetime import datetime, timedelta

import pytest

from app.models import Church, Service, ServiceItem, add_track
from app.timeutil import from_local
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
SUNDAY = datetime(2026, 11, 1, 9, 0)


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_sunday(db, journey, at=SUNDAY):
    service = Service(church_id=journey.id, name="Sunday",
                      starts_at=from_local(at, journey))
    db.session.add(service)
    db.session.commit()
    return service


def put(db, journey, track, title, minutes, position, pre=False, offset=None,
        kind="element", notes=None):
    item = ServiceItem(
        church_id=journey.id, service_id=track.service_id, track_id=track.id,
        position=position, kind=kind, title=title, minutes=minutes,
        pre_service=pre, offset_minutes=offset, notes=notes,
    )
    db.session.add(item)
    db.session.commit()
    return item


def at_of(track, title):
    return next(at for item, at in track.timed_items if item.title == title)


class TestChangingALineInPlace:
    def test_its_length_can_be_changed(self, db, journey, staff):
        """The reported case: a greeting entered as one minute."""
        service = a_sunday(db, journey)
        track = service.main_track
        item = put(db, journey, track, "Greeting From Team", 1, 1)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Greeting From Team", "minutes": "5"})
        db.session.refresh(item)
        assert item.minutes == 5

    def test_it_keeps_its_place_in_the_order(self, db, journey, staff):
        """What delete-and-re-add cost. A line edited in place must not jump
        to the bottom of the plan."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        middle = put(db, journey, track, "Worship", 20, 2)
        put(db, journey, track, "Message", 30, 3)

        staff.post(f"/services/{service.id}/items/{middle.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Worship set", "minutes": "25"})
        db.session.expire(track, ["items"])

        assert [i.title for i in track.items] == ["Welcome", "Worship set", "Message"]

    def test_its_name_and_notes_can_be_changed(self, db, journey, staff):
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Welcom", 5, 1, notes="old")

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Welcome", "minutes": "5",
                         "notes": "Pastor Reed opens"})
        db.session.refresh(item)
        assert item.title == "Welcome"
        assert item.notes == "Pastor Reed opens"

    def test_a_blank_length_clears_it(self, db, journey, staff):
        """A line nobody has estimated yet is a real state, and it reads
        better than a guessed zero."""
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Open prayer", 5, 1)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Open prayer", "minutes": ""})
        db.session.refresh(item)
        assert item.minutes is None

    def test_a_song_keeps_the_librarys_title(self, db, journey, staff):
        """Renaming the line would make the plan disagree with the song it
        was chosen from, and the next person to look up what was sung would
        find a title nobody in the library has."""
        from app.models import Song

        song = Song(church_id=journey.id, title="Great Is Thy Faithfulness")
        db.session.add(song)
        db.session.commit()

        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, song.title, 5, 1, kind="song")
        item.song_id = song.id
        db.session.commit()

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Something else", "minutes": "6"})
        db.session.refresh(item)
        assert item.title == "Great Is Thy Faithfulness"
        assert item.minutes == 6

    def test_a_line_cannot_be_left_nameless(self, db, journey, staff):
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Welcome", 5, 1)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True, data={"title": "  ", "minutes": "5"})
        db.session.refresh(item)
        assert item.title == "Welcome"

    def test_another_churchs_line_is_not_reachable(self, db, journey, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        theirs = Service(church_id=other.id, name="Sunday",
                         starts_at=from_local(SUNDAY, other))
        db.session.add(theirs)
        db.session.commit()
        item = put(db, other, theirs.main_track, "Theirs", 5, 1)

        page = staff.post(f"/services/{theirs.id}/items/{item.id}/", headers=H,
                          data={"title": "Mine", "minutes": "9"})
        assert page.status_code == 404
        db.session.refresh(item)
        assert item.title == "Theirs"


class TestPinningALineToTheClock:
    def test_a_pinned_line_starts_when_it_says(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Message", 30, 2, offset=45)

        assert at_of(track, "Message") == service.starts_at + timedelta(minutes=45)

    def test_what_is_above_it_cannot_push_it(self, db, journey):
        """The whole point. Adding ten minutes of worship does not move a
        sermon that always starts at 9:45."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        pinned = put(db, journey, track, "Message", 30, 3, offset=45)
        before = at_of(track, "Message")

        put(db, journey, track, "Extra song", 10, 2)
        db.session.expire(track, ["items"])

        assert at_of(track, "Message") == before
        assert pinned.offset_minutes == 45

    def test_what_is_below_it_follows_from_it(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Message", 30, 2, offset=45)
        put(db, journey, track, "Response", 10, 3)

        assert at_of(track, "Response") == service.starts_at + timedelta(minutes=75)

    def test_an_unpinned_plan_is_unchanged(self, db, journey):
        """Every plan already in the system. The column is null for all of
        them, so they must clock exactly as they did before it existed."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Worship", 20, 2)

        assert at_of(track, "Welcome") == service.starts_at
        assert at_of(track, "Worship") == service.starts_at + timedelta(minutes=5)

    def test_moving_the_sunday_moves_the_pin_with_it(self, db, journey):
        """Stored as minutes from the start, not a time of day. Somebody who
        shifts the whole morning by half an hour means the sermon too; a
        stored 9:45 would end up sitting before lines that now start after
        it."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Welcome", 5, 1)
        put(db, journey, track, "Message", 30, 2, offset=45)

        service.starts_at = from_local(datetime(2026, 11, 1, 9, 30), journey)
        db.session.commit()
        db.session.expire(track, ["items"])

        assert at_of(track, "Message") == service.starts_at + timedelta(minutes=45)

    def test_the_pre_service_block_can_be_pinned_too(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Team arrives", 15, 1, pre=True, offset=-60)
        put(db, journey, track, "Sound check", 30, 2, pre=True)
        put(db, journey, track, "Welcome", 5, 3)

        assert at_of(track, "Team arrives") == service.starts_at - timedelta(minutes=60)
        # Flows forward from the pin, not backward from the service start.
        assert at_of(track, "Sound check") == service.starts_at - timedelta(minutes=45)
        # And the service still starts when it says it does.
        assert at_of(track, "Welcome") == service.starts_at

    def test_lines_above_a_pin_are_measured_back_from_it(self, db, journey):
        """The third rule in `_place`. A line with nothing above it to follow
        takes its time from the first fixed point below it."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Team arrives", 15, 1, pre=True)
        put(db, journey, track, "Sound check", 30, 2, pre=True, offset=-45)

        assert at_of(track, "Sound check") == service.starts_at - timedelta(minutes=45)
        assert at_of(track, "Team arrives") == service.starts_at - timedelta(minutes=60)

    def test_the_call_time_follows_the_pin(self, db, journey):
        """What a volunteer is told has to be what the plan shows, so it is
        read off the placed lines rather than subtracted from the start."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Team arrives", 15, 1, pre=True, offset=-90)
        put(db, journey, track, "Sound check", 30, 2, pre=True)

        assert track.call_at == service.starts_at - timedelta(minutes=90)


class TestWhenAPinDisagreesWithTheLinesAboveIt:
    def test_the_clash_is_reported(self, db, journey):
        """9:00 plus a fifty minute worship set reaches 9:50, and the sermon
        says 9:45. Both are on the screen and the row is marked."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Worship", 50, 1)
        clashing = put(db, journey, track, "Message", 30, 2, offset=45)

        assert clashing.id in track.clashes

    def test_a_pin_with_room_before_it_is_not(self, db, journey):
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Worship", 20, 1)
        fine = put(db, journey, track, "Message", 30, 2, offset=45)

        assert fine.id not in track.clashes

    def test_the_pin_still_wins_on_screen(self, db, journey):
        """Reported, not resolved. The pinned line shows the time it was
        pinned to, so the person reading can see the two statements that
        cannot both be true."""
        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Worship", 50, 1)
        put(db, journey, track, "Message", 30, 2, offset=45)

        assert at_of(track, "Message") == service.starts_at + timedelta(minutes=45)

    def test_the_plan_screen_marks_it(self, db, journey, staff):
        from app.content import SERVICES

        service = a_sunday(db, journey)
        track = service.main_track
        put(db, journey, track, "Worship", 50, 1)
        put(db, journey, track, "Message", 30, 2, offset=45)

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert SERVICES["pin_clash"] in body


class TestEnteringAPinAsAClockTime:
    def test_a_time_is_stored_as_minutes_from_the_start(self, db, journey, staff):
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Message", 30, 1)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Message", "minutes": "30", "pin_at": "09:45"})
        db.session.refresh(item)
        assert item.offset_minutes == 45

    def test_clearing_the_box_removes_the_pin(self, db, journey, staff):
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Message", 30, 1, offset=45)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Message", "minutes": "30", "pin_at": ""})
        db.session.refresh(item)
        assert item.offset_minutes is None

    def test_a_pre_service_time_reads_as_before_the_service(self, db, journey, staff):
        """8:00 against a 9:00 service is an hour before it, not twenty three
        hours after. Setting up cannot start after the thing it sets up."""
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Team arrives", 15, 1, pre=True)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Team arrives", "minutes": "15",
                         "pin_at": "08:00"})
        db.session.refresh(item)
        assert item.offset_minutes == -60

    def test_a_pre_service_time_after_the_start_means_the_night_before(
        self, db, journey, staff
    ):
        """A church setting up at 11:30pm on Saturday for a 9:00 Sunday means
        the night before. Reading it as the day after would put setting up
        fifteen hours after the service."""
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Stage build", 60, 1, pre=True)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Stage build", "minutes": "60",
                         "pin_at": "23:30"})
        db.session.refresh(item)
        assert item.offset_minutes == -570      # 9.5 hours before 9:00

    def test_rubbish_is_refused_and_nothing_changes(self, db, journey, staff):
        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Message", 30, 1, offset=45)

        staff.post(f"/services/{service.id}/items/{item.id}/", headers=H,
                   follow_redirects=True,
                   data={"title": "Message", "minutes": "30",
                         "pin_at": "not a time"})
        db.session.refresh(item)
        assert item.offset_minutes == 45

    def test_moving_a_line_between_blocks_drops_its_pin(self, db, journey, staff):
        """"Starts at 9:45" means something on one side of the service start
        and nothing on the other. Keeping the clock time would put setting up
        after the service; flipping the sign would invent a time nobody
        typed."""
        from app.content import SERVICES

        service = a_sunday(db, journey)
        item = put(db, journey, service.main_track, "Message", 30, 1, offset=45)

        page = staff.post(f"/services/{service.id}/items/{item.id}/pre/",
                          headers=H, follow_redirects=True)
        db.session.refresh(item)
        assert item.pre_service is True
        assert item.offset_minutes is None
        assert SERVICES["pin_dropped"] in page.get_data(as_text=True)


class TestTheControlsAreFindable:
    """The pre-service block shipped and could not be found.

    Every verb on a row lived in a strip that was invisible until the row was
    hovered, so on a laptop you had to already know "Move before the service"
    was there. They are in a panel the row opens now, and a plan with nothing
    before the service carries one line naming the feature.
    """

    def test_a_row_opens_rather_than_hiding_its_verbs(self, db, journey, staff):
        service = a_sunday(db, journey)
        put(db, journey, service.main_track, "Welcome", 5, 1)

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert 'class="rowbox"' in body
        assert 'class="rline"' in body

    def test_nothing_is_hidden_behind_a_hover_any_more(self):
        """The rule that made them unfindable. Its absence is the fix, so its
        absence is what is checked."""
        from pathlib import Path

        css = (Path(__file__).resolve().parents[1]
               / "app" / "static" / "css" / "app.css").read_text()
        assert ".rrow .ractions,.rsection .ractions{opacity:0" not in css

    def test_the_move_control_is_in_the_markup_without_hovering(self, db, journey, staff):
        from app.content import SERVICES

        service = a_sunday(db, journey)
        put(db, journey, service.main_track, "Welcome", 5, 1)

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert SERVICES["item_pre_to"] in body

    def test_a_plan_with_no_pre_service_block_says_the_feature_exists(
        self, db, journey, staff
    ):
        from app.content import SERVICES

        service = a_sunday(db, journey)
        put(db, journey, service.main_track, "Welcome", 5, 1)

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert SERVICES["pre_open"] in body

    def test_and_stops_saying_it_once_the_block_is_used(self, db, journey, staff):
        """One line of furniture is worth it for a church that has not found
        the feature. Two would be noise for one that has."""
        from app.content import SERVICES

        service = a_sunday(db, journey)
        put(db, journey, service.main_track, "Sound check", 30, 1, pre=True)
        put(db, journey, service.main_track, "Welcome", 5, 2)

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert SERVICES["pre_open"] not in body


class TestItSurvivesTheWaysAPlanIsBuilt:
    def test_copying_last_week_carries_the_pins(self, db, journey):
        from app.models import copy_plan

        source = a_sunday(db, journey)
        put(db, journey, source.main_track, "Welcome", 5, 1)
        put(db, journey, source.main_track, "Message", 30, 2, offset=45)

        target = a_sunday(db, journey, at=datetime(2026, 11, 8, 9, 0))
        copy_plan(source, target)
        db.session.commit()

        assert at_of(target.main_track, "Message") == (
            target.starts_at + timedelta(minutes=45)
        )

    def test_a_second_strand_keeps_its_own_pins(self, db, journey):
        """A pin on the kids plan must not move the main service."""
        service = a_sunday(db, journey)
        kids = add_track(service, None, name="Kids")
        db.session.commit()

        put(db, journey, service.main_track, "Welcome", 5, 1)
        put(db, journey, kids, "Kids dismissed", 5, 1, offset=20)

        assert at_of(service.main_track, "Welcome") == service.starts_at
        assert at_of(kids, "Kids dismissed") == service.starts_at + timedelta(minutes=20)
