"""A parent adding their own children, ready for kids check-in."""

from datetime import date, timedelta

import pytest

from app.models import Church, Household, Person, PersonEvent, User
from app.models.base import utcnow
from app.blueprints.member import MAX_HOUSEHOLD_MEMBERS
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def parent(db, client, sign_in):
    """The member login with a roster record and no household yet."""
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                    email=user.email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return person


def add(client, first="Sofia", **fields):
    data = {"first_name": first, "is_child": "on"}
    data.update(fields)
    return client.post("/me/family/add/", data=data, headers=H, follow_redirects=True)


def children(db, parent):
    return [m for m in parent.household.members if m.id != parent.id]


class TestAddingAChild:
    def test_the_first_child_creates_the_household(self, db, client, parent):
        assert parent.household is None
        response = add(client, birthdate="2019-06-02")
        assert b"Sofia added to your family." in response.data
        db.session.refresh(parent)
        assert parent.household is not None
        assert parent.household.name == "The Romero family"
        [child] = children(db, parent)
        assert child.first_name == "Sofia"
        assert child.is_child is True
        assert child.birthdate == date(2019, 6, 2)

    def test_the_surname_follows_the_parent(self, db, client, parent):
        add(client)
        assert children(db, parent)[0].last_name == "Romero"

    def test_a_different_surname_is_kept(self, db, client, parent):
        add(client, last_name="Diaz")
        assert children(db, parent)[0].last_name == "Diaz"

    def test_a_second_child_joins_the_same_household(self, db, client, parent):
        add(client, first="Sofia")
        add(client, first="Mateo")
        db.session.refresh(parent)
        assert sorted(c.first_name for c in children(db, parent)) == ["Mateo", "Sofia"]
        assert len({c.household_id for c in children(db, parent)}) == 1

    def test_notes_reach_the_kids_team(self, db, client, parent):
        add(client, notes="Peanut allergy. Goes by Sofi.")
        assert "Peanut allergy" in children(db, parent)[0].notes

    def test_an_adult_can_be_added_too(self, db, client, parent):
        response = add(client, first="Marco", is_child="")
        assert b"Marco added to your family." in response.data
        assert children(db, parent)[0].is_child is False

    def test_they_land_in_the_church_the_parent_is_in(self, db, client, parent):
        add(client)
        assert children(db, parent)[0].church_id == parent.church_id

    def test_they_start_where_the_parent_is(self, db, client, parent):
        """A family arrives together, so a child does not start on a
        follow-up path built for a first-time adult visitor."""
        add(client)
        assert children(db, parent)[0].stage == parent.stage

    def test_staff_see_who_added_them(self, db, client, parent):
        add(client)
        child = children(db, parent)[0]
        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == child.id)
        ).all()
        assert any("Added by a parent in the app" in e.summary for e in events)
        assert any("Alicia Romero" in (e.detail or "") for e in events)


class TestReadyForCheckIn:
    def test_the_household_gets_a_code(self, db, client, parent):
        add(client)
        db.session.refresh(parent)
        assert parent.household.checkin_pin

    def test_the_code_is_on_the_screen(self, db, client, parent):
        add(client)
        db.session.refresh(parent)
        page = client.get("/me/you/?open=family", headers=H).data.decode()
        assert parent.household.checkin_pin in page
        assert "Ready for check-in" in page

    def test_the_code_finds_the_family(self, db, client, parent):
        """The whole point: the code a parent is given at home is the one the
        kiosk looks the family up by on Sunday."""
        add(client, first="Sofia")
        db.session.refresh(parent)
        found = db.session.scalar(
            db.select(Household).where(
                Household.church_id == parent.church_id,
                Household.checkin_pin == parent.household.checkin_pin,
            )
        )
        assert found is not None and found.id == parent.household_id
        assert "Sofia" in [m.first_name for m in found.members]


class TestTheKioskSeesThem:
    """Signed in as a leader only, because the `db` fixture holds one
    application context and a test cannot sign in as two people."""

    def test_a_child_a_parent_added_is_listed_at_the_kiosk(self, db, client, sign_in):
        from app.models import CheckinSession

        church = journey(db)
        home = Household(church_id=church.id, name="The Romero family")
        db.session.add(home)
        db.session.flush()
        db.session.add(Person(church_id=church.id, first_name="Sofia", last_name="Romero",
                              is_child=True, household_id=home.id))
        db.session.add(CheckinSession(church_id=church.id, name="Sunday",
                                      starts_at=utcnow(), is_open=True))
        db.session.commit()

        sign_in("leader@journeychurchsemo.com")
        page = client.get(f"/kids/kiosk/family/{home.id}/", headers=H).data
        assert b"Sofia Romero" in page


class TestWhatIsRefused:
    def test_no_first_name(self, db, client, parent):
        response = add(client, first="")
        assert b"We need at least a first name." in response.data
        assert parent.household is None

    def test_the_same_person_twice(self, db, client, parent):
        add(client, first="Sofia")
        response = add(client, first="Sofia")
        assert b"Sofia Romero is already on your family." in response.data
        assert len(children(db, parent)) == 1

    def test_case_does_not_get_around_it(self, db, client, parent):
        add(client, first="Sofia")
        add(client, first="SOFIA")
        assert len(children(db, parent)) == 1

    def test_a_birthday_in_the_future(self, db, client, parent):
        ahead = (date.today() + timedelta(days=1)).isoformat()
        response = add(client, birthdate=ahead)
        assert b"cannot be in the future" in response.data

    def test_a_birthday_that_is_not_a_date(self, db, client, parent):
        response = add(client, birthdate="sometime in June")
        assert b"did not look like a date" in response.data

    def test_a_family_has_a_ceiling(self, db, client, parent):
        for index in range(MAX_HOUSEHOLD_MEMBERS - 1):
            add(client, first=f"Child{index}")
        response = add(client, first="OneTooMany")
        assert b"A family can hold" in response.data
        db.session.refresh(parent)
        assert len(parent.household.members) == MAX_HOUSEHOLD_MEMBERS

    def test_what_they_type_is_escaped(self, db, client, parent):
        add(client, first="<script>alert(1)</script>")
        page = client.get("/me/you/?open=family", headers=H).data
        assert b"<script>alert(1)</script>" not in page
        assert b"&lt;script&gt;" in page


class TestRemoving:
    def test_a_parent_can_take_a_child_back_off(self, db, client, parent):
        add(client, first="Sofia")
        child = children(db, parent)[0]
        response = client.post("/me/family/remove/", data={"member_id": child.id},
                               headers=H, follow_redirects=True)
        assert b"Sofia removed from your family." in response.data
        db.session.refresh(child)
        # Archived, never deleted: a child who has been checked in has a
        # safety record worth keeping.
        assert child.is_archived is True

    def test_the_record_stops_showing_on_the_screen(self, db, client, parent):
        add(client, first="Sofia")
        child = children(db, parent)[0]
        client.post("/me/family/remove/", data={"member_id": child.id}, headers=H)
        page = client.get("/me/you/?open=family", headers=H).data.decode()
        # The family list itself, not the page: the confirmation message names
        # them once on the way past.
        family = page[page.index('class="famlist"'):page.index("</ul>", page.index('class="famlist"'))]
        assert "Sofia" not in family
        assert "Alicia Romero" in family

    def test_a_removed_child_frees_a_place(self, db, client, parent):
        add(client, first="Sofia")
        child = children(db, parent)[0]
        client.post("/me/family/remove/", data={"member_id": child.id}, headers=H)
        response = add(client, first="Sofia")
        assert b"Sofia added to your family." in response.data

    def test_staff_see_the_removal(self, db, client, parent):
        add(client, first="Sofia")
        child = children(db, parent)[0]
        client.post("/me/family/remove/", data={"member_id": child.id}, headers=H)
        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == child.id)
        ).all()
        assert any("Removed by a parent in the app" in e.summary for e in events)

    def test_somebody_elses_child_is_not_theirs_to_remove(self, db, client, parent):
        add(client, first="Sofia")
        other_home = Household(church_id=parent.church_id, name="The Carter family")
        db.session.add(other_home)
        db.session.flush()
        theirs = Person(church_id=parent.church_id, first_name="Ben", last_name="Carter",
                        is_child=True, household_id=other_home.id)
        db.session.add(theirs)
        db.session.commit()

        response = client.post("/me/family/remove/", data={"member_id": theirs.id},
                               headers=H, follow_redirects=True)
        assert b"could not find that person" in response.data
        db.session.refresh(theirs)
        assert theirs.is_archived is False

    def test_a_parent_cannot_remove_themselves(self, db, client, parent):
        add(client, first="Sofia")
        client.post("/me/family/remove/", data={"member_id": parent.id}, headers=H)
        db.session.refresh(parent)
        assert parent.is_archived is False

    def test_nonsense_ids(self, db, client, parent):
        add(client, first="Sofia")
        for value in ("", "abc", "0", "999999"):
            response = client.post("/me/family/remove/", data={"member_id": value},
                                   headers=H, follow_redirects=True)
            assert b"could not find that person" in response.data


class TestChildrenAreNotFollowUp:
    def test_a_child_never_shows_as_stuck(self, db, client, parent):
        """Visitor plus 200 quiet days would flag an adult. A five year old is
        not somebody staff failed to call."""
        add(client, first="Sofia")
        child = children(db, parent)[0]
        child.stage = "visitor"
        child.stage_since = utcnow() - timedelta(days=200)
        child.last_contact_at = None
        db.session.commit()

        assert child.is_stuck is False
        stuck = db.session.scalars(Person.stuck(parent.church_id)).all()
        assert child not in stuck

    def test_an_adult_in_the_same_state_does_show(self, db, client, parent):
        add(client, first="Marco", is_child="")
        adult = children(db, parent)[0]
        adult.stage = "visitor"
        adult.stage_since = utcnow() - timedelta(days=200)
        adult.last_contact_at = None
        db.session.commit()

        assert adult.is_stuck is True
        assert adult in db.session.scalars(Person.stuck(parent.church_id)).all()
