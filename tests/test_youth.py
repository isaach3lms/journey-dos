"""Splitting youth out of kids at thirteen.

The rule is an age, so the ordinary case has to need nobody: a child becomes
a youth on their thirteenth birthday whether or not staff noticed. That is
the whole reason this is derived rather than a flag somebody ticks.

Two things make it harder than one comparison, and both are about this
roster rather than about ages:

1. **A birthday is optional here**, and plenty of children have none. The
   rule has to give an answer for them, and the answer has to be the one
   that keeps somebody visible on the lobby iPad rather than quietly
   removing them from it.
2. **The line is drawn in four places** that must agree: the roster filter,
   the counts on the rail, the check-in screen, and the person's own record.
   Two of those are SQL and two are Python, so the same rule is written
   twice and the tests here exist mostly to keep the two honest.
"""

from datetime import date, timedelta

import pytest

from app.ages import ADULT, KID, YOUTH, YOUTH_FROM_AGE, bands_for, born_on_or_before, group_for
from app.models import Church, Household, Person
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def years_ago(years, days=0):
    return date.today().replace(year=date.today().year - years) - timedelta(days=days)


def a_child(db, journey, first, *, birthdate=None, override=None,
            household=None):
    person = Person(church_id=journey.id, first_name=first, last_name="Brandt",
                    stage="member", is_child=True, birthdate=birthdate,
                    youth_override=override, approved_at=utcnow(),
                    household_id=household.id if household else None)
    db.session.add(person)
    db.session.commit()
    return person


class TestWhereTheLineIs:
    def test_a_thirteen_year_old_is_youth(self, db, journey):
        assert a_child(db, journey, "Theo",
                       birthdate=years_ago(13)).age_group == YOUTH

    def test_a_twelve_year_old_is_a_kid(self, db, journey):
        assert a_child(db, journey, "Sam",
                       birthdate=years_ago(12)).age_group == KID

    def test_the_day_before_the_birthday_is_still_a_kid(self, db, journey):
        """Off by one here is a child in the wrong room."""
        tomorrow = date.today() + timedelta(days=1)
        almost = tomorrow.replace(year=tomorrow.year - YOUTH_FROM_AGE)

        assert a_child(db, journey, "Nearly", birthdate=almost).age_group == KID

    def test_the_birthday_itself_is_youth(self, db, journey):
        today = date.today().replace(year=date.today().year - YOUTH_FROM_AGE)

        assert a_child(db, journey, "Today", birthdate=today).age_group == YOUTH

    def test_nobody_has_to_do_anything_for_it_to_happen(self, db, journey):
        """The point of deriving it. A child who turned thirteen last night
        is a youth this morning with no staff action."""
        theo = a_child(db, journey, "Theo", birthdate=years_ago(13, days=1))

        assert theo.youth_override is None
        assert theo.age_group == YOUTH

    def test_an_adult_is_neither(self, db, journey):
        adult = Person(church_id=journey.id, first_name="Dana", last_name="Brandt",
                       stage="member", approved_at=utcnow())
        db.session.add(adult)
        db.session.commit()

        assert adult.age_group == ADULT

    def test_a_leap_day_birthday_does_not_crash(self):
        """29 February has no anniversary in most years, and the obvious
        one-liner raises on those."""
        assert born_on_or_before(date(2027, 2, 28))
        assert born_on_or_before(date(2028, 2, 29)) == date(2015, 3, 1)


class TestAChildWithNoBirthday:
    def test_they_stay_with_the_kids(self, db, journey):
        """It has to mean one of the two, and this is the one that keeps
        somebody on the check-in screen. The other way round they vanish from
        the lobby iPad and nobody knows why."""
        assert a_child(db, journey, "Unknown").age_group == KID

    def test_a_human_can_say_which(self, db, journey):
        assert a_child(db, journey, "Teen",
                       override=True).age_group == YOUTH


class TestTheOverrideWins:
    def test_a_thirteen_year_old_can_be_kept_with_the_kids(self, db, journey):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14),
                       override=False)

        assert theo.age_group == KID

    def test_a_twelve_year_old_can_be_moved_up(self, db, journey):
        sam = a_child(db, journey, "Sam", birthdate=years_ago(12), override=True)

        assert sam.age_group == YOUTH

    def test_clearing_it_goes_back_to_the_birthday(self, db, journey):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14),
                       override=False)
        theo.youth_override = None
        db.session.commit()

        assert theo.age_group == YOUTH


class TestTheDatabaseAgreesWithPython:
    """The rule is written twice, in `group_for` and in `Person.in_group`.

    Two of the four places that draw this line are SQL, because counting a
    church's kids by loading every person is not something to do on every
    page view. These tests exist to stop the two drifting, which would show
    up as a roster filter and a check-in screen disagreeing about one child.
    """

    def test_every_case_matches(self, db, journey):
        cases = [
            ("ChildNoDob", None, None),
            ("Twelve", years_ago(12), None),
            ("Thirteen", years_ago(13), None),
            ("Fifteen", years_ago(15), None),
            ("HeldBack", years_ago(14), False),
            ("MovedUp", years_ago(11), True),
        ]
        for name, birthdate, override in cases:
            a_child(db, journey, name, birthdate=birthdate, override=override)

        for group in (KID, YOUTH):
            in_sql = {
                p.first_name for p in db.session.scalars(
                    db.select(Person).where(
                        Person.church_id == journey.id,
                        Person.in_group(group))
                ).all()
            }
            in_python = {
                name for name, _, _ in cases
                if group_for(db.session.scalar(
                    db.select(Person).where(
                        Person.church_id == journey.id,
                        Person.first_name == name))) == group
            }
            assert in_sql == in_python, group

    def test_adults_are_not_in_either(self, db, journey):
        for group in (KID, YOUTH):
            names = {p.first_name for p in db.session.scalars(
                db.select(Person).where(Person.church_id == journey.id,
                                        Person.in_group(group))).all()}
            assert "Dana" not in names

    def test_an_unknown_group_is_refused(self):
        """Rather than returning everybody, which would silently misreport
        whatever screen asked."""
        with pytest.raises(ValueError):
            Person.in_group("teenagers")


class TestTheRoster:
    def test_the_youth_filter_shows_only_youth(self, db, journey, client, staff):
        a_child(db, journey, "Theo", birthdate=years_ago(14))
        a_child(db, journey, "Sam", birthdate=years_ago(5))

        page = client.get("/people/?stage=youth", headers=H).get_data(as_text=True)

        assert "Theo" in page
        assert "Sam" not in page

    def test_the_kids_filter_no_longer_shows_youth(self, db, journey, client,
                                                   staff):
        """The split. Before this, one number and one list covered both."""
        a_child(db, journey, "Theo", birthdate=years_ago(14))
        a_child(db, journey, "Sam", birthdate=years_ago(5))

        page = client.get("/people/?stage=kids", headers=H).get_data(as_text=True)

        assert "Sam" in page
        assert "Theo" not in page

    def test_the_rail_counts_them_apart(self, db, journey, client, staff):
        a_child(db, journey, "Theo", birthdate=years_ago(14))
        a_child(db, journey, "Nia", birthdate=years_ago(16))
        a_child(db, journey, "Sam", birthdate=years_ago(5))

        assert Person.group_count(journey.id, YOUTH) == 2
        assert Person.group_count(journey.id, KID) == 1

    def test_the_rail_offers_both(self, db, journey, client, staff):
        """Seeded first: the rail hides itself on a church with nobody on
        the roster, so an empty seed would pass this test by not rendering
        either link."""
        a_child(db, journey, "Theo", birthdate=years_ago(14))

        page = client.get("/people/", headers=H).get_data(as_text=True)

        assert "stage=youth" in page
        assert "stage=kids" in page

    def test_an_archived_youth_is_not_counted(self, db, journey):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))
        theo.is_archived = True
        db.session.commit()

        assert Person.group_count(journey.id, YOUTH) == 0

    def test_another_churchs_youth_are_not_counted(self, db, journey):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(Person(church_id=riverbend.id, first_name="Theirs",
                              last_name="Osei", stage="member", is_child=True,
                              birthdate=years_ago(14), approved_at=utcnow()))
        db.session.commit()

        assert Person.group_count(journey.id, YOUTH) == 0

    def test_a_nonsense_filter_is_still_refused(self, client, staff):
        assert client.get("/people/?stage=teenagers",
                          headers=H).status_code == 404


class TestTheCheckInScreen:
    def test_the_bands_come_back_in_order(self):
        """Kids first, where a volunteer's thumb already is."""
        bands = bands_for([])

        assert [b.group for b in bands] == [KID, YOUTH, ADULT]

    def test_nobody_is_dropped(self, db, journey):
        """Including adults. This screen has never filtered anybody out, and
        starting now would quietly stop a family who have been checking in
        for months."""
        home = Household(church_id=journey.id, name="The Brandt family")
        db.session.add(home)
        db.session.flush()
        a_child(db, journey, "Theo", birthdate=years_ago(14), household=home)
        a_child(db, journey, "Sam", birthdate=years_ago(5), household=home)
        db.session.add(Person(church_id=journey.id, first_name="Dana",
                              last_name="Brandt", stage="member",
                              household_id=home.id, approved_at=utcnow()))
        db.session.commit()

        bands = bands_for(home.members)

        assert sum(len(b.members) for b in bands) == 3
        assert [m.first_name for m in bands[0].members] == ["Sam"]
        assert [m.first_name for m in bands[1].members] == ["Theo"]
        assert [m.first_name for m in bands[2].members] == ["Dana"]

    def test_the_kiosk_shows_the_headings(self, db, journey, client, staff):
        from app.models import CheckinSession

        home = Household(church_id=journey.id, name="The Brandt family")
        db.session.add(home)
        db.session.flush()
        a_child(db, journey, "Theo", birthdate=years_ago(14), household=home)
        a_child(db, journey, "Sam", birthdate=years_ago(5), household=home)
        db.session.add(CheckinSession(church_id=journey.id, name="Sunday",
                                      starts_at=utcnow()))
        db.session.commit()

        page = client.get(f"/kids/kiosk/family/{home.id}/",
                          headers=H).get_data(as_text=True)

        assert "Theo" in page and "Sam" in page
        assert "Youth" in page and "Kids" in page

    def test_a_youth_can_still_be_checked_in(self, db, journey, client, staff):
        """Grouped, not filtered. A volunteer who has been checking in a
        fourteen year old every week still can."""
        from app.models import Checkin, CheckinSession

        home = Household(church_id=journey.id, name="The Brandt family")
        db.session.add(home)
        db.session.flush()
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14),
                       household=home)
        db.session.add(CheckinSession(church_id=journey.id, name="Sunday",
                                      starts_at=utcnow()))
        db.session.commit()

        client.post(f"/kids/kiosk/family/{home.id}/",
                    data={"person_id": str(theo.id)}, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Checkin.id)).where(
                Checkin.person_id == theo.id)
        ) == 1


class TestSettingItByHand:
    def test_staff_can_hold_a_youth_with_the_kids(self, db, journey, client,
                                                  staff):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))

        client.post(f"/people/{theo.id}/flags/",
                    data={"youth_override": "kid"}, headers=H)

        db.session.refresh(theo)
        assert theo.age_group == KID

    def test_staff_can_move_a_kid_up(self, db, journey, client, staff):
        sam = a_child(db, journey, "Sam", birthdate=years_ago(12))

        client.post(f"/people/{sam.id}/flags/",
                    data={"youth_override": "youth"}, headers=H)

        db.session.refresh(sam)
        assert sam.age_group == YOUTH

    def test_clearing_it_returns_to_the_birthday(self, db, journey, client,
                                                 staff):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14),
                       override=False)

        client.post(f"/people/{theo.id}/flags/",
                    data={"youth_override": ""}, headers=H)

        db.session.refresh(theo)
        assert theo.youth_override is None
        assert theo.age_group == YOUTH

    def test_the_move_is_on_the_timeline(self, db, journey, client, staff):
        """Which room a child is in is the kind of change somebody asks
        about later."""
        from app.models import PersonEvent

        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))

        client.post(f"/people/{theo.id}/flags/",
                    data={"youth_override": "kid"}, headers=H)

        entries = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == theo.id)
        ).all()
        assert any("Youth" in (e.detail or "") and "Kids" in (e.detail or "")
                   for e in entries)

    def test_the_control_is_not_offered_on_an_adult(self, db, journey, client,
                                                    staff):
        """The line does not apply to them, and a three-way box on an
        adult's record is a question nobody asked."""
        adult = Person(church_id=journey.id, first_name="Dana", last_name="Brandt",
                       stage="member", approved_at=utcnow())
        db.session.add(adult)
        db.session.commit()

        page = client.get(f"/people/{adult.id}/", headers=H).get_data(as_text=True)

        assert 'name="youth_override"' not in page

    def test_a_nonsense_value_changes_nothing(self, db, journey, client, staff):
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))

        client.post(f"/people/{theo.id}/flags/",
                    data={"youth_override": "grown"}, headers=H)

        db.session.refresh(theo)
        assert theo.youth_override is None


class TestWhatDidNotChange:
    def test_a_youth_is_still_a_child_everywhere_else(self, db, journey):
        """Youth are a split inside children, not a third thing outside
        them. They stay out of church-wide email and out of the adult
        follow-up series, which is what `is_child` has always governed."""
        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))

        assert theo.is_child is True

    def test_a_youth_is_excluded_from_church_wide_email(self, db, journey):
        from app.broadcast import resolve, EVERYONE

        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))
        theo.email = "theo@example.com"
        db.session.commit()

        assert all(p.id != theo.id for p in resolve(journey.id, EVERYONE))

    def test_a_youth_starts_no_adult_sequence(self, db, journey):
        from app.automation import enroll_for_stage

        theo = a_child(db, journey, "Theo", birthdate=years_ago(14))
        theo.stage = "visitor"
        db.session.commit()

        assert enroll_for_stage(theo) == []

    def test_the_stage_counts_still_exclude_both(self, db, journey):
        before = sum(Person.stage_counts(journey.id).values())
        a_child(db, journey, "Theo", birthdate=years_ago(14))
        a_child(db, journey, "Sam", birthdate=years_ago(5))

        assert sum(Person.stage_counts(journey.id).values()) == before
