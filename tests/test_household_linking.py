"""Linking a person to a household from the staff side.

Written for the case that produced it. The same person was entered twice,
once by the office with an email and once by a parent with the family, and
there was no way to put the family on the record worth keeping.
"""

import pytest

from app.models import Church, Household, Person, PersonEvent
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def add_person(db, first, last="Tanksley", child=False, home=None):
    person = Person(church_id=journey(db).id, first_name=first, last_name=last,
                    stage="member", is_child=child, approved_at=utcnow(),
                    household_id=home.id if home else None)
    db.session.add(person)
    db.session.commit()
    return person


def add_home(db, name="The Tanksley family"):
    home = Household(church_id=journey(db).id, name=name)
    db.session.add(home)
    db.session.commit()
    return home


def move(client, person, household_id="", name=""):
    return client.post(
        f"/people/{person.id}/household/",
        data={"household_id": household_id, "household_name": name},
        headers=H, follow_redirects=True,
    )


class TestMovingSomebodyIn:
    def test_into_a_household_that_already_exists(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney")

        response = move(staff, whitney, household_id=home.id)
        assert b"Whitney is now in The Tanksley family." in response.data
        db.session.refresh(whitney)
        assert whitney.household_id == home.id

    def test_starting_a_new_one(self, db, staff):
        whitney = add_person(db, "Whitney")
        response = move(staff, whitney, name="The Tanksley family")
        assert b"Started The Tanksley family with Whitney in it." in response.data

        db.session.refresh(whitney)
        assert whitney.household.name == "The Tanksley family"
        assert whitney.household.church_id == whitney.church_id

    def test_a_typed_name_wins_over_the_dropdown(self, db, staff):
        """Somebody who typed a name meant the name."""
        home = add_home(db, "The Wrong family")
        whitney = add_person(db, "Whitney")
        move(staff, whitney, household_id=home.id, name="The Tanksley family")
        db.session.refresh(whitney)
        assert whitney.household.name == "The Tanksley family"

    def test_moving_between_households(self, db, staff):
        old = add_home(db, "The Old family")
        new = add_home(db, "The New family")
        # Somebody else keeps the old one alive.
        add_person(db, "Someone", home=old)
        whitney = add_person(db, "Whitney", home=old)

        move(staff, whitney, household_id=new.id)
        db.session.refresh(whitney)
        assert whitney.household_id == new.id
        assert db.session.get(Household, old.id) is not None

    def test_the_family_now_lists_them(self, db, staff):
        home = add_home(db)
        marcus = add_person(db, "Marcus", home=home)
        whitney = add_person(db, "Whitney")
        move(staff, whitney, household_id=home.id)

        db.session.refresh(home)
        assert {m.first_name for m in home.members} == {"Marcus", "Whitney"}
        page = staff.get(f"/people/{marcus.id}/", headers=H).data
        assert b"Whitney Tanksley" in page


class TestMovingSomebodyOut:
    def test_out_of_a_household(self, db, staff):
        home = add_home(db)
        add_person(db, "Someone", home=home)
        whitney = add_person(db, "Whitney", home=home)

        response = move(staff, whitney)
        assert b"Whitney is no longer in a household." in response.data
        db.session.refresh(whitney)
        assert whitney.household_id is None

    def test_the_last_person_out_takes_the_household_with_them(self, db, staff):
        """An empty family is clutter, and its check-in code should not stay
        live for a family that is not there."""
        home = add_home(db)
        whitney = add_person(db, "Whitney", home=home)

        response = move(staff, whitney)
        assert b"had nobody left in it, so it is gone" in response.data
        assert db.session.get(Household, home.id) is None

    def test_sunday_still_remembers_who_collected_whom(self, db, staff):
        """Check-in records copy the household name rather than reading it, so
        deleting an emptied household cannot rewrite a safety record."""
        from app.models import Checkin, CheckinSession

        home = add_home(db)
        child = add_person(db, "Sofia", child=True, home=home)
        session = CheckinSession(church_id=child.church_id, name="Sunday",
                                 starts_at=utcnow(), is_open=True)
        db.session.add(session)
        db.session.flush()
        db.session.add(Checkin(
            church_id=child.church_id, session_id=session.id, person_id=child.id,
            household_id=home.id, household_name=home.name, pickup_code="ABC123",
        ))
        db.session.commit()

        move(staff, child)
        assert db.session.get(Household, home.id) is None
        record = db.session.scalar(db.select(Checkin))
        assert record is not None
        # The name is the column the record is read from. The id is a
        # convenience the database nulls on delete.
        assert record.household_name == "The Tanksley family"

    def test_nobody_in_a_household_and_nothing_asked_for(self, db, staff):
        whitney = add_person(db, "Whitney")
        response = move(staff, whitney)
        assert response.status_code == 200
        db.session.refresh(whitney)
        assert whitney.household_id is None


class TestTheCheckInCode:
    def test_a_family_that_gains_a_child_gets_a_code(self, db, staff):
        home = add_home(db)
        assert home.checkin_pin is None
        child = add_person(db, "Sofia", child=True)

        move(staff, child, household_id=home.id)
        db.session.refresh(home)
        assert home.checkin_pin

    def test_an_adults_only_family_does_not_need_one(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney")
        move(staff, whitney, household_id=home.id)
        db.session.refresh(home)
        assert home.checkin_pin is None

    def test_an_existing_code_is_left_alone(self, db, staff):
        home = add_home(db)
        add_person(db, "Sofia", child=True, home=home)
        db.session.refresh(home)
        code = home.ensure_checkin_pin()
        db.session.commit()

        move(staff, add_person(db, "Mateo", child=True), household_id=home.id)
        db.session.refresh(home)
        assert home.checkin_pin == code


class TestWhatStaffSeeAfterwards:
    def test_the_move_is_on_their_record(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney")
        move(staff, whitney, household_id=home.id)

        events = db.session.scalars(
            PersonEvent.for_person(whitney.church_id, whitney.id)
        ).all()
        assert any("Household changed" in e.summary for e in events)
        assert any("Moved into The Tanksley family" in (e.detail or "") for e in events)

    def test_who_moved_them(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney")
        move(staff, whitney, household_id=home.id)
        event = db.session.scalars(
            PersonEvent.for_person(whitney.church_id, whitney.id)
        ).all()[0]
        assert event.actor_name == "Pastor Reed"

    def test_leaving_one_says_which_one(self, db, staff):
        home = add_home(db)
        add_person(db, "Someone", home=home)
        whitney = add_person(db, "Whitney", home=home)
        move(staff, whitney)
        assert any(
            "Taken out of The Tanksley family" in (e.detail or "")
            for e in db.session.scalars(
                PersonEvent.for_person(whitney.church_id, whitney.id))
        )

    def test_moving_them_nowhere_new_is_not_an_event(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney", home=home)
        response = move(staff, whitney, household_id=home.id)
        assert b"already in The Tanksley family" in response.data
        assert db.session.scalars(
            PersonEvent.for_person(whitney.church_id, whitney.id)
        ).all() == []


class TestThePickerOnTheScreen:
    def test_every_household_is_offered(self, db, staff):
        add_home(db, "The Tanksley family")
        add_home(db, "The Romero family")
        whitney = add_person(db, "Whitney")
        page = staff.get(f"/people/{whitney.id}/", headers=H).data.decode()
        assert "The Tanksley family" in page
        assert "The Romero family" in page

    def test_how_many_are_in_each_one(self, db, staff):
        home = add_home(db)
        add_person(db, "Marcus", home=home)
        add_person(db, "Sofia", child=True, home=home)
        whitney = add_person(db, "Whitney")
        page = staff.get(f"/people/{whitney.id}/", headers=H).data.decode()
        assert "The Tanksley family (2)" in page

    def test_one_person_is_not_1_plural(self, db, staff):
        home = add_home(db)
        add_person(db, "Marcus", home=home)
        whitney = add_person(db, "Whitney")
        page = staff.get(f"/people/{whitney.id}/", headers=H).data.decode()
        assert "The Tanksley family (1)" in page

    def test_their_own_household_is_preselected(self, db, staff):
        home = add_home(db)
        whitney = add_person(db, "Whitney", home=home)
        page = staff.get(f"/people/{whitney.id}/", headers=H).data.decode()
        option = page[page.index(f'value="{home.id}"'):]
        assert "selected" in option[:60]


class TestWhatIsRefused:
    def test_a_household_at_another_church(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Household(church_id=other.id, name="The Riverbend family")
        db.session.add(theirs)
        db.session.commit()

        whitney = add_person(db, "Whitney")
        response = move(staff, whitney, household_id=theirs.id)
        assert b"could not find that household" in response.data
        db.session.refresh(whitney)
        assert whitney.household_id is None

    @pytest.mark.parametrize("bad", ["abc", "0", "999999", "1;drop"])
    def test_nonsense_ids(self, db, staff, bad):
        whitney = add_person(db, "Whitney")
        response = move(staff, whitney, household_id=bad)
        assert b"could not find that household" in response.data
        db.session.refresh(whitney)
        assert whitney.household_id is None

    def test_a_name_that_is_only_spaces_is_not_a_family(self, db, staff):
        whitney = add_person(db, "Whitney")
        move(staff, whitney, name="   ")
        db.session.refresh(whitney)
        assert whitney.household_id is None

    def test_a_very_long_name_is_cut_not_refused(self, db, staff):
        whitney = add_person(db, "Whitney")
        move(staff, whitney, name="T" * 400)
        db.session.refresh(whitney)
        assert len(whitney.household.name) == 160

    def test_what_they_type_is_escaped(self, db, staff):
        whitney = add_person(db, "Whitney")
        move(staff, whitney, name="<script>alert(1)</script>")
        page = staff.get(f"/people/{whitney.id}/", headers=H).data
        assert b"<script>alert(1)</script>" not in page
        assert b"&lt;script&gt;" in page

    def test_somebody_at_another_church(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Someone", last_name="Else",
                        stage="member")
        db.session.add(theirs)
        db.session.commit()
        assert staff.post(f"/people/{theirs.id}/household/", data={},
                          headers=H).status_code == 404

    def test_a_member_cannot(self, db, member):
        whitney = add_person(db, "Whitney")
        assert member.post(f"/people/{whitney.id}/household/", data={},
                           headers=H).status_code == 403


class TestTheDuplicateItWasBuiltFor:
    def test_the_family_moves_to_the_record_worth_keeping(self, db, staff):
        """Two Whitneys: the office record has her email, the one a parent
        made has the family. Put the family on the first, archive the second,
        and nothing was retyped."""
        home = add_home(db)
        duplicate = add_person(db, "Whitney", home=home)
        real = add_person(db, "Whitney")
        real.email = "whitney@thejourneychurchsemo.com"
        db.session.commit()

        move(staff, real, household_id=home.id)
        staff.post("/people/archive/", data={"person_id": duplicate.id},
                   headers=H, follow_redirects=True)

        db.session.refresh(real)
        db.session.refresh(duplicate)
        assert real.household_id == home.id
        assert real.email == "whitney@thejourneychurchsemo.com"
        assert duplicate.is_archived is True

        # One Whitney on the roster, with both halves of her record.
        listed = staff.get("/people/", headers=H).data.decode()
        table = listed[listed.index("<tbody>"):listed.index("</tbody>")]
        # Once in the row, once in the checkbox label for a screen reader.
        assert table.count('class="pn">Whitney Tanksley') == 1
        assert "The Tanksley family" in table
        assert "whitney@thejourneychurchsemo.com" in table


class TestTheSnapshotHoldsItsColumns:
    from pathlib import Path as _Path

    CSS = (_Path(__file__).resolve().parent.parent
           / "app" / "static" / "css" / "app.css").read_text()

    def test_a_long_email_stays_in_its_own_column(self):
        """A real church address ran out of the contact column and printed
        over the household beside it."""
        rule = self.CSS[self.CSS.index(".snap > div{"):]
        rule = rule[:rule.index("}")]
        assert "min-width:0" in rule
        assert "overflow-wrap:anywhere" in rule

    def test_the_picker_is_closed_until_it_is_wanted(self, db, staff):
        whitney = add_person(db, "Whitney")
        page = staff.get(f"/people/{whitney.id}/", headers=H).data.decode()
        block = page[page.index('class="hhedit"'):]
        assert not block[:40].strip().endswith("open>")
        assert "<summary>" in block[:120]
