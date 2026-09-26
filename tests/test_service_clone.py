"""Cloning a service onto another date.

This replaced "save as a template" on the plan screen. A service type still
carries a running order for brand new services; cloning is the shortcut staff
actually reach for, which is "this Sunday again, next Sunday".
"""

from datetime import date, timedelta

import pytest

from app.models import (
    Church, Person, Service, ServiceAssignment, ServiceItem, ServiceNeed,
    ServiceType, Song, Team, TeamPosition,
)
from app.models.base import utcnow
from app.models.service import ACCEPTED, STATUS_PUBLISHED
from app.timeutil import to_local
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def service(db):
    """A Sunday with a running order, a role it needs, and somebody on it."""
    c = journey(db)
    c.timezone = "America/Chicago"
    song = Song(church_id=c.id, title="Goodness of God")
    team = Team(church_id=c.id, name="Band")
    kind = ServiceType(church_id=c.id, name="Sunday Morning")
    db.session.add_all([song, team, kind])
    db.session.flush()
    vocals = TeamPosition(church_id=c.id, team_id=team.id, name="Vocals")
    person = Person(church_id=c.id, first_name="Nina", last_name="Ibarra", stage="member")
    db.session.add_all([vocals, person])
    db.session.flush()

    s = Service(church_id=c.id, name="Sunday 10:30", service_type_id=kind.id,
                starts_at=utcnow() + timedelta(days=1), notes="Communion week")
    db.session.add(s)
    db.session.flush()
    for i, (item_kind, title, minutes, song_id) in enumerate((
        ("header", "Worship", None, None),
        ("song", "Goodness of God", 5, song.id),
        ("element", "Welcome", 3, None),
        ("element", "Message", 35, None),
    ), start=1):
        db.session.add(ServiceItem(church_id=c.id, service_id=s.id, position=i,
                                   kind=item_kind, title=title, minutes=minutes,
                                   song_id=song_id, notes=f"note {i}",
                                   key_override="G" if item_kind == "song" else None))
    db.session.add(ServiceNeed(church_id=c.id, service_id=s.id, position_id=vocals.id,
                               position_name="Vocals", wanted=2))
    db.session.add(ServiceAssignment(church_id=c.id, service_id=s.id, person_id=person.id,
                                     position_id=vocals.id, position_name="Vocals",
                                     status=ACCEPTED))
    db.session.commit()
    return s


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def clone(staff, service, when=None, time=None):
    if when is None:
        when = (date.today() + timedelta(days=14)).isoformat()
    data = {"date": when}
    if time is not None:
        data["time"] = time
    return staff.post(f"/services/{service.id}/clone/", data=data, headers=H,
                      follow_redirects=True)


def copies(db, service):
    return [s for s in db.session.scalars(db.select(Service)).all() if s.id != service.id]


class TestWhatIsCopied:
    def test_the_running_order(self, db, staff, service):
        response = clone(staff, service)
        assert b"Copied 4 items onto" in response.data

        [copy] = copies(db, service)
        assert [i.title for i in copy.items] == [
            "Worship", "Goodness of God", "Welcome", "Message"]
        assert [i.position for i in copy.items] == [1, 2, 3, 4]

    def test_the_detail_on_each_item(self, db, staff, service):
        clone(staff, service)
        [copy] = copies(db, service)
        song = next(i for i in copy.items if i.kind == "song")
        assert song.minutes == 5
        assert song.song_id == service.items[1].song_id
        assert song.key_override == "G"
        assert song.notes == "note 2"

    def test_the_roles_it_needs(self, db, staff, service):
        clone(staff, service)
        [copy] = copies(db, service)
        assert [(n.position_name, n.wanted) for n in copy.needs] == [("Vocals", 2)]

    def test_the_name_and_the_type(self, db, staff, service):
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.name == "Sunday 10:30"
        assert copy.service_type_id == service.service_type_id

    def test_the_notes(self, db, staff, service):
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.notes == "Communion week"

    def test_it_lands_in_the_same_church(self, db, staff, service):
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.church_id == service.church_id
        assert all(i.church_id == service.church_id for i in copy.items)


class TestWhatIsNotCopied:
    def test_never_the_team(self, db, staff, service):
        """Who served this week is not who is free next week, and a plan that
        arrives pre-filled with names nobody asked is how a volunteer finds
        out they are playing by reading it."""
        assert len(service.assignments) == 1
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.assignments == []

    def test_a_clone_of_a_published_service_is_a_draft(self, db, staff, service):
        service.publish()
        db.session.commit()
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.status != STATUS_PUBLISHED
        assert copy.is_published is False

    def test_the_headcount_stays_behind(self, db, staff, service):
        service.headcount = 212
        db.session.commit()
        clone(staff, service)
        [copy] = copies(db, service)
        assert copy.headcount is None

    def test_the_original_is_untouched(self, db, staff, service):
        clone(staff, service)
        db.session.refresh(service)
        assert len(service.items) == 4
        assert len(service.assignments) == 1


class TestTheDate:
    def test_it_lands_on_the_date_that_was_picked(self, db, staff, service):
        when = (date.today() + timedelta(days=21)).isoformat()
        clone(staff, service, when=when)
        [copy] = copies(db, service)
        assert to_local(copy.starts_at, journey(db)).date().isoformat() == when

    def test_the_time_of_day_carries_over_when_the_box_is_left_alone(
        self, db, staff, service
    ):
        church = journey(db)
        source = to_local(service.starts_at, church)
        clone(staff, service, time="")
        [copy] = copies(db, service)
        landed = to_local(copy.starts_at, church)
        assert (landed.hour, landed.minute) == (source.hour, source.minute)

    def test_a_different_time_is_honoured(self, db, staff, service):
        clone(staff, service, time="18:30")
        [copy] = copies(db, service)
        landed = to_local(copy.starts_at, journey(db))
        assert (landed.hour, landed.minute) == (18, 30)

    def test_the_time_is_the_churchs_own_zone(self, db, staff, service):
        """Stored as UTC, typed as the wall clock in Jackson."""
        church = journey(db)
        when = (date.today() + timedelta(days=10)).isoformat()
        clone(staff, service, when=when, time="09:00")
        [copy] = copies(db, service)
        assert to_local(copy.starts_at, church).strftime("%H:%M") == "09:00"
        # Central time is behind UTC, so the stored hour is later in the day.
        assert copy.starts_at.hour != 9

    def test_a_date_in_the_past_is_allowed(self, db, staff, service):
        """Churches do backfill a Sunday they never entered."""
        when = (date.today() - timedelta(days=7)).isoformat()
        response = clone(staff, service, when=when)
        assert b"Copied 4 items onto" in response.data

    def test_no_date_is_refused(self, db, staff, service):
        response = clone(staff, service, when="")
        assert b"Pick a date for the copy." in response.data
        assert copies(db, service) == []

    @pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "<script>"])
    def test_nonsense_is_refused(self, db, staff, service, bad):
        response = clone(staff, service, when=bad)
        assert copies(db, service) == []
        assert b"<script>alert" not in response.data


class TestTheCardOnThePlan:
    def test_it_is_there(self, db, staff, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"Clone this service" in page
        assert b'id="clone"' in page

    def test_the_old_template_card_is_gone(self, db, staff, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"Save as a template" not in page
        assert b"save-template" not in page

    def test_the_date_defaults_to_a_week_on(self, db, staff, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        expected = (to_local(service.starts_at, journey(db)).date()
                    + timedelta(days=7)).isoformat()
        assert f'value="{expected}"' in page

    def test_the_time_defaults_to_this_services_time(self, db, staff, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        expected = to_local(service.starts_at, journey(db)).strftime("%H:%M")
        assert f'value="{expected}"' in page

    def test_an_empty_plan_says_so_instead_of_offering_a_copy(self, db, staff):
        empty = Service(church_id=journey(db).id, name="Nothing yet",
                        starts_at=utcnow() + timedelta(days=3))
        db.session.add(empty)
        db.session.commit()
        page = staff.get(f"/services/{empty.id}/", headers=H).data
        assert b"a copy would be empty too" in page
        assert b'name="date"' not in page

    def test_it_lands_on_the_copy(self, db, staff, service):
        """Straight into the new plan, which is where the next edit happens."""
        response = staff.post(f"/services/{service.id}/clone/",
                              data={"date": (date.today() + timedelta(days=7)).isoformat()},
                              headers=H)
        [copy] = copies(db, service)
        assert response.headers["Location"].endswith(f"/services/{copy.id}/")


class TestWhoCan:
    def test_a_member_cannot(self, db, client, sign_in, service):
        sign_in("member@journeychurchsemo.com")
        assert client.post(f"/services/{service.id}/clone/",
                           data={"date": "2026-12-25"}, headers=H).status_code == 403

    def test_another_churchs_service_cannot_be_cloned(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Service(church_id=other.id, name="Theirs", starts_at=utcnow())
        db.session.add(theirs)
        db.session.commit()
        assert staff.post(f"/services/{theirs.id}/clone/",
                          data={"date": "2026-12-25"}, headers=H).status_code == 404


class TestTheWordingOfTheConfirmation:
    def test_one_item_is_not_1_items(self, db, staff, service):
        for item in service.items[1:]:
            db.session.delete(item)
        db.session.commit()
        response = clone(staff, service)
        assert b"Copied 1 item onto" in response.data
        assert b"1 items" not in response.data

    def test_several_items(self, db, staff, service):
        response = clone(staff, service)
        assert b"Copied 4 items onto" in response.data

    def test_it_says_the_copy_is_a_draft(self, db, staff, service):
        """Nothing reaches the team until somebody has looked at it."""
        service.publish()
        db.session.commit()
        response = clone(staff, service)
        assert b"draft until you publish it" in response.data
