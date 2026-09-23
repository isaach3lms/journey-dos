"""The You tab as a profile: who you are, how far along, what you can change."""

from datetime import date, timedelta

import pytest

from app.models import Church, Household, Person, PersonEvent, User
from app.models.base import utcnow
from app.stages import STAGES
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def alicia(db, client, sign_in):
    """The member login with a roster record, signed in."""
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(
        church_id=user.church_id, first_name="Alicia", last_name="Romero",
        email=user.email, phone="(573) 555-0142", stage="member",
        approved_at=utcnow(), first_seen_on=date.today() - timedelta(days=176),
    )
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return person


def save(client, **fields):
    data = {"first_name": "Alicia", "last_name": "Romero"}
    data.update(fields)
    return client.post("/me/you/details/", data=data, headers=H, follow_redirects=True)


class TestTheProfileHeader:
    def test_name_stage_and_how_long(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data
        assert b"Alicia Romero" in page
        assert b"Member &middot; 176 days" in page or "Member · 176 days".encode() in page

    def test_initials_stand_in_for_a_photo(self, db, client, alicia):
        assert b'class="pav" aria-hidden="true">AR<' in client.get("/me/you/", headers=H).data

    def test_someone_added_today(self, db, client, alicia):
        alicia.first_seen_on = date.today()
        db.session.commit()
        assert b"joined today" in client.get("/me/you/", headers=H).data

    def test_the_journey_strip_fills_to_their_stage(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data.decode()
        bar = page[page.index('class="jbar"'):page.index("</ol>")]
        assert bar.count("<li") == len(STAGES)
        # Member is the fourth stage, so four segments are filled.
        assert bar.count("done") == 4
        assert bar.count("here") == 1
        assert "Has committed to this church publicly." in page

    def test_a_visitor_sees_one_filled_segment(self, db, client, alicia):
        alicia.stage = "visitor"
        db.session.commit()
        page = client.get("/me/you/", headers=H).data.decode()
        bar = page[page.index('class="jbar"'):page.index("</ol>")]
        assert bar.count("done") == 1


class TestTheRows:
    def test_empty_church_life_still_reads(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data
        assert b"Nobody on file yet" in page
        assert b"Not in a group yet" in page
        assert b"Not scheduled" in page

    def test_groups_and_serving_open_their_own_screens(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data
        assert b'href="/me/groups/"' in page
        assert b'href="/me/serve/"' in page


class TestRowsOpenInPlace:
    """Everything on this screen is a closed row until it is tapped."""

    def rows(self, client):
        page = client.get("/me/you/", headers=H).data.decode()
        return page[page.index('class="mcard mrows"'):page.index("</section>", page.index('class="mcard mrows"'))]

    def test_every_section_is_a_row(self, db, client, alicia):
        rows = self.rows(client)
        for key in ("details", "family", "notifications", "push", "blocked", "account"):
            assert f'<details class="mrowbox" id="{key}"' in rows

    def test_they_start_closed(self, db, client, alicia):
        assert " open>" not in self.rows(client)

    def test_the_form_is_still_on_the_page_inside_its_row(self, db, client, alicia):
        rows = self.rows(client)
        assert 'name="first_name"' in rows
        assert 'action="/me/you/details/"' in rows

    @pytest.mark.parametrize("key", ["details", "family", "notifications", "account"])
    def test_a_link_can_ask_for_one_to_be_open(self, db, client, alicia, key):
        page = client.get(f"/me/you/?open={key}", headers=H).data.decode()
        assert f'id="{key}" open>' in page
        # Only that one.
        assert page.count(" open>") == 1

    def test_saving_leaves_the_row_open(self, db, client, alicia):
        response = save(client, phone="573-555-0123")
        assert b'id="details" open>' in response.data

    def test_a_refused_save_leaves_the_row_open(self, db, client, alicia):
        response = save(client, first_name="")
        assert b'id="details" open>' in response.data
        assert b"We need both a first and last name." in response.data

    def test_saving_preferences_leaves_notifications_open(self, db, client, alicia):
        response = client.post("/me/you/preferences/", data={"cat_welcome": "on"},
                               headers=H, follow_redirects=True)
        assert b'id="notifications" open>' in response.data

    def test_nonsense_in_the_query_opens_nothing(self, db, client, alicia):
        page = client.get("/me/you/?open=<script>", headers=H).data
        assert b" open>" not in page
        assert b"<script>alert" not in page


class TestChangingYourOwnDetails:
    def test_phone_and_birthday(self, db, client, alicia):
        response = save(client, phone="573-555-0199", birthdate="1989-04-12")
        assert b"Saved. Thank you." in response.data
        db.session.refresh(alicia)
        assert alicia.phone == "573-555-0199"
        assert alicia.birthdate == date(1989, 4, 12)

    def test_a_name_correction(self, db, client, alicia):
        save(client, first_name="Alicia", last_name="Romero-Diaz", phone=alicia.phone)
        db.session.refresh(alicia)
        assert alicia.full_name == "Alicia Romero-Diaz"

    def test_clearing_the_birthday(self, db, client, alicia):
        save(client, birthdate="1989-04-12")
        save(client, birthdate="")
        db.session.refresh(alicia)
        assert alicia.birthdate is None

    def test_staff_see_that_they_changed_it(self, db, client, alicia):
        save(client, phone="573-555-0199")
        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == alicia.id)
        ).all()
        assert any("Updated their own details" in e.summary for e in events)
        assert any("phone" in (e.detail or "") for e in events)

    def test_saving_nothing_new_is_not_an_event(self, db, client, alicia):
        response = save(client, phone=alicia.phone)
        assert b"Nothing changed." in response.data
        assert db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == alicia.id)
        ).all() == []

    def test_a_name_cannot_be_emptied(self, db, client, alicia):
        response = save(client, first_name="", last_name="")
        assert b"We need both a first and last name." in response.data
        db.session.refresh(alicia)
        assert alicia.full_name == "Alicia Romero"

    @pytest.mark.parametrize("bad", ["yesterday", "12/04/1989", "1989-13-45"])
    def test_a_birthday_that_is_not_a_date(self, db, client, alicia, bad):
        response = save(client, birthdate=bad)
        assert b"did not look like a date" in response.data
        db.session.refresh(alicia)
        assert alicia.birthdate is None

    def test_a_birthday_in_the_future(self, db, client, alicia):
        ahead = (date.today() + timedelta(days=1)).isoformat()
        response = save(client, birthdate=ahead)
        assert b"cannot be in the future" in response.data
        db.session.refresh(alicia)
        assert alicia.birthdate is None

    def test_long_values_are_cut_not_refused(self, db, client, alicia):
        save(client, first_name="A" * 200, last_name="B" * 200, phone="9" * 90)
        db.session.refresh(alicia)
        assert len(alicia.first_name) == 80
        assert len(alicia.phone) == 40


class TestEmailStaysWithTheOffice:
    def test_there_is_no_email_field(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data
        assert b'name="email"' not in page
        assert b"the church office changes that one" in page

    def test_posting_one_anyway_changes_nothing(self, db, client, alicia):
        save(client, email="someone.else@example.com")
        db.session.refresh(alicia)
        assert alicia.email == "member@journeychurchsemo.com"


class TestNobodyElsesRecord:
    def test_an_id_in_the_form_is_ignored(self, db, client, alicia):
        other = Person(church_id=alicia.church_id, first_name="Ben", last_name="Carter",
                       stage="member", approved_at=utcnow())
        db.session.add(other)
        db.session.commit()
        save(client, person_id=other.id, id=other.id, first_name="Taken", last_name="Over")
        db.session.refresh(other)
        db.session.refresh(alicia)
        assert other.full_name == "Ben Carter"
        assert alicia.full_name == "Taken Over"  # their own record, as asked

    def test_a_login_with_no_roster_record(self, db, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        response = client.post("/me/you/details/", data={"first_name": "A", "last_name": "B"},
                               headers=H)
        assert response.status_code == 302

    def test_what_they_type_is_escaped(self, db, client, alicia):
        save(client, first_name="<script>alert(1)</script>", last_name="Romero")
        page = client.get("/me/you/", headers=H).data
        assert b"<script>alert(1)</script>" not in page
        assert b"&lt;script&gt;" in page


class TestTheFamilyAddress:
    @pytest.fixture
    def with_household(self, db, alicia):
        home = Household(church_id=alicia.church_id, name="The Romero family")
        db.session.add(home)
        db.session.flush()
        alicia.household_id = home.id
        db.session.commit()
        return home

    def test_saving_an_address(self, db, client, alicia, with_household):
        save(client, address_line="412 Oak Street", city="Jackson", postal_code="63755")
        db.session.refresh(with_household)
        assert with_household.address_line == "412 Oak Street"
        assert with_household.city == "Jackson"
        assert with_household.postal_code == "63755"

    def test_the_screen_says_it_is_shared(self, db, client, alicia, with_household):
        page = client.get("/me/you/", headers=H).data
        assert b'name="address_line"' in page
        assert b"shared with everyone in your family" in page

    def test_no_household_means_no_address_fields(self, db, client, alicia):
        page = client.get("/me/you/", headers=H).data
        assert b'name="address_line"' not in page

    def test_saving_without_a_household_still_works(self, db, client, alicia):
        response = save(client, phone="573-555-0111", address_line="Nowhere")
        assert b"Saved. Thank you." in response.data
        db.session.refresh(alicia)
        assert alicia.phone == "573-555-0111"

    def test_the_check_in_code_moved_into_the_family_card(self, db, client, alicia, with_household):
        page = client.get("/me/you/", headers=H).data.decode()
        # Between the Family row and the next row: the code belongs with the
        # family, not on a screen of its own.
        family = page[page.index('id="family"'):page.index('id="notifications"')]
        assert "Check-in code" in family
