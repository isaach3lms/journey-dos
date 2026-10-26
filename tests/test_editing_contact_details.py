"""The office correcting somebody's name, email or phone.

A member can fix their own name and phone from the app and deliberately
cannot touch their own email: it is what they sign in with, and letting
somebody retype it on a profile screen is how an account locks itself out.
That route's own note says "the church office changes that", and the office
had no way to. A wrong address on the roster could only be fixed from a
terminal, which in this church means it could not be fixed.

Three decisions worth stating, all tested here:

- **The roster record is not the login.** Changing an email here changes
  where the church writes to, not what anybody signs in with. They are
  separate fields, and the one case staff will hit, correcting an address
  for somebody who has an account, says so out loud rather than leaving
  them to discover it a week later.
- **A shared address is warned about, not refused.** Households genuinely
  share one. What it costs is real and specific (a login can only be matched
  to one person by email) so it is said, and the save still happens.
- **Staff only**, while the profile itself is open to leaders. Adding people
  to the roster is one level of trust; rewriting the address the church uses
  to reach somebody is another.
"""

import pytest

from app.content import PEOPLE
from app.models import Church, Person, PersonEvent, User
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def amy(db, journey):
    person = Person(church_id=journey.id, first_name="Amy", last_name="Davis",
                    email="adavis@jackson.k12.mo.us", phone="5735874721",
                    stage="visitor")
    db.session.add(person)
    db.session.commit()
    return person


def form(**over):
    """A complete submission, so a test changing one field says so."""
    data = {"first_name": "Amy", "last_name": "Davis",
            "email": "adavis@jackson.k12.mo.us", "phone": "5735874721"}
    data.update(over)
    return data


class TestWhoCanCorrectThem:
    def test_staff_get_the_form(self, db, journey, amy, staff):
        body = staff.get(f"/people/{amy.id}/", headers=H).get_data(as_text=True)
        assert PEOPLE["contact_heading"] in body
        assert PEOPLE["contact_edit"] in body

    def test_a_leader_does_not(self, db, journey, amy, leader):
        """Absent, not refused. A form that rejects you on submit teaches
        people to try things and see what happens."""
        page = leader.get(f"/people/{amy.id}/", headers=H)
        assert page.status_code == 200
        assert PEOPLE["contact_heading"] not in page.get_data(as_text=True)

    def test_a_leader_cannot_reach_the_route_either(self, db, journey, amy, leader):
        """The screen hiding a form is not what keeps it safe."""
        page = leader.post(f"/people/{amy.id}/details/", headers=H,
                           data=form(email="changed@example.com"))
        assert page.status_code in (403, 404)
        db.session.refresh(amy)
        assert amy.email == "adavis@jackson.k12.mo.us"

    def test_another_churchs_person_is_not_reachable(self, db, journey, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        theirs = Person(church_id=other.id, first_name="Someone",
                        last_name="Else", email="else@example.com",
                        stage="visitor")
        db.session.add(theirs)
        db.session.commit()

        page = staff.post(f"/people/{theirs.id}/details/", headers=H,
                          data=form(email="mine@example.com"))
        assert page.status_code == 404
        db.session.refresh(theirs)
        assert theirs.email == "else@example.com"


class TestCorrectingThem:
    def test_the_email_can_be_fixed(self, db, journey, amy, staff):
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="amy.davis@jackson.k12.mo.us"))
        db.session.refresh(amy)
        assert amy.email == "amy.davis@jackson.k12.mo.us"

    def test_the_phone_can_be_fixed(self, db, journey, amy, staff):
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(phone="573 587 4722"))
        db.session.refresh(amy)
        assert amy.phone == "573 587 4722"

    def test_a_misspelled_name_can_be_fixed(self, db, journey, amy, staff):
        """Not asked for, and included because it is the same trip. A name
        typed wrong on a connection card was permanent for staff: nothing
        outside a terminal could change it."""
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(first_name="Aimee"))
        db.session.refresh(amy)
        assert amy.full_name == "Aimee Davis"

    def test_an_address_can_be_removed(self, db, journey, amy, staff):
        """Somebody who asks to be taken off email. Blank means none, not
        an empty string that still looks like an address to everything
        downstream."""
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email=""))
        db.session.refresh(amy)
        assert amy.email is None

    def test_the_address_is_stored_lowercase(self, db, journey, amy, staff):
        """Every other path lowercases it, and a login is matched to a person
        by comparing the two."""
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="Amy.Davis@Jackson.K12.MO.US"))
        db.session.refresh(amy)
        assert amy.email == "amy.davis@jackson.k12.mo.us"

    def test_rubbish_is_refused_and_nothing_changes(self, db, journey, amy, staff):
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="not an address"))
        db.session.refresh(amy)
        assert amy.email == "adavis@jackson.k12.mo.us"

    def test_a_person_cannot_be_left_nameless(self, db, journey, amy, staff):
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(first_name="   "))
        db.session.refresh(amy)
        assert amy.first_name == "Amy"


class TestTheTimelineSaysWhoChangedIt:
    def _events(self, db, journey, person):
        return db.session.scalars(
            PersonEvent.for_person(journey.id, person.id)
        ).all()

    def test_a_correction_is_recorded(self, db, journey, amy, staff):
        """"Who changed this address, and when" is the question asked after
        email stops arriving, and a column holding only the current value
        answers nothing."""
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="amy.davis@jackson.k12.mo.us"))

        entries = self._events(db, journey, amy)
        assert any(PEOPLE["contact_event"] in (e.summary or "") for e in entries)

    def test_it_names_what_changed(self, db, journey, amy, staff):
        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="amy.davis@jackson.k12.mo.us", phone="123"))

        detail = " ".join(
            (e.detail or "") for e in self._events(db, journey, amy)
        )
        assert PEOPLE["contact_field_email"] in detail
        assert PEOPLE["contact_field_phone"] in detail
        # And not the one that did not.
        assert PEOPLE["contact_field_name"] not in detail

    def test_saving_without_changing_anything_records_nothing(
        self, db, journey, amy, staff
    ):
        """Opening the form and pressing Save is not an event in anybody's
        week. A timeline that fills up with them stops being read."""
        before = len(self._events(db, journey, amy))
        staff.post(f"/people/{amy.id}/details/", headers=H,
                   follow_redirects=True, data=form())
        assert len(self._events(db, journey, amy)) == before


class TestWhatStaffAreToldAfterwards:
    def _account(self, db, journey, person, email):
        user = User(church_id=journey.id, email=email, name=person.full_name,
                    role="member", person_id=person.id)
        user.set_unusable_password()
        user.mark_verified()
        db.session.add(user)
        db.session.commit()
        return user

    def test_the_login_address_is_not_changed_with_it(self, db, journey, amy, staff):
        """The decision this whole route rests on. The roster record is where
        the church writes to; the login is what somebody signs in with.
        Changing one must not quietly change the other, because an address
        corrected on a profile screen would otherwise lock somebody out of
        their own account."""
        account = self._account(db, journey, amy, "adavis@jackson.k12.mo.us")

        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="amy.davis@jackson.k12.mo.us"))
        db.session.refresh(account)
        assert account.email == "adavis@jackson.k12.mo.us"

    def test_and_staff_are_told_so(self, db, journey, amy, staff):
        """The one case they will hit. Silence here means correcting an
        address and wondering for a week why the person still cannot get
        in."""
        self._account(db, journey, amy, "adavis@jackson.k12.mo.us")

        page = staff.post(f"/people/{amy.id}/details/", headers=H,
                          follow_redirects=True,
                          data=form(email="amy.davis@jackson.k12.mo.us"))
        assert "adavis@jackson.k12.mo.us" in page.get_data(as_text=True)

    def test_nothing_is_said_when_the_two_agree(self, db, journey, amy, staff):
        """A warning that fires on the normal case is a warning people learn
        to click past."""
        self._account(db, journey, amy, "adavis@jackson.k12.mo.us")

        page = staff.post(f"/people/{amy.id}/details/", headers=H,
                          follow_redirects=True, data=form(phone="999"))
        body = page.get_data(as_text=True)
        assert PEOPLE["contact_login_differs"].split("{")[0] not in body

    def test_a_shared_address_is_warned_about(self, db, journey, amy, staff):
        """Households share one address, so this is worth knowing and not
        worth refusing. What it costs is specific: a login is matched to a
        person by email, and two people with one address match neither."""
        rob = Person(church_id=journey.id, first_name="Rob", last_name="Davis",
                     email="davis@example.com", stage="member")
        db.session.add(rob)
        db.session.commit()

        page = staff.post(f"/people/{amy.id}/details/", headers=H,
                          follow_redirects=True,
                          data=form(email="davis@example.com"))
        assert "Rob Davis" in page.get_data(as_text=True)

    def test_but_the_save_still_happens(self, db, journey, amy, staff):
        rob = Person(church_id=journey.id, first_name="Rob", last_name="Davis",
                     email="davis@example.com", stage="member")
        db.session.add(rob)
        db.session.commit()

        staff.post(f"/people/{amy.id}/details/", headers=H, follow_redirects=True,
                   data=form(email="davis@example.com"))
        db.session.refresh(amy)
        assert amy.email == "davis@example.com"

    def test_an_archived_twin_is_not_counted(self, db, journey, amy, staff):
        """Somebody archived is off the roster. Warning about a clash with a
        record nobody can see is a warning with nothing to do about it."""
        gone = Person(church_id=journey.id, first_name="Gone", last_name="Away",
                      email="davis@example.com", stage="visitor",
                      is_archived=True)
        db.session.add(gone)
        db.session.commit()

        page = staff.post(f"/people/{amy.id}/details/", headers=H,
                          follow_redirects=True,
                          data=form(email="davis@example.com"))
        assert "Gone Away" not in page.get_data(as_text=True)
