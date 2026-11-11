"""Dates a volunteer is not available to serve.

A worship leader builds a plan on Thursday and asks six people. Two of them
are away that weekend, and the only way anybody found out was those two
answering no, days later, after the invitation had gone out.

The rule this file exists to pin is the split between the two ways somebody
gets asked:

- The AUTOMATIC path, which is publishing a plan, skips anybody who said they
  are away. Nothing about publishing was a decision to chase one particular
  person, so a machine would otherwise ask them about the one weekend they
  took the trouble to rule out.
- The DELIBERATE path, the "Send the invites" button, does not skip them. A
  leader may know perfectly well that somebody said they were away and want
  to ask anyway, and the alternative is a leader deleting what a volunteer
  wrote about their own weekend in order to ask a question.

Both halves are tested. A change that made blockouts absolute would pass half
of this file and fail the other half, which is the point.
"""

from datetime import date, datetime, timedelta

import pytest

from app.models import (
    Church,
    Person,
    Service,
    ServiceAssignment,
    ServiceItem,
    ServingBlockout,
    User,
    add_track,
)
from app.models.base import utcnow
from app.timeutil import from_local
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


@pytest.fixture
def alicia(db, journey):
    """A member with a sign-in, so both sides of this can be driven."""
    user = db.session.scalar(
        db.select(User).where(User.email == "member@journeychurchsemo.com")
    )
    person = Person(church_id=journey.id, first_name="Alicia", last_name="Romero",
                    email=user.email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return person


# A fixed Sunday, so every assertion can name a date rather than compute one.
SUNDAY = datetime(2026, 11, 1, 9, 0)


def a_sunday(db, journey, at=SUNDAY, published=False):
    """A Sunday with one line in the plan.

    The line is not decoration. Publishing refuses an empty plan, and half of
    this file is about what publishing does, so without it those tests would
    pass by publishing nothing at all.
    """
    service = Service(church_id=journey.id, name="Sunday",
                      starts_at=from_local(at, journey))
    if published:
        service.publish()
    db.session.add(service)
    db.session.flush()
    db.session.add(ServiceItem(
        church_id=journey.id, service_id=service.id,
        track_id=service.main_track.id, position=1, kind="element",
        title="Welcome", minutes=5,
    ))
    db.session.commit()
    return service


def block(db, journey, person, start, end=None, note=None):
    record = ServingBlockout(
        church_id=journey.id, person_id=person.id,
        starts_on=start, ends_on=end or start, note=note,
    )
    db.session.add(record)
    db.session.commit()
    return record


class TestWhatARangeMeans:
    def test_the_last_day_is_included(self, db, journey, alicia):
        """"Away until the 19th" means the 19th. A range that quietly excluded
        its last day would send an invitation on the day somebody flies home,
        which is the most annoying version of this bug there is."""
        away = block(db, journey, alicia, date(2026, 7, 5), date(2026, 7, 19))
        assert away.covers(date(2026, 7, 19)) is True
        assert away.covers(date(2026, 7, 20)) is False

    def test_the_first_day_is_included(self, db, journey, alicia):
        away = block(db, journey, alicia, date(2026, 7, 5), date(2026, 7, 19))
        assert away.covers(date(2026, 7, 5)) is True
        assert away.covers(date(2026, 7, 4)) is False

    def test_one_day_is_a_range_of_one(self, db, journey, alicia):
        away = block(db, journey, alicia, date(2026, 7, 5))
        assert away.is_one_day is True
        assert away.covers(date(2026, 7, 5)) is True


class TestWhatTheVolunteerSees:
    def test_they_can_say_they_are_away(self, db, journey, alicia, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        page = client.post("/me/serve/away/", headers=H, follow_redirects=True,
                           data={"starts_on": "2027-07-05", "ends_on": "2027-07-19",
                                 "note": "Out of town"})
        assert page.status_code == 200

        saved = db.session.scalars(
            ServingBlockout.for_person(journey.id, alicia.id, today=date(2026, 1, 1))
        ).all()
        assert len(saved) == 1
        assert saved[0].starts_on == date(2027, 7, 5)
        assert saved[0].ends_on == date(2027, 7, 19)
        assert saved[0].note == "Out of town"

    def test_leaving_the_second_box_empty_means_one_day(self, db, journey, alicia,
                                                        client, sign_in):
        """Most of these are one Sunday, and asking somebody to enter the same
        date twice for the common case is how a form stops being used."""
        sign_in("member@journeychurchsemo.com")
        client.post("/me/serve/away/", headers=H, follow_redirects=True,
                    data={"starts_on": "2027-07-05", "ends_on": ""})

        saved = db.session.scalar(
            ServingBlockout.for_person(journey.id, alicia.id, today=date(2026, 1, 1))
        )
        assert saved.is_one_day is True

    def test_a_backwards_range_is_refused(self, db, journey, alicia, client, sign_in):
        """Not a short blockout. A typo, and one that would silently cover no
        days at all, so the volunteer would think they had told us."""
        sign_in("member@journeychurchsemo.com")
        page = client.post("/me/serve/away/", headers=H, follow_redirects=True,
                           data={"starts_on": "2027-07-19", "ends_on": "2027-07-05"})
        assert page.status_code == 200
        assert db.session.scalars(
            ServingBlockout.for_person(journey.id, alicia.id, today=date(2026, 1, 1))
        ).all() == []

    def test_rubbish_dates_are_refused(self, db, journey, alicia, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        client.post("/me/serve/away/", headers=H, follow_redirects=True,
                    data={"starts_on": "not a date"})
        assert db.session.scalars(
            ServingBlockout.for_person(journey.id, alicia.id, today=date(2026, 1, 1))
        ).all() == []

    def test_they_can_take_one_off_again(self, db, journey, alicia, client, sign_in):
        away = block(db, journey, alicia, date(2027, 7, 5))
        sign_in("member@journeychurchsemo.com")
        client.post(f"/me/serve/away/{away.id}/delete/", headers=H,
                    follow_redirects=True)
        assert db.session.get(ServingBlockout, away.id) is None

    def test_the_screen_lists_what_they_said(self, db, journey, alicia, client, sign_in):
        block(db, journey, alicia, date(2027, 7, 5), date(2027, 7, 19),
              note="Out of town")
        sign_in("member@journeychurchsemo.com")
        body = client.get("/me/serve/", headers=H).get_data(as_text=True)
        assert "Out of town" in body
        assert "5 Jul" in body and "19 Jul" in body

    def test_dates_that_have_passed_drop_off(self, db, journey, alicia, client, sign_in):
        """The screen is a list of things that are still true. Keeping every
        holiday somebody ever took would need clearing out by hand."""
        block(db, journey, alicia, date(2020, 7, 5), date(2020, 7, 19),
              note="Long gone")
        sign_in("member@journeychurchsemo.com")
        body = client.get("/me/serve/", headers=H).get_data(as_text=True)
        assert "Long gone" not in body


class TestOnlyYourOwn:
    def test_somebody_else_cannot_remove_yours(self, db, journey, alicia,
                                               client, sign_in):
        """Removing one would put that person back in the rota for a weekend
        they said they were away."""
        away = block(db, journey, alicia, date(2027, 7, 5))

        other_user = db.session.scalar(
            db.select(User).where(User.email == "pastor@journeychurchsemo.com")
        )
        other = Person(church_id=journey.id, first_name="Reed", last_name="Parker",
                       email=other_user.email, stage="member", approved_at=utcnow())
        db.session.add(other)
        db.session.flush()
        other_user.person_id = other.id
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        page = client.post(f"/me/serve/away/{away.id}/delete/", headers=H)
        assert page.status_code == 403
        assert db.session.get(ServingBlockout, away.id) is not None

    def test_another_church_cannot_see_it(self, db, journey, alicia):
        block(db, journey, alicia, date(2027, 7, 5))
        other = db.session.scalar(
            db.select(Church).where(Church.slug != "journey")
        )
        assert ServingBlockout.away_on(other.id, date(2027, 7, 5)) == {}


class TestWhatPublishingDoes:
    """Driven through the publish route rather than by calling the sender.

    Publishing is the automatic path, and what matters is that the rule holds
    at the place it actually happens. Calling `ask_everyone_waiting` directly
    would test the function and leave the route free to stop using it.
    """

    def _on_the_plan(self, db, journey, service, person):
        assignment = ServiceAssignment(
            church_id=journey.id, service_id=service.id,
            track_id=service.main_track.id, person_id=person.id,
            position_name="Drums", status="invited",
        )
        db.session.add(assignment)
        db.session.commit()
        return assignment

    def _publish(self, staff, service):
        page = staff.post(f"/services/{service.id}/publish/", headers=H,
                          follow_redirects=True)
        assert page.status_code == 200
        return page

    def test_publishing_does_not_ask_somebody_who_is_away(self, db, journey,
                                                          alicia, staff):
        """The rule. Nothing about publishing was a decision to chase this one
        person, so a machine must not ask them about the one weekend they took
        the trouble to rule out."""
        service = a_sunday(db, journey)
        assignment = self._on_the_plan(db, journey, service, alicia)
        block(db, journey, alicia, SUNDAY.date())

        self._publish(staff, service)
        db.session.refresh(assignment)
        assert assignment.was_invited is False

    def test_publishing_still_asks_everybody_else(self, db, journey, alicia, staff):
        """The other half. A blockout on one person must not quietly stop the
        plan going out to the rest of the team."""
        service = a_sunday(db, journey)
        away_one = self._on_the_plan(db, journey, service, alicia)
        block(db, journey, alicia, SUNDAY.date())

        reed = Person(church_id=journey.id, first_name="Reed", last_name="Parker",
                      email="reed@example.com", stage="member", approved_at=utcnow())
        db.session.add(reed)
        db.session.commit()
        other = self._on_the_plan(db, journey, service, reed)

        self._publish(staff, service)
        db.session.refresh(away_one)
        db.session.refresh(other)
        assert away_one.was_invited is False
        assert other.was_invited is True

    def test_a_blockout_on_another_weekend_changes_nothing(self, db, journey,
                                                           alicia, staff):
        service = a_sunday(db, journey)
        assignment = self._on_the_plan(db, journey, service, alicia)
        block(db, journey, alicia, SUNDAY.date() + timedelta(days=7))

        self._publish(staff, service)
        db.session.refresh(assignment)
        assert assignment.was_invited is True

    def test_the_date_compared_is_the_churchs_own(self, db, journey, alicia, staff):
        """A Saturday evening service in Missouri is already Sunday in UTC. A
        blockout is about days in the church's calendar, so somebody who
        blocks out Saturday the 31st must be away for a Saturday service.

        The guard below is what makes this test worth having: without a
        service whose UTC date differs from its local one, it would pass
        against an implementation that compared the wrong one.
        """
        service = a_sunday(db, journey, at=datetime(2026, 10, 31, 20, 0))
        assert service.starts_at.date() == date(2026, 11, 1), (
            "this test proves nothing unless UTC disagrees with the church"
        )
        assignment = self._on_the_plan(db, journey, service, alicia)
        block(db, journey, alicia, date(2026, 10, 31))

        self._publish(staff, service)
        db.session.refresh(assignment)
        assert assignment.was_invited is False


class TestWhatALeaderCanStillDo:
    def test_the_invites_button_asks_anyway(self, db, journey, alicia, staff):
        """The deliberate path. A leader who means to ask somebody who said
        they were away still can, without deleting what that person wrote
        about their own weekend."""
        service = a_sunday(db, journey, published=True)
        db.session.add(ServiceAssignment(
            church_id=journey.id, service_id=service.id,
            track_id=service.main_track.id, person_id=alicia.id,
            position_name="Drums", status="invited",
        ))
        db.session.commit()
        block(db, journey, alicia, SUNDAY.date())

        page = staff.post(f"/services/{service.id}/invite/", headers=H,
                          data={"track": service.main_track.id},
                          follow_redirects=True)
        assert page.status_code == 200

        assignment = db.session.scalar(
            db.select(ServiceAssignment).where(
                ServiceAssignment.person_id == alicia.id
            )
        )
        assert assignment.was_invited is True

    def test_assigning_somebody_away_still_puts_them_on_the_plan(
        self, db, journey, alicia, staff
    ):
        """They go on. What is held back is the notification, not the row:
        refusing outright would leave a leader with no way to schedule
        somebody short of deleting their blockout."""
        service = a_sunday(db, journey, published=True)
        block(db, journey, alicia, SUNDAY.date())

        staff.post(f"/services/{service.id}/assignments/", headers=H,
                   follow_redirects=True,
                   data={"track": service.main_track.id, "person_id": alicia.id})

        assignment = db.session.scalar(
            db.select(ServiceAssignment).where(
                ServiceAssignment.person_id == alicia.id
            )
        )
        assert assignment is not None
        # On the plan, not yet asked.
        assert assignment.was_invited is False

    def test_assigning_anybody_else_asks_them_as_before(self, db, journey, staff):
        """The behaviour this must not change. Assigning is asking, and a
        feature about blockouts must not quietly turn that off for the people
        who have not set one."""
        service = a_sunday(db, journey, published=True)
        reed = Person(church_id=journey.id, first_name="Reed", last_name="Parker",
                      email="reed@example.com", stage="member", approved_at=utcnow())
        db.session.add(reed)
        db.session.commit()

        staff.post(f"/services/{service.id}/assignments/", headers=H,
                   follow_redirects=True,
                   data={"track": service.main_track.id, "person_id": reed.id})

        assignment = db.session.scalar(
            db.select(ServiceAssignment).where(ServiceAssignment.person_id == reed.id)
        )
        assert assignment.was_invited is True

    def test_the_leader_is_told_why_nothing_was_sent(self, db, journey, alicia, staff):
        """Silence would read as a bug. The flash says who, which date, and
        which button sends it anyway."""
        service = a_sunday(db, journey, published=True)
        block(db, journey, alicia, SUNDAY.date())

        page = staff.post(f"/services/{service.id}/assignments/", headers=H,
                          follow_redirects=True,
                          data={"track": service.main_track.id,
                                "person_id": alicia.id})
        body = page.get_data(as_text=True)
        assert "Alicia Romero" in body
        assert "1 November" in body
        assert "Send the invites" in body


class TestWhatTheLeaderSees:
    def test_the_picker_says_who_is_away(self, db, journey, alicia, staff):
        """Said beside the name rather than by removing them from the list, so
        the usual case is nobody being asked in the first place."""
        service = a_sunday(db, journey)
        block(db, journey, alicia, SUNDAY.date())

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert "Alicia Romero (away)" in body

    def test_it_says_nothing_about_a_different_weekend(self, db, journey, alicia, staff):
        service = a_sunday(db, journey)
        block(db, journey, alicia, SUNDAY.date() + timedelta(days=7))

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert "Alicia Romero (away)" not in body
        assert "Alicia Romero" in body

    def test_somebody_already_on_the_plan_is_flagged(self, db, journey, alicia, staff):
        """The usual way this happens is the blockout arriving after the plan
        was built, so the warning has to be on the plan and not only in the
        picker a leader has already used."""
        service = a_sunday(db, journey)
        db.session.add(ServiceAssignment(
            church_id=journey.id, service_id=service.id,
            track_id=service.main_track.id, person_id=alicia.id,
            position_name="Drums", status="invited",
        ))
        db.session.commit()
        block(db, journey, alicia, SUNDAY.date())

        body = staff.get(f"/services/{service.id}/", headers=H).get_data(as_text=True)
        assert ">Away</b>" in body
