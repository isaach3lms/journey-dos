"""Telling a household who is in a room and who has left it.

Check-in and check-out notified nobody at all. Check-in is arguably survivable
without one, because the parent walks away from the desk holding the tag.
Check-out is not: a grandparent collects a child at 11:40 and the parent in
the service finds out by walking to an empty room.

The test that matters most in this file is the one asserting the pickup code
is in none of it. It is a live credential for removing a child from a room, a
push payload is handed to a company that is not us and rendered on a lock
screen in a crowded lobby, and the safety model of this whole feature rests
on that code travelling on paper to one person. See app/pickup.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.kids_notify import tell_checked_in, tell_checked_out
from app.models import (
    Checkin,
    CheckinSession,
    Church,
    Household,
    OutboxMessage,
    Person,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def years_ago(years):
    return date.today().replace(year=date.today().year - years)


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def family(db, journey):
    """Two parents, a kid, and a youth, which is the shape that matters.

    The youth is here because a fifteen year old is a child on this roster and
    must not be told about their own check-in, while the adults must.
    """
    household = Household(church_id=journey.id, name="The Webbs")
    db.session.add(household)
    db.session.flush()
    db.session.add_all([
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               email="marcus@journey.test", stage="member",
               household_id=household.id, approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Dana", last_name="Webb",
               email="dana@journey.test", stage="member",
               household_id=household.id, approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Ellie", last_name="Webb",
               stage="member", household_id=household.id, is_child=True,
               birthdate=years_ago(7), email="ellie@journey.test"),
        Person(church_id=journey.id, first_name="Theo", last_name="Webb",
               stage="member", household_id=household.id, is_child=True,
               birthdate=years_ago(15), email="theo@journey.test"),
    ])
    db.session.commit()
    return household


@pytest.fixture
def sunday(db, journey):
    session = CheckinSession(church_id=journey.id, name="Sunday 9:30",
                             starts_at=utcnow())
    db.session.add(session)
    db.session.commit()
    return session


def child(family, first):
    return next(p for p in family.members if p.first_name == first)


def checked_in(db, journey, family, sunday, people, *, room="Kids 1"):
    code = sunday.issue_pickup_code(family.id)
    rows = []
    for person in people:
        row = Checkin(church_id=journey.id, session_id=sunday.id,
                      person_id=person.id, household_id=family.id,
                      household_name=family.name, pickup_code=code, room=room)
        db.session.add(row)
        rows.append(row)
    db.session.commit()
    return code, rows


def kids_mail(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.category == "kids_checkin",
        )
    ).all()


class TestWhoIsTold:
    def test_both_parents_are_told(self, app, db, journey, family, sunday):
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])

        with app.test_request_context(headers=H):
            told = tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert told == 2
        assert {m.to_email for m in kids_mail(db, journey)} == {
            "marcus@journey.test", "dana@journey.test"
        }

    def test_the_child_is_not_told_about_themselves(
        self, app, db, journey, family, sunday
    ):
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert "ellie@journey.test" not in {m.to_email for m in kids_mail(db, journey)}

    def test_a_youth_is_not_told_about_their_own_check_in(
        self, app, db, journey, family, sunday
    ):
        """A fifteen year old is a child on this roster. The notification is
        for whoever is responsible for them, which is not them."""
        theo = child(family, "Theo")
        _, rows = checked_in(db, journey, family, sunday, [theo], room="Youth")

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [theo], rows)
        db.session.commit()

        assert "theo@journey.test" not in {m.to_email for m in kids_mail(db, journey)}

    def test_another_household_is_not_told(self, app, db, journey, family, sunday):
        other = Household(church_id=journey.id, name="The Brandts")
        db.session.add(other)
        db.session.flush()
        db.session.add(Person(church_id=journey.id, first_name="Nia",
                              last_name="Brandt", email="nia@journey.test",
                              stage="member", household_id=other.id,
                              approved_at=utcnow()))
        db.session.commit()

        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert "nia@journey.test" not in {m.to_email for m in kids_mail(db, journey)}

    def test_an_archived_parent_is_not_told(self, app, db, journey, family, sunday):
        marcus = next(p for p in family.members if p.first_name == "Marcus")
        marcus.is_archived = True
        db.session.commit()

        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        with app.test_request_context(headers=H):
            told = tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert told == 1
        assert "marcus@journey.test" not in {m.to_email for m in kids_mail(db, journey)}


class TestThePickupCodeIsNeverInIt:
    """The one that matters. A code on a lock screen in a lobby is a code
    anybody standing nearby can use to take a child out of a room."""

    def test_check_in_never_carries_the_code(
        self, app, db, journey, family, sunday
    ):
        ellie = child(family, "Ellie")
        code, rows = checked_in(db, journey, family, sunday, [ellie])

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        for message in kids_mail(db, journey):
            assert code not in message.body_text
            assert code not in message.subject

    def test_check_out_never_carries_the_code(
        self, app, db, journey, family, sunday
    ):
        ellie = child(family, "Ellie")
        code, rows = checked_in(db, journey, family, sunday, [ellie])
        for row in rows:
            row.check_out(collected_by="Grandma Webb")
        db.session.commit()

        with app.test_request_context(headers=H):
            tell_checked_out(journey, rows, collected_by="Grandma Webb")
        db.session.commit()

        for message in kids_mail(db, journey):
            assert code not in message.body_text

    def test_the_household_pin_is_never_in_it_either(
        self, app, db, journey, family, sunday
    ):
        """A different secret with a different job. The forgot-PIN screen
        exists for that one and somebody has to ask for it."""
        pin = family.ensure_checkin_pin()
        db.session.commit()
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        for message in kids_mail(db, journey):
            assert pin not in message.body_text


class TestWhatItSays:
    def test_the_room_is_in_it(self, app, db, journey, family, sunday):
        """It is the answer to "where do I go to get them"."""
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie], room="Kids 1")

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert "Kids 1" in kids_mail(db, journey)[0].body_text

    def test_two_children_read_as_a_list(self, app, db, journey, family, sunday):
        ellie, theo = child(family, "Ellie"), child(family, "Theo")
        _, rows = checked_in(db, journey, family, sunday, [ellie, theo])

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie, theo], rows)
        db.session.commit()

        subject = kids_mail(db, journey)[0].subject
        assert "Ellie and Theo" in subject

    def test_no_room_recorded_does_not_leave_a_dangling_sentence(
        self, app, db, journey, family, sunday
    ):
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie], room=None)

        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        body = kids_mail(db, journey)[0].body_text
        assert "Room:" not in body
        assert "None" not in body

    def test_who_collected_them_is_in_the_check_out(
        self, app, db, journey, family, sunday
    ):
        """Often a grandparent who is not on the roster, which is exactly the
        case worth telling a parent sitting in the service about."""
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        for row in rows:
            row.check_out(collected_by="Grandma Webb")
        db.session.commit()

        with app.test_request_context(headers=H):
            tell_checked_out(journey, rows, collected_by="Grandma Webb")
        db.session.commit()

        assert "Grandma Webb" in kids_mail(db, journey)[0].body_text

    def test_nobody_recorded_says_so_rather_than_inventing_one(
        self, app, db, journey, family, sunday
    ):
        """The staff-list check-out route deliberately records no name,
        because the staff member did not collect the child."""
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        for row in rows:
            row.check_out(collected_by=None)
        db.session.commit()

        with app.test_request_context(headers=H):
            tell_checked_out(journey, rows, collected_by=None)
        db.session.commit()

        body = kids_mail(db, journey)[0].body_text
        assert "Nobody was recorded" in body
        assert "None" not in body


class TestTheVolunteerAtTheDeskIsNotTold:
    def test_a_parent_checking_in_their_own_child_is_not_told_they_did(
        self, app, db, journey, family, sunday
    ):
        from app.models import User

        marcus = next(p for p in family.members if p.first_name == "Marcus")
        user = User(church_id=journey.id, email="marcus-login@journey.test",
                    name="Marcus Webb", role="leader", is_active_account=True,
                    person_id=marcus.id)
        user.set_password("a-long-enough-password")
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.commit()

        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        with app.test_request_context(headers=H):
            told = tell_checked_in(journey, family, [ellie], rows, by_user=user)
        db.session.commit()

        assert told == 1
        assert "marcus@journey.test" not in {m.to_email for m in kids_mail(db, journey)}

    def test_a_volunteer_from_another_family_does_not_reduce_who_is_told(
        self, app, db, journey, family, sunday
    ):
        from app.models import User

        user = db.session.scalar(
            db.select(User).where(User.email == "leader@journeychurchsemo.com")
        )
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        with app.test_request_context(headers=H):
            told = tell_checked_in(journey, family, [ellie], rows, by_user=user)
        db.session.commit()

        assert told == 2


class TestItNeverTakesDownTheKiosk:
    """A printer queue and a family waiting at the desk."""

    def test_a_push_failure_does_not_raise(
        self, app, db, journey, family, sunday, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise RuntimeError("the push provider is having an afternoon")

        # Was `app.notify.send_to_person`, back when the kiosk sent the push
        # itself and a hung provider held up the desk. It queues now, so the
        # failure worth surviving here is the queuing.
        monkeypatch.setattr("app.notify.enqueue", explode)
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])

        with app.test_request_context(headers=H):
            told = tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert told == 2, "a push failure swallowed the email"

    def test_no_household_tells_nobody_and_does_not_raise(
        self, app, db, journey, sunday
    ):
        """A child with no household can be on the roster. They cannot be
        checked in through the family screen, but nothing here may assume."""
        with app.test_request_context(headers=H):
            assert tell_checked_in(journey, None, [], []) == 0

    def test_a_household_of_only_children_tells_nobody(
        self, app, db, journey, sunday
    ):
        household = Household(church_id=journey.id, name="Just Kids")
        db.session.add(household)
        db.session.flush()
        kid = Person(church_id=journey.id, first_name="Ada", last_name="Stone",
                     stage="member", household_id=household.id, is_child=True,
                     birthdate=years_ago(6))
        db.session.add(kid)
        db.session.commit()
        _, rows = checked_in(db, journey, household, sunday, [kid])

        with app.test_request_context(headers=H):
            assert tell_checked_in(journey, household, [kid], rows) == 0


class TestOptOutIsIgnored:
    def test_a_parent_who_left_the_newsletter_is_still_told(
        self, app, db, journey, family, sunday
    ):
        """Transactional, for the same reason the pickup code email is: a
        parent who unsubscribed from church news has not asked to stop being
        told where their child is."""
        marcus = next(p for p in family.members if p.first_name == "Marcus")
        marcus.set_preference("announcement", False)
        marcus.set_preference("kids_checkin", False)
        db.session.commit()

        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])
        with app.test_request_context(headers=H):
            tell_checked_in(journey, family, [ellie], rows)
        db.session.commit()

        assert "marcus@journey.test" in {m.to_email for m in kids_mail(db, journey)}


class TestThroughTheScreens:
    def test_checking_in_from_the_kiosk_tells_the_parents(
        self, app, db, journey, family, sunday, staff
    ):
        ellie = child(family, "Ellie")
        page = staff.post(
            f"/kids/kiosk/family/{family.id}/",
            data={"person_id": ellie.id}, headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        assert len(kids_mail(db, journey)) == 2

    def test_the_printed_tag_still_has_the_child_on_it(
        self, app, db, journey, family, sunday, staff
    ):
        """A regression guard, from a bug this very change introduced.

        Adding the notification meant reading the check-in rows back, and the
        first attempt flushed instead of committing. The loop above had
        already loaded `session.checkins` to skip anybody already in, so the
        collection was stale and the new rows were invisible: the label page
        rendered with no tags on it and said nothing was wrong. A silent empty
        tag page is a child in a room with no name badge and no pickup code.
        """
        ellie = child(family, "Ellie")
        page = staff.post(
            f"/kids/kiosk/family/{family.id}/",
            data={"person_id": ellie.id}, headers=H, follow_redirects=True,
        )
        body = page.get_data(as_text=True)

        assert "Ellie" in body, "the label page printed no tags"
        _, rows = [], db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)
        ).all()
        assert rows[0].pickup_code in body, "the tag carried no pickup code"

    def test_collecting_from_the_desk_tells_the_parents(
        self, app, db, journey, family, sunday, staff
    ):
        ellie = child(family, "Ellie")
        code, rows = checked_in(db, journey, family, sunday, [ellie])
        before = len(kids_mail(db, journey))

        page = staff.post(
            "/kids/checkout/",
            data={"code": code, "checkin_id": rows[0].id,
                  "collected_by": "Grandma Webb"},
            headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        after = kids_mail(db, journey)
        assert len(after) > before
        assert any("Grandma Webb" in m.body_text for m in after)

    def test_the_staff_list_check_out_tells_them_too(
        self, app, db, journey, family, sunday, staff
    ):
        ellie = child(family, "Ellie")
        _, rows = checked_in(db, journey, family, sunday, [ellie])

        page = staff.post(f"/kids/checkins/{rows[0].id}/out/", headers=H,
                          follow_redirects=True)
        assert page.status_code == 200
        assert any("collected" in m.subject.lower()
                   for m in kids_mail(db, journey))
