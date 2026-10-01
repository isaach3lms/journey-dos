"""Renaming a service, moving it, and deleting it.

Deleting is the one that needs care. A Sunday entered twice is the usual
reason, the duplicate is often the published one, and everything on the plan
goes with it.
"""

from datetime import date, timedelta

import pytest

from app.models import (
    AuditEvent, Church, Person, Service, ServiceAssignment, ServiceItem,
    ServiceNeed, Team, TeamPosition,
)
from app.models.base import utcnow
from app.models.service import ACCEPTED
from app.timeutil import to_local
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def service(db):
    """A Sunday with a plan, a role, and somebody asked."""
    c = journey(db)
    c.timezone = "America/Chicago"
    team = Team(church_id=c.id, name="Band")
    db.session.add(team)
    db.session.flush()
    vocals = TeamPosition(church_id=c.id, team_id=team.id, name="Vocals")
    person = Person(church_id=c.id, first_name="Nina", last_name="Ibarra", stage="member")
    db.session.add_all([vocals, person])
    db.session.flush()

    s = Service(church_id=c.id, name="Sunday 10:30",
                starts_at=utcnow() + timedelta(days=3))
    db.session.add(s)
    db.session.flush()
    db.session.add(ServiceItem(church_id=c.id, service_id=s.id, track_id=s.main_track.id, position=1,
                               kind="element", title="Welcome"))
    db.session.add(ServiceNeed(church_id=c.id, service_id=s.id, track_id=s.main_track.id, position_id=vocals.id,
                               position_name="Vocals", wanted=2))
    db.session.add(ServiceAssignment(church_id=c.id, service_id=s.id, track_id=s.main_track.id, person_id=person.id,
                                     position_id=vocals.id, position_name="Vocals",
                                     status=ACCEPTED))
    db.session.commit()
    return s


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def save(staff, service, **fields):
    local = to_local(service.starts_at, service.church)
    data = {
        "name": service.name,
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M"),
    }
    data.update(fields)
    return staff.post(f"/services/{service.id}/details/", data=data, headers=H,
                      follow_redirects=True)


def remove(staff, service, confirm=True):
    data = {"confirm": "on"} if confirm else {}
    return staff.post(f"/services/{service.id}/delete/", data=data, headers=H,
                      follow_redirects=True)


class TestRenaming:
    def test_a_new_name(self, db, staff, service):
        response = save(staff, service, name="Christmas Eve")
        assert b"Renamed to Christmas Eve." in response.data
        db.session.refresh(service)
        assert service.name == "Christmas Eve"

    def test_it_shows_everywhere_the_name_does(self, db, staff, service):
        save(staff, service, name="Christmas Eve")
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"Christmas Eve" in page
        assert b"Sunday 10:30" not in page

    def test_renaming_a_published_service(self, db, staff, service):
        """One save, not unpublish, edit and republish."""
        service.publish()
        db.session.commit()
        save(staff, service, name="Christmas Eve")
        db.session.refresh(service)
        assert service.name == "Christmas Eve"
        assert service.is_published is True

    def test_an_empty_name_is_refused(self, db, staff, service):
        response = save(staff, service, name="   ")
        assert b"A service needs a name." in response.data
        db.session.refresh(service)
        assert service.name == "Sunday 10:30"

    def test_a_very_long_name_is_cut(self, db, staff, service):
        save(staff, service, name="S" * 400)
        db.session.refresh(service)
        assert len(service.name) == 160

    def test_what_they_type_is_escaped(self, db, staff, service):
        save(staff, service, name="<script>alert(1)</script>")
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"<script>alert(1)</script>" not in page
        assert b"&lt;script&gt;" in page


class TestMovingIt:
    def test_a_new_date(self, db, staff, service):
        when = (date.today() + timedelta(days=30)).isoformat()
        response = save(staff, service, date=when)
        assert b"Moved to" in response.data
        db.session.refresh(service)
        assert to_local(service.starts_at, journey(db)).date().isoformat() == when

    def test_a_new_time(self, db, staff, service):
        save(staff, service, time="18:45")
        db.session.refresh(service)
        landed = to_local(service.starts_at, journey(db))
        assert (landed.hour, landed.minute) == (18, 45)

    def test_the_time_typed_is_the_churchs_own_clock(self, db, staff, service):
        save(staff, service, time="09:00")
        db.session.refresh(service)
        assert to_local(service.starts_at, journey(db)).strftime("%H:%M") == "09:00"
        assert service.starts_at.hour != 9  # stored UTC, Central is behind

    def test_moving_a_published_service_says_the_team_can_see_it(self, db, staff, service):
        service.publish()
        db.session.commit()
        response = save(staff, service, date=(date.today() + timedelta(days=9)).isoformat())
        assert b"the team sees the new time now" in response.data

    def test_moving_a_draft_does_not_say_that(self, db, staff, service):
        response = save(staff, service, date=(date.today() + timedelta(days=9)).isoformat())
        assert b"Moved to" in response.data
        assert b"the team sees the new time now" not in response.data

    def test_no_date_is_refused(self, db, staff, service):
        before = service.starts_at
        response = save(staff, service, date="")
        assert b"A service needs a date." in response.data
        db.session.refresh(service)
        assert service.starts_at == before

    @pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45"])
    def test_nonsense_is_refused(self, db, staff, service, bad):
        before = service.starts_at
        save(staff, service, date=bad)
        db.session.refresh(service)
        assert service.starts_at == before

    def test_nothing_changed_says_so_quietly(self, db, staff, service):
        response = save(staff, service)
        assert b"Saved." in response.data

    def test_the_plan_is_untouched_by_a_rename(self, db, staff, service):
        save(staff, service, name="Christmas Eve", date=(date.today() + timedelta(days=40)).isoformat())
        db.session.refresh(service)
        assert len(service.items) == 1
        assert len(service.assignments) == 1
        assert len(service.needs) == 1


class TestTheFormOnThePlan:
    def test_it_is_there_with_todays_values(self, db, staff, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        local = to_local(service.starts_at, journey(db))
        assert 'id="details"' in page
        assert f'value="{service.name}"' in page
        assert f'value="{local.date().isoformat()}"' in page
        assert f'value="{local.strftime("%H:%M")}"' in page

    def test_delete_is_folded_away(self, db, staff, service):
        """A delete button next to Save is one somebody hits by accident."""
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        block = page[page.index('class="dangerbox"'):]
        assert "<summary>" in block[:80]
        assert "name=\"confirm\"" in block


class TestDeleting:
    def test_a_draft_goes(self, db, staff, service):
        response = remove(staff, service)
        assert b"Sunday 10:30 on" in response.data
        assert b"is gone." in response.data
        assert db.session.get(Service, service.id) is None

    def test_a_published_one_goes_too(self, db, staff, service):
        """The usual reason to delete is a Sunday entered twice, and the
        duplicate is often the one already published."""
        service.publish()
        db.session.commit()
        service_id = service.id
        remove(staff, service)
        assert db.session.get(Service, service_id) is None

    def test_everything_on_the_plan_goes_with_it(self, db, staff, service):
        service_id = service.id
        remove(staff, service)
        for model in (ServiceItem, ServiceNeed, ServiceAssignment):
            left = db.session.scalars(
                db.select(model).where(model.service_id == service_id)
            ).all()
            assert left == [], model.__name__

    def test_the_people_themselves_are_not_deleted(self, db, staff, service):
        """A volunteer is not part of the plan. Removing a Sunday must not
        remove anybody from the roster."""
        remove(staff, service)
        assert db.session.scalars(
            db.select(Person).where(Person.first_name == "Nina")
        ).all() != []

    def test_it_lands_back_on_the_services_screen(self, db, staff, service):
        response = staff.post(f"/services/{service.id}/delete/",
                              data={"confirm": "on"}, headers=H)
        assert response.headers["Location"].endswith("/services/")

    def test_it_is_off_the_list_afterwards(self, db, staff, service):
        remove(staff, service)
        assert b"Sunday 10:30" not in staff.get("/services/", headers=H).data

    def test_without_the_tick_box_nothing_happens(self, db, staff, service):
        response = remove(staff, service, confirm=False)
        assert b"Tick the box first." in response.data
        assert db.session.get(Service, service.id) is not None

    def test_the_log_remembers_it(self, db, staff, service):
        """The audit entry is the only thing left, so it carries what was
        there."""
        remove(staff, service)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == "service_deleted")
        )
        assert event is not None
        assert "Sunday 10:30" in event.summary
        assert event.actor_name == "Pastor Reed"
        assert "1 items" in (event.detail or "")
        assert "1 people asked" in (event.detail or "")

    def test_the_log_says_whether_it_was_live(self, db, staff, service):
        service.publish()
        db.session.commit()
        remove(staff, service)
        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == "service_deleted")
        )
        assert "Published" in (event.detail or "")

    def test_it_shows_on_the_settings_log(self, db, staff, service):
        remove(staff, service)
        page = staff.get("/settings/?open=audit", headers=H).data
        assert b"Service deleted" in page or b"was deleted" in page


class TestWhoCan:
    def test_a_member_cannot_rename(self, db, client, sign_in, service):
        sign_in("member@journeychurchsemo.com")
        assert client.post(f"/services/{service.id}/details/",
                           data={"name": "Mine", "date": "2026-12-25"},
                           headers=H).status_code == 403

    def test_a_member_cannot_delete(self, db, client, sign_in, service):
        sign_in("member@journeychurchsemo.com")
        assert client.post(f"/services/{service.id}/delete/", data={"confirm": "on"},
                           headers=H).status_code == 403
        assert db.session.get(Service, service.id) is not None

    def test_another_churchs_service_is_a_404(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Service(church_id=other.id, name="Theirs", starts_at=utcnow())
        db.session.add(theirs)
        db.session.commit()
        assert staff.post(f"/services/{theirs.id}/delete/", data={"confirm": "on"},
                          headers=H).status_code == 404
        assert db.session.get(Service, theirs.id) is not None
