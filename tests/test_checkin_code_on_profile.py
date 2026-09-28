"""A family's check-in code, readable by staff on the person's own page.

Staff need it at the desk when a parent has forgotten theirs, and a staff
member who is also a parent had no way to see their own: the member app shows
it on the You tab, which staff rarely open.

The code identifies a family at the kiosk. It is not a password and it does
not authorise a pickup, which is why it can sit on a screen staff already
have open. See app/checkin_pin.py.
"""

import pytest

from app.models import AuditEvent, Church, Household, Person
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


@pytest.fixture
def family(db):
    """A parent and a child in one household, with no code yet."""
    church = journey(db)
    home = Household(church_id=church.id, name="The Tanksley family")
    db.session.add(home)
    db.session.flush()
    parent = Person(church_id=church.id, first_name="Whitney", last_name="Tanksley",
                    stage="leader", household_id=home.id, approved_at=utcnow())
    child = Person(church_id=church.id, first_name="Ella", last_name="Tanksley",
                   is_child=True, household_id=home.id)
    db.session.add_all([parent, child])
    db.session.commit()
    return parent, home


def page(staff, person):
    return staff.get(f"/people/{person.id}/", headers=H).data.decode()


def make_code(staff, person):
    return staff.post(f"/people/{person.id}/household/code/", headers=H,
                      follow_redirects=True)


class TestSeeingTheCode:
    def test_it_is_on_the_person_page(self, db, staff, family):
        parent, home = family
        code = home.ensure_checkin_pin()
        db.session.commit()

        body = page(staff, parent)
        assert "Check-in code" in body
        assert code in body

    def test_it_is_with_the_household(self, db, staff, family):
        """Next to the family it belongs to, not in a corner of its own."""
        parent, home = family
        home.ensure_checkin_pin()
        db.session.commit()
        body = page(staff, parent)
        block = body[body.index('id="household"'):body.index("</dd>", body.index('id="household"'))]
        assert "Check-in code" in block
        assert home.checkin_pin in block

    def test_everybody_in_the_family_shows_the_same_code(self, db, staff, family):
        parent, home = family
        code = home.ensure_checkin_pin()
        db.session.commit()
        child = next(m for m in home.members if m.is_child)
        assert code in page(staff, child)

    def test_it_says_what_the_code_is_for(self, db, staff, family):
        """A volunteer reading it should not think it is a password."""
        parent, home = family
        home.ensure_checkin_pin()
        db.session.commit()
        assert "it does not authorise a pickup" in page(staff, parent)

    def test_a_family_with_no_code_yet(self, db, staff, family):
        parent, _ = family
        body = page(staff, parent)
        assert "No check-in code yet" in body
        assert "Create a code" in body

    def test_somebody_with_no_family_has_no_code_line(self, db, staff):
        alone = Person(church_id=journey(db).id, first_name="Ben", last_name="Carter",
                       stage="member")
        db.session.add(alone)
        db.session.commit()
        body = page(staff, alone)
        assert "Check-in code" not in body
        assert "Not linked to a household" in body


class TestCreatingOne:
    def test_a_family_that_had_none(self, db, staff, family):
        parent, home = family
        assert home.checkin_pin is None

        response = make_code(staff, parent)
        db.session.refresh(home)
        assert home.checkin_pin
        assert home.checkin_pin.encode() in response.data
        assert b"now has the check-in code" in response.data

    def test_it_shows_straight_away(self, db, staff, family):
        parent, home = family
        make_code(staff, parent)
        db.session.refresh(home)
        assert home.checkin_pin in page(staff, parent)

    def test_creating_one_is_not_logged_as_a_rotation(self, db, staff, family):
        """Nothing was replaced, so nothing stopped working."""
        parent, _ = family
        make_code(staff, parent)
        assert db.session.scalars(
            db.select(AuditEvent).where(AuditEvent.action == "pin_rotated")
        ).all() == []

    def test_a_person_with_no_family_is_told_why_not(self, db, staff):
        alone = Person(church_id=journey(db).id, first_name="Ben", last_name="Carter",
                       stage="member")
        db.session.add(alone)
        db.session.commit()
        response = make_code(staff, alone)
        assert b"belongs to a household, not a person" in response.data


class TestReplacingOne:
    def test_the_code_changes(self, db, staff, family):
        parent, home = family
        first = home.ensure_checkin_pin()
        db.session.commit()

        response = make_code(staff, parent)
        db.session.refresh(home)
        assert home.checkin_pin != first
        assert b"The old one no longer works." in response.data

    def test_the_old_one_stops_working_at_the_kiosk(self, db, staff, family):
        """The whole point of replacing it."""
        parent, home = family
        first = home.ensure_checkin_pin()
        db.session.commit()
        make_code(staff, parent)

        found = db.session.scalar(
            db.select(Household).where(
                Household.church_id == home.church_id,
                Household.checkin_pin == first,
            )
        )
        assert found is None

    def test_it_is_audited(self, db, staff, family):
        parent, home = family
        home.ensure_checkin_pin()
        db.session.commit()
        make_code(staff, parent)

        event = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == "pin_rotated")
        )
        assert event is not None
        assert "The Tanksley family" in event.summary
        assert event.actor_name == "Pastor Reed"

    def test_the_new_code_is_not_in_the_log(self, db, staff, family):
        """The log is made to be read, kept and exported. A working code in
        it is worse than no log."""
        parent, home = family
        home.ensure_checkin_pin()
        db.session.commit()
        make_code(staff, parent)

        db.session.refresh(home)
        for event in db.session.scalars(db.select(AuditEvent)).all():
            assert home.checkin_pin not in (event.summary or "")
            assert home.checkin_pin not in (event.detail or "")

    def test_the_button_only_shows_when_there_is_one_to_replace(self, db, staff, family):
        parent, home = family
        assert "Replace this code" not in page(staff, parent)
        home.ensure_checkin_pin()
        db.session.commit()
        assert "Replace this code" in page(staff, parent)

    def test_the_warning_is_next_to_the_button(self, db, staff, family):
        parent, home = family
        home.ensure_checkin_pin()
        db.session.commit()
        assert "The old code stops working straight away" in page(staff, parent)


class TestWhoCan:
    def test_a_leader_can(self, db, client, sign_in, family):
        """Leaders run the kids desk, so they read codes."""
        parent, home = family
        sign_in("leader@journeychurchsemo.com")
        assert home.ensure_checkin_pin() or True
        db.session.commit()
        assert client.get(f"/people/{parent.id}/", headers=H).status_code == 200

    def test_a_member_cannot(self, db, client, sign_in, family):
        parent, _ = family
        sign_in("member@journeychurchsemo.com")
        assert client.get(f"/people/{parent.id}/", headers=H).status_code == 403
        assert client.post(f"/people/{parent.id}/household/code/",
                           headers=H).status_code == 403

    def test_another_churchs_family_is_a_404(self, db, staff):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Someone", last_name="Else",
                        stage="member")
        db.session.add(theirs)
        db.session.commit()
        assert staff.post(f"/people/{theirs.id}/household/code/",
                          headers=H).status_code == 404


class TestTheMemberStillSeesTheirOwn:
    def test_the_you_tab_is_unchanged(self, db, client, sign_in):
        """This change adds a place to read it, it does not move it."""
        from app.models import User

        user = db.session.scalar(
            db.select(User).where(User.email == "member@journeychurchsemo.com"))
        home = Household(church_id=user.church_id, name="The Romero family")
        db.session.add(home)
        db.session.flush()
        person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                        email=user.email, stage="member", household_id=home.id,
                        approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        code = home.ensure_checkin_pin()
        db.session.commit()

        sign_in("member@journeychurchsemo.com")
        assert code in client.get("/me/you/?open=family", headers=H).data.decode()
