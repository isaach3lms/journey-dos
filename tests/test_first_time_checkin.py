"""A family nobody has met, checking a child in at the lobby iPad.

Before this the kiosk could only find a family it already knew. A first time
visitor with a four year old got a volunteer, a paper form, and a promise
that somebody would add them later.

The feature has two halves and both have to hold:

**The lobby half.** The family is on the roster, the child has a tag, the
household has a code for next week, and it all finishes on one screen while
a parent holds a toddler.

**The pastor's half.** It lands on the Guests screen as a connect card,
alerts staff, and starts the guest follow-up, because a child checked in by
a stranger is a visiting family and that is what a card records.

The thing most worth protecting here is that it goes through `app.guests`
rather than around it. A second path to the roster would mean two matching
rules and two places the follow-up has to start, and one of them would
drift. Several tests below exist only to pin that down.
"""

from datetime import date, timedelta

import pytest

from app.models import (
    Checkin,
    CheckinSession,
    Church,
    GuestCard,
    Household,
    Person,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def sunday(db, journey):
    session = CheckinSession(church_id=journey.id, name="Sunday 9:30am",
                             starts_at=utcnow())
    db.session.add(session)
    db.session.commit()
    return session


@pytest.fixture
def kiosk(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_birthday(years):
    """A birthday that makes somebody exactly this old today.

    Worked out by calendar rather than by multiplying 365.25, which lands a
    day or two out and makes a fifteen year old fourteen.
    """
    today = date.today()
    try:
        born = today.replace(year=today.year - years)
    except ValueError:  # 29 February
        born = today.replace(year=today.year - years, day=28)
    return (born - timedelta(days=1)).isoformat()


FAMILY = {
    "parent_first": "Megan",
    "parent_last": "Slinkard",
    "phone": "(573) 475-0235",
    "child_first_0": "Tyler",
    "child_birthdate_0": a_birthday(6),
    "child_first_1": "Rose",
    "child_birthdate_1": a_birthday(3),
}


def submit(kiosk, **overrides):
    data = dict(FAMILY)
    data.update(overrides)
    return kiosk.post("/kids/kiosk/first-time/", data=data, headers=H,
                      follow_redirects=True)


def people_named(db, journey, first):
    return db.session.scalars(
        db.select(Person).where(Person.church_id == journey.id,
                                Person.first_name == first)
    ).all()


class TestTheLobbyHalf:
    def test_the_family_lands_on_the_roster(self, db, journey, sunday, kiosk):
        submit(kiosk)

        [parent] = people_named(db, journey, "Megan")
        assert parent.stage == "visitor"
        assert parent.phone_last4 == "0235", (
            "the kiosk searches on the last four digits, so a family who "
            "just gave their number must be findable by it next week"
        )
        assert parent.household is not None

        [tyler] = people_named(db, journey, "Tyler")
        assert tyler.is_child
        assert tyler.household_id == parent.household_id
        assert tyler.last_name == "Slinkard", "the surname did not carry down"

    def test_the_children_are_checked_in(self, db, journey, sunday, kiosk):
        submit(kiosk)

        rows = db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)).all()
        assert {r.person.first_name for r in rows} == {"Tyler", "Rose"}
        assert len({r.pickup_code for r in rows}) == 1, (
            "siblings got different pickup codes, so a parent is carrying two"
        )

    def test_the_parent_is_not_checked_in(self, db, journey, sunday, kiosk):
        """The adult is on the roster, not in the kids room."""
        submit(kiosk)
        rows = db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)).all()
        assert "Megan" not in {r.person.first_name for r in rows}

    def test_the_tags_are_ready_on_the_same_screen(self, db, journey, sunday,
                                                   kiosk):
        response = submit(kiosk)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Tyler" in body and "Rose" in body
        assert "Print name tags" in body

    def test_the_family_is_told_their_code(self, db, journey, sunday, kiosk):
        """The only moment this family looks at a screen that can show it."""
        body = submit(kiosk).get_data(as_text=True)
        household = db.session.scalar(db.select(Household).where(
            Household.church_id == journey.id))
        assert household.checkin_pin
        assert household.checkin_pin in body

    def test_a_birthday_puts_them_in_the_right_band(self, db, journey, sunday,
                                                    kiosk):
        submit(kiosk, child_first_2="Jonah", child_birthdate_2=a_birthday(15))

        jonah = people_named(db, journey, "Jonah")[0]
        assert jonah.age == 15
        assert jonah.is_child, (
            "a fifteen year old is still a child record for check-in; the "
            "kid or youth band is worked out from the birthday"
        )

    def test_no_birthday_is_fine(self, db, journey, sunday, kiosk):
        """A parent who does not want to give one, or a date picker nobody
        can work on an old tablet. The child is still checked in."""
        submit(kiosk, child_birthdate_0="", child_birthdate_1="")
        assert len(people_named(db, journey, "Tyler")) == 1
        assert db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)).all()

    def test_a_typed_nonsense_birthday_does_not_refuse_the_family(
        self, db, journey, sunday, kiosk
    ):
        submit(kiosk, child_birthdate_0="not a date")
        tyler = people_named(db, journey, "Tyler")[0]
        assert tyler.birthdate is None

    def test_a_birthday_in_the_future_is_dropped(self, db, journey, sunday, kiosk):
        """A typo, and an age of minus three puts a child in no room at all."""
        ahead = (date.today() + timedelta(days=200)).isoformat()
        submit(kiosk, child_birthdate_0=ahead)
        assert people_named(db, journey, "Tyler")[0].birthdate is None


class TestThePastorsHalf:
    def test_it_makes_a_connect_card(self, db, journey, sunday, kiosk):
        submit(kiosk)

        [card] = db.session.scalars(
            db.select(GuestCard).where(GuestCard.church_id == journey.id)).all()
        assert card.first_name == "Megan"
        assert card.phone == "(573) 475-0235"
        assert card.is_new_person
        assert card.wants_contact, (
            "somebody who handed over a number so the church can reach them "
            "about their child has asked to be contacted"
        )

    def test_the_card_says_where_it_came_from(self, db, journey, sunday, kiosk):
        """A pastor reading the Guests screen on Monday needs to know why
        there is no email on this one."""
        submit(kiosk)
        card = db.session.scalar(db.select(GuestCard))
        assert "kids check-in" in (card.note or "")

    def test_it_shows_up_on_the_guests_screen(self, db, journey, sunday, kiosk):
        submit(kiosk)
        body = kiosk.get("/people/guests/", headers=H).get_data(as_text=True)
        assert "Megan" in body

    def test_staff_are_told(self, db, journey, sunday, kiosk, monkeypatch):
        told = []
        import app.blueprints.kids as kids_bp

        monkeypatch.setattr(kids_bp.guests, "alert_staff",
                            lambda *a, **k: told.append(k) or 1)
        submit(kiosk)
        assert len(told) == 1
        assert "/people/guests" in told[0]["path"]

    def test_a_failed_alert_does_not_lose_the_family(self, db, journey, sunday,
                                                     kiosk, monkeypatch):
        """They are standing in the lobby and the child already has a tag.
        An error page here would say their check-in did not work when it
        did."""
        import app.blueprints.kids as kids_bp

        monkeypatch.setattr(
            kids_bp.guests, "alert_staff",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no email")))

        response = submit(kiosk)
        assert response.status_code == 200
        assert len(people_named(db, journey, "Tyler")) == 1
        assert db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)).all()

    def test_the_guest_sequence_starts(self, db, journey, sunday, kiosk):
        """The reason this goes through app.guests rather than writing its
        own rows: a family added here gets the same follow-up as one who
        filled in the card on their phone."""
        from app.models import SequenceEnrollment

        submit(kiosk)
        parent = people_named(db, journey, "Megan")[0]
        enrolled = db.session.scalars(
            db.select(SequenceEnrollment).where(
                SequenceEnrollment.person_id == parent.id)).all()
        assert enrolled, "a first time family was not enrolled in anything"


class TestItDoesNotMakeDuplicates:
    def test_a_parent_already_on_the_roster_is_matched(self, db, journey,
                                                       sunday, kiosk):
        """Somebody who filled in a card on their phone last week and now
        uses this button because the kiosk did not recognise them. Two
        records of the same person is the fastest way to ruin a roster."""
        existing = Person(church_id=journey.id, first_name="Megan",
                          last_name="Slinkard", phone="(573) 475-0235",
                          stage="member", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        submit(kiosk)
        assert len(people_named(db, journey, "Megan")) == 1

    def test_a_matched_parent_is_not_demoted(self, db, journey, sunday, kiosk):
        """A member helping with their grandchild must not be moved back to
        visitor and started on a series written for strangers."""
        member = Person(church_id=journey.id, first_name="Megan",
                        last_name="Slinkard", phone="(573) 475-0235",
                        stage="member", approved_at=utcnow())
        db.session.add(member)
        db.session.commit()

        submit(kiosk)
        db.session.refresh(member)
        assert member.stage == "member"

    def test_the_same_family_twice_makes_one_household(self, db, journey,
                                                       sunday, kiosk):
        submit(kiosk)
        submit(kiosk)

        households = db.session.scalars(
            db.select(Household).where(Household.church_id == journey.id)).all()
        assert len(households) == 1
        assert len(people_named(db, journey, "Tyler")) == 1

    def test_the_same_child_twice_is_checked_in_once(self, db, journey, sunday,
                                                     kiosk):
        submit(kiosk)
        submit(kiosk)

        rows = db.session.scalars(
            db.select(Checkin).where(Checkin.session_id == sunday.id)).all()
        names = [r.person.first_name for r in rows]
        assert sorted(names) == ["Rose", "Tyler"], names

    def test_a_second_visit_fills_in_a_missing_birthday(self, db, journey,
                                                        sunday, kiosk):
        submit(kiosk, child_birthdate_0="")
        assert people_named(db, journey, "Tyler")[0].birthdate is None

        submit(kiosk)
        assert people_named(db, journey, "Tyler")[0].birthdate is not None

    def test_an_existing_household_is_reused(self, db, journey, sunday, kiosk):
        """A parent already attached to a family, using this button anyway.
        Their children belong in the family they are already in."""
        household = Household(church_id=journey.id, name="The Slinkards")
        db.session.add(household)
        db.session.flush()
        parent = Person(church_id=journey.id, first_name="Megan",
                        last_name="Slinkard", phone="(573) 475-0235",
                        household_id=household.id, stage="member",
                        approved_at=utcnow())
        db.session.add(parent)
        db.session.commit()

        submit(kiosk)
        assert len(db.session.scalars(
            db.select(Household).where(Household.church_id == journey.id)).all()) == 1
        assert people_named(db, journey, "Tyler")[0].household_id == household.id


class TestWhatItRefuses:
    def test_no_phone_number(self, db, journey, sunday, kiosk):
        """The number is how the church reaches a parent during the service
        and how the family checks in next week. Without it this is a child
        with nobody attached."""
        response = submit(kiosk, phone="")
        assert "phone number" in response.get_data(as_text=True)
        assert people_named(db, journey, "Tyler") == []

    def test_no_children(self, db, journey, sunday, kiosk):
        response = submit(kiosk, child_first_0="", child_first_1="")
        assert "at least one child" in response.get_data(as_text=True)
        assert people_named(db, journey, "Megan") == []

    def test_no_parent_name(self, db, journey, sunday, kiosk):
        response = submit(kiosk, parent_first="")
        assert "first name" in response.get_data(as_text=True)
        assert people_named(db, journey, "Tyler") == []

    def test_nothing_is_written_when_it_refuses(self, db, journey, sunday, kiosk):
        """Half a family on the roster is worse than none: it is a household
        with no children and a card nobody can answer."""
        submit(kiosk, phone="")
        assert db.session.scalars(db.select(GuestCard)).all() == []
        assert db.session.scalars(
            db.select(Household).where(Household.church_id == journey.id)).all() == []

    def test_a_closed_session_sends_them_back(self, db, journey, kiosk):
        """No service running. Nothing to check into, so nothing is made."""
        response = kiosk.post("/kids/kiosk/first-time/", data=FAMILY,
                              headers=H, follow_redirects=True)
        assert response.status_code == 200
        assert db.session.scalars(db.select(GuestCard)).all() == []


class TestWhoCanReachIt:
    def test_the_link_is_on_the_kiosk(self, db, journey, sunday, kiosk):
        body = kiosk.get("/kids/kiosk/", headers=H).get_data(as_text=True)
        assert "First time here?" in body
        assert "/kids/kiosk/first-time/" in body

    def test_a_signed_out_tablet_gets_nothing(self, client, db, journey, sunday):
        """The kiosk is behind a login for the same reason the rest of
        check-in is: this screen writes children onto a church's roster."""
        response = client.get("/kids/kiosk/first-time/", headers=H)
        assert response.status_code in (302, 401, 403)

    def test_a_member_cannot_reach_it(self, client, sign_in, db, journey, sunday):
        sign_in("member@journeychurchsemo.com")
        response = client.get("/kids/kiosk/first-time/", headers=H)
        assert response.status_code in (302, 403)

    def test_another_churchs_kiosk_does_not_see_this_family(self, db, journey,
                                                            sunday, kiosk):
        submit(kiosk)
        other = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        assert db.session.scalars(
            db.select(Person).where(Person.church_id == other.id,
                                    Person.first_name == "Tyler")).all() == []


class TestTheFormItself:
    def test_it_offers_room_for_more_than_one_child(self, db, journey, sunday,
                                                    kiosk):
        body = kiosk.get("/kids/kiosk/first-time/", headers=H).get_data(as_text=True)
        assert "child_first_0" in body
        assert "child_first_1" in body
        assert "Add another child" in body

    def test_rows_past_the_second_are_out_of_the_way(self, db, journey, sunday,
                                                     kiosk):
        """A form showing six empty children reads as six questions."""
        body = kiosk.get("/kids/kiosk/first-time/", headers=H).get_data(as_text=True)
        assert body.count("data-extra") >= 4

    def test_it_asks_for_a_number_not_an_address(self, db, journey, sunday, kiosk):
        body = kiosk.get("/kids/kiosk/first-time/", headers=H).get_data(as_text=True)
        assert 'type="tel"' in body
        assert 'name="email"' not in body


class TestTheHouseholdName:
    def test_it_reads_like_a_family(self):
        from app.firsttime import household_name

        assert household_name("Slinkard", "Megan") == "The Slinkard family"

    def test_no_surname_still_gets_a_name(self):
        """A household with an empty name is a blank line on the check-in
        screen and a blank line on the tag."""
        from app.firsttime import household_name

        assert household_name("", "Megan") == "Megan's family"
        assert household_name("", "").strip()
