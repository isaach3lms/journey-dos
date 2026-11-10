"""Kids codes derived from a parent's phone number.

The church can switch kids check-in and pickup from generated codes to the
last four digits of a parent's phone. That is a trade the church made
knowingly: one number a family already knows, in exchange for a pickup code
that no longer expires and that somebody outside the system could know.

What is *not* part of that trade, and is tested hardest here, is the
collision. Four digits is 10,000 values, so two families sharing them is a
question of when. A code that quietly reaches two families and puts both sets
of children on one screen with one button is a child handed to the wrong
adult, and no convenience buys that. Every path asks for a last name first.

The generated codes are still issued and stored throughout, so the switch is
reversible. That is tested too, because a safety control you cannot turn back
on is not a control.
"""

import pytest

from app.models import Checkin, CheckinSession, Church, Household, Person
from app.models.base import utcnow
from app.phonecode import last4, matches_last_name
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    church.phone_checkin = True
    db.session.commit()
    return church


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def a_family(db, journey, name, phone, children=("Kid",), surname=None,
             parent="Parent", email=None):
    # "The Webb family" -> "Webb". The surname has to be real, because the
    # whole disambiguation path is tested against it.
    if surname is None:
        words = [w for w in name.split() if w.lower() not in ("the", "family")]
        surname = words[-1] if words else name
    household = Household(church_id=journey.id, name=name)
    db.session.add(household)
    db.session.flush()
    db.session.add(Person(
        church_id=journey.id, first_name=parent, last_name=surname,
        phone=phone, email=email, stage="member", household_id=household.id,
    ))
    for child in children:
        db.session.add(Person(
            church_id=journey.id, first_name=child, last_name=surname,
            stage="member", household_id=household.id, is_child=True,
        ))
    household.ensure_checkin_pin()
    db.session.commit()
    return household


@pytest.fixture
def sunday(db, journey):
    row = CheckinSession(church_id=journey.id, name="Sunday 9:30",
                         starts_at=utcnow())
    db.session.add(row)
    db.session.commit()
    return row


class TestReadingAPhoneNumber:
    def test_it_takes_the_last_four_digits(self):
        assert last4("(573) 555-0142") == "0142"

    def test_formatting_does_not_matter(self):
        """The same number typed four ways is the same number."""
        for written in ("5735550142", "573-555-0142", "+1 573 555 0142",
                        "(573) 555.0142"):
            assert last4(written) == "0142"

    def test_an_extension_is_not_stripped(self):
        """Nothing here can tell an extension from the number, so a family
        whose record ends in one gets those digits. Wrong, but consistently
        wrong, which is what lets a parent learn their own code."""
        assert last4("573-555-0142 x88") == "4288"

    def test_a_short_number_gets_nothing(self):
        """A two digit code at a pickup desk collides with everybody. None is
        the honest answer and the caller falls back to a generated code."""
        assert last4("911") is None
        assert last4("") is None
        assert last4(None) is None

    def test_letters_are_ignored(self):
        assert last4("555-CALL") is None


class TestTheDigitsAreKeptOnTheRecord:
    def test_saving_a_phone_fills_them_in(self, db, journey):
        person = Person(church_id=journey.id, first_name="Ann", last_name="Lee",
                        phone="(573) 555-0142", stage="member")
        db.session.add(person)
        db.session.commit()
        assert person.phone_last4 == "0142"

    def test_changing_the_number_changes_them(self, db, journey):
        """A validator rather than a rule callers follow, because a missed
        update is a family standing at a kiosk that does not know them."""
        person = Person(church_id=journey.id, first_name="Ann", last_name="Lee",
                        phone="5735550142", stage="member")
        db.session.add(person)
        db.session.commit()
        person.phone = "5735559999"
        db.session.commit()
        assert person.phone_last4 == "9999"

    def test_clearing_the_number_clears_them(self, db, journey):
        person = Person(church_id=journey.id, first_name="Ann", last_name="Lee",
                        phone="5735550142", stage="member")
        db.session.add(person)
        db.session.commit()
        person.phone = None
        db.session.commit()
        assert person.phone_last4 is None


class TestWhichCodeAFamilyHas:
    def test_it_is_the_parents_digits(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        assert home.phone_code == "0142"
        assert home.kiosk_code(journey) == "0142"

    def test_a_childs_phone_is_not_the_familys_code(self, db, journey):
        """A nine year old's number changes when they get a new one, and the
        family would silently stop being able to check in."""
        home = a_family(db, journey, "The Webb family", None)
        child = next(p for p in home.members if p.is_child)
        child.phone = "5735559999"
        db.session.commit()
        db.session.refresh(home)
        assert home.phone_code is None

    def test_no_phone_falls_back_to_the_generated_code(self, db, journey):
        """A family with no code cannot check in at all, which is worse than
        a family on the older scheme."""
        home = a_family(db, journey, "The Webb family", None)
        assert home.phone_code is None
        assert home.kiosk_code(journey) == home.checkin_pin

    def test_the_generated_code_is_still_there(self, db, journey):
        """Nothing is deleted, so the switch is reversible."""
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        assert home.checkin_pin
        assert len(home.checkin_pin) == 4

    def test_with_the_setting_off_it_is_the_generated_code(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        journey.phone_checkin = False
        db.session.commit()
        assert home.kiosk_code(journey) == home.checkin_pin


class TestLookingAFamilyUp:
    def test_the_digits_find_them(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        assert [h.id for h in Household.matching_code(journey, "0142")] == [home.id]

    def test_the_old_generated_code_still_works(self, db, journey):
        """A family with no phone on file is still using theirs."""
        home = a_family(db, journey, "The Webb family", None)
        found = Household.matching_code(journey, home.checkin_pin)
        assert [h.id for h in found] == [home.id]

    def test_two_families_sharing_digits_both_come_back(self, db, journey):
        """The whole point of returning a list."""
        one = a_family(db, journey, "The Webb family", "573-555-0142")
        two = a_family(db, journey, "The Alvarez family", "417-222-0142")
        found = Household.matching_code(journey, "0142")
        assert {h.id for h in found} == {one.id, two.id}

    def test_a_last_name_separates_them(self, db, journey):
        a_family(db, journey, "The Webb family", "573-555-0142")
        two = a_family(db, journey, "The Alvarez family", "417-222-0142")
        found = Household.matching_code(journey, "0142", "alvarez")
        assert [h.id for h in found] == [two.id]

    def test_the_last_name_is_not_case_sensitive(self, db, journey):
        two = a_family(db, journey, "The Alvarez family", "417-222-0142")
        assert Household.matching_code(journey, "0142", "ALVAREZ")[0].id == two.id

    def test_a_members_name_counts_not_just_the_household_name(self, db, journey):
        """A blended family may hold two surnames, and a household called
        "Marcus and Dana Webb" should still answer to Webb."""
        home = a_family(db, journey, "Marcus and Dana", "573-555-0142",
                        surname="Webb")
        assert Household.matching_code(journey, "0142", "Webb")[0].id == home.id

    def test_a_wrong_last_name_finds_nobody(self, db, journey):
        a_family(db, journey, "The Webb family", "573-555-0142")
        assert Household.matching_code(journey, "0142", "Nguyen") == []

    def test_with_the_setting_off_digits_do_nothing(self, db, journey):
        a_family(db, journey, "The Webb family", "573-555-0142")
        journey.phone_checkin = False
        db.session.commit()
        assert Household.matching_code(journey, "0142") == []

    def test_an_archived_parent_does_not_answer(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        parent = next(p for p in home.members if not p.is_child)
        parent.is_archived = True
        db.session.commit()
        assert Household.matching_code(journey, "0142") == []

    def test_nothing_typed_finds_nobody(self, db, journey):
        a_family(db, journey, "The Webb family", "573-555-0142")
        assert Household.matching_code(journey, "") == []
        assert Household.matching_code(journey, None) == []

    def test_another_church_is_not_reachable(self, db, journey):
        a_family(db, journey, "The Webb family", "573-555-0142")
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        other.phone_checkin = True
        db.session.commit()
        assert Household.matching_code(other, "0142") == []


class TestTheKiosk:
    def test_a_parent_types_their_digits(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        r = staff.post("/kids/kiosk/", data={"pin": "0142"}, headers=H)
        assert r.status_code == 302
        assert f"/kids/kiosk/family/{home.id}/" in r.headers["Location"]

    def test_the_screen_asks_for_the_phone(self, db, journey, staff, sunday):
        page = staff.get("/kids/kiosk/", headers=H).data.decode()
        assert "last four digits of your phone" in page

    def test_the_old_code_still_gets_a_family_in(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", None)
        r = staff.post("/kids/kiosk/", data={"pin": home.checkin_pin}, headers=H)
        assert f"/kids/kiosk/family/{home.id}/" in r.headers["Location"]

    def test_a_tie_asks_for_the_last_name(self, db, journey, staff, sunday):
        a_family(db, journey, "The Webb family", "573-555-0142")
        a_family(db, journey, "The Alvarez family", "417-222-0142")
        page = staff.post("/kids/kiosk/", data={"pin": "0142"},
                          headers=H).data.decode()
        assert "What is your last name" in page

    def test_a_tie_names_nobody(self, db, journey, staff, sunday):
        """A screen listing the families that matched hands whoever typed
        those four digits the names of everybody they belong to."""
        a_family(db, journey, "The Webb family", "573-555-0142")
        a_family(db, journey, "The Alvarez family", "417-222-0142")
        page = staff.post("/kids/kiosk/", data={"pin": "0142"},
                          headers=H).data.decode()
        assert "Webb" not in page
        assert "Alvarez" not in page

    def test_the_last_name_gets_them_in(self, db, journey, staff, sunday):
        a_family(db, journey, "The Webb family", "573-555-0142")
        two = a_family(db, journey, "The Alvarez family", "417-222-0142")
        r = staff.post("/kids/kiosk/",
                       data={"pin": "0142", "last_name": "Alvarez"}, headers=H)
        assert f"/kids/kiosk/family/{two.id}/" in r.headers["Location"]

    def test_two_families_of_one_name_go_to_a_volunteer(self, db, journey, staff, sunday):
        """Rather than another guessing game."""
        a_family(db, journey, "The Webb family", "573-555-0142", parent="Marcus")
        a_family(db, journey, "The other Webbs", "417-222-0142", parent="Jo",
                 surname="Webb")
        page = staff.post("/kids/kiosk/",
                          data={"pin": "0142", "last_name": "Webb"},
                          headers=H, follow_redirects=True).data.decode()
        assert "need a volunteer" in page

    def test_an_unknown_code_says_so(self, db, journey, staff, sunday):
        page = staff.post("/kids/kiosk/", data={"pin": "9999"}, headers=H,
                          follow_redirects=True).data.decode()
        assert "do not recognise" in page

    def test_a_tie_does_not_burn_an_attempt(self, db, journey, staff, sunday):
        """It is a real family typing their real code. Counting it toward the
        lockout would lock a tablet out on a busy Sunday."""
        a_family(db, journey, "The Webb family", "573-555-0142")
        a_family(db, journey, "The Alvarez family", "417-222-0142")
        for _ in range(12):
            staff.post("/kids/kiosk/", data={"pin": "0142"}, headers=H)
        page = staff.post("/kids/kiosk/",
                          data={"pin": "0142", "last_name": "Webb"},
                          headers=H, follow_redirects=True).data.decode()
        assert "Too many tries" not in page


class TestThePickupCode:
    def check_in(self, db, staff, home, sunday):
        kids = [p for p in home.members if p.is_child]
        staff.post(f"/kids/kiosk/family/{home.id}/",
                   data={"person_id": [p.id for p in kids]}, headers=H)
        db.session.refresh(sunday)
        return [c for c in sunday.checkins if c.household_id == home.id]

    def test_it_is_the_parents_digits(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        rows = self.check_in(db, staff, home, sunday)
        assert rows and all(r.pickup_code == "0142" for r in rows)

    def test_siblings_still_share_one(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", "573-555-0142",
                        children=("Ellie", "Nate"))
        rows = self.check_in(db, staff, home, sunday)
        assert len({r.pickup_code for r in rows}) == 1

    def test_a_family_with_no_phone_gets_a_generated_one(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", None)
        rows = self.check_in(db, staff, home, sunday)
        assert rows
        assert rows[0].pickup_code.isalpha()

    def test_with_the_setting_off_it_is_generated(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        journey.phone_checkin = False
        db.session.commit()
        rows = self.check_in(db, staff, home, sunday)
        assert rows[0].pickup_code.isalpha()

    def test_it_is_the_same_next_week(self, db, journey, staff, sunday):
        """Stated plainly because it is what the church gave up. A generated
        code is meaningless an hour later; this one is not."""
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        first = self.check_in(db, staff, home, sunday)[0].pickup_code

        sunday.close()
        later = CheckinSession(church_id=journey.id, name="Next Sunday",
                               starts_at=utcnow())
        db.session.add(later)
        db.session.commit()
        rows = self.check_in(db, staff, home, later)
        assert rows[0].pickup_code == first


class TestCollectingAChild:
    def put_in(self, db, journey, sunday, home, code):
        for person in [p for p in home.members if p.is_child]:
            db.session.add(Checkin(
                church_id=journey.id, session_id=sunday.id, person_id=person.id,
                household_id=home.id, household_name=home.name, pickup_code=code,
            ))
        db.session.commit()
        db.session.refresh(sunday)

    def test_the_digits_find_the_children(self, db, journey, staff, sunday):
        home = a_family(db, journey, "The Webb family", "573-555-0142",
                        children=("Ellie",))
        self.put_in(db, journey, sunday, home, "0142")
        page = staff.get("/kids/checkout/?code=0142", headers=H).data.decode()
        assert "Ellie Webb" in page

    def test_a_shared_code_shows_nobody(self, db, journey, staff, sunday):
        """The failure this whole subsystem exists to prevent: two families'
        children on one screen with one check-them-out button."""
        one = a_family(db, journey, "The Webb family", "573-555-0142",
                       children=("Ellie",))
        two = a_family(db, journey, "The Alvarez family", "417-222-0142",
                       children=("Mateo",))
        self.put_in(db, journey, sunday, one, "0142")
        self.put_in(db, journey, sunday, two, "0142")

        page = staff.get("/kids/checkout/?code=0142", headers=H).data.decode()
        assert "Ellie Webb" not in page
        assert "Mateo Alvarez" not in page
        assert "What is the last name" in page

    def test_the_last_name_narrows_it(self, db, journey, staff, sunday):
        one = a_family(db, journey, "The Webb family", "573-555-0142",
                       children=("Ellie",))
        two = a_family(db, journey, "The Alvarez family", "417-222-0142",
                       children=("Mateo",))
        self.put_in(db, journey, sunday, one, "0142")
        self.put_in(db, journey, sunday, two, "0142")

        page = staff.get("/kids/checkout/?code=0142&last_name=Webb",
                         headers=H).data.decode()
        assert "Ellie Webb" in page
        assert "Mateo Alvarez" not in page

    def test_a_posted_form_cannot_skip_the_last_name(self, db, journey, staff, sunday):
        """A screen is not a security control. Without this check a code
        shared by two families would check out either one's children on an id
        the sender guessed."""
        one = a_family(db, journey, "The Webb family", "573-555-0142",
                       children=("Ellie",))
        two = a_family(db, journey, "The Alvarez family", "417-222-0142",
                       children=("Mateo",))
        self.put_in(db, journey, sunday, one, "0142")
        self.put_in(db, journey, sunday, two, "0142")
        target = next(c for c in sunday.checkins if c.household_id == two.id)

        staff.post("/kids/checkout/",
                   data={"code": "0142", "checkin_id": target.id,
                         "collected_by": "Somebody"},
                   headers=H)
        db.session.refresh(target)
        assert target.is_present

    def test_with_the_last_name_the_check_out_goes_through(self, db, journey, staff, sunday):
        one = a_family(db, journey, "The Webb family", "573-555-0142",
                       children=("Ellie",))
        two = a_family(db, journey, "The Alvarez family", "417-222-0142",
                       children=("Mateo",))
        self.put_in(db, journey, sunday, one, "0142")
        self.put_in(db, journey, sunday, two, "0142")
        target = next(c for c in sunday.checkins if c.household_id == two.id)

        staff.post("/kids/checkout/",
                   data={"code": "0142", "last_name": "Alvarez",
                         "checkin_id": target.id, "collected_by": "Ana Alvarez"},
                   headers=H)
        db.session.refresh(target)
        assert not target.is_present
        assert target.collected_by == "Ana Alvarez"

    def test_last_weeks_code_collects_nobody(self, db, journey, staff, sunday):
        """The code no longer expires, but it is still scoped to one session,
        so it only reaches a child who is in a room today."""
        home = a_family(db, journey, "The Webb family", "573-555-0142",
                        children=("Ellie",))
        self.put_in(db, journey, sunday, home, "0142")
        sunday.close()
        later = CheckinSession(church_id=journey.id, name="Next Sunday",
                               starts_at=utcnow())
        db.session.add(later)
        db.session.commit()

        page = staff.get("/kids/checkout/?code=0142", headers=H).data.decode()
        assert "Ellie Webb" not in page
        assert "No children are checked in" in page


class TestTheChurchSetting:
    def test_it_is_off_for_a_new_church(self, db):
        """A church that has not weighed what a permanent pickup code costs
        should not inherit it."""
        fresh = Church(slug="fresh", name="Fresh Church", palette_key="journey")
        db.session.add(fresh)
        db.session.commit()
        assert fresh.phone_checkin is False

    def test_staff_can_turn_it_off(self, db, journey, staff):
        staff.post("/settings/kids-codes/", headers=H)
        db.session.refresh(journey)
        assert journey.phone_checkin is False

    def test_turning_it_off_restores_generated_codes(self, db, journey, staff, sunday):
        """A control you cannot turn back on is not a control."""
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        staff.post("/settings/kids-codes/", headers=H)
        db.session.refresh(journey)
        assert home.kiosk_code(journey) == home.checkin_pin
        assert Household.matching_code(journey, "0142") == []

    def test_the_screen_says_what_it_costs(self, db, journey, staff):
        page = staff.get("/settings/", headers=H).data.decode()
        assert "stops changing every week" in page

    def test_it_is_audited(self, db, journey, staff):
        from app.models import AuditEvent

        staff.post("/settings/kids-codes/", headers=H)
        event = db.session.scalar(
            db.select(AuditEvent).order_by(AuditEvent.id.desc())
        )
        assert "Kids codes switched to" in event.summary

    def test_a_leader_cannot_change_it(self, db, journey, leader):
        assert leader.post("/settings/kids-codes/", headers=H).status_code == 403


class TestTellingFamilies:
    def test_it_emails_the_adults(self, db, journey, staff):
        from app.models import OutboxMessage

        a_family(db, journey, "The Webb family", "573-555-0142",
                 email="marcus@example.com")
        staff.post("/settings/kids-codes/tell-families/", headers=H)
        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert any("0142" in (m.body_text or "") for m in queued)

    def test_it_does_not_email_children(self, db, journey, staff):
        from app.models import OutboxMessage

        home = a_family(db, journey, "The Webb family", "573-555-0142",
                        email="marcus@example.com")
        child = next(p for p in home.members if p.is_child)
        child.email = "ellie@example.com"
        db.session.commit()

        staff.post("/settings/kids-codes/tell-families/", headers=H)
        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert not any(m.to_email == "ellie@example.com" for m in queued)

    def test_nothing_goes_out_until_the_button_is_pressed(self, db, journey, staff):
        """Deploying does not mail a church's whole roster."""
        from app.models import OutboxMessage

        a_family(db, journey, "The Webb family", "573-555-0142",
                 email="marcus@example.com")
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_it_says_how_many(self, db, journey, staff):
        a_family(db, journey, "The Webb family", "573-555-0142",
                 email="marcus@example.com")
        page = staff.post("/settings/kids-codes/tell-families/", headers=H,
                          follow_redirects=True).data.decode()
        assert "1 family told" in page

    def test_a_leader_cannot_send_it(self, db, journey, leader):
        r = leader.post("/settings/kids-codes/tell-families/", headers=H)
        assert r.status_code == 403


class TestMatchingALastName:
    def test_the_household_name_counts(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        assert matches_last_name(home, "webb")

    def test_a_members_name_counts(self, db, journey):
        home = a_family(db, journey, "Marcus and Dana", "573-555-0142",
                        surname="Webb")
        assert matches_last_name(home, "Webb")

    def test_nothing_typed_matches_nothing(self, db, journey):
        home = a_family(db, journey, "The Webb family", "573-555-0142")
        assert not matches_last_name(home, "")
        assert not matches_last_name(home, None)
