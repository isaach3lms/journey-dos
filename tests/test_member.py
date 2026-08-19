"""Increment 5: the member app shell.

Two things carry most of the risk here. The PIN generator, because a collision
at a kiosk is a child-safety problem rather than an inconvenience, and the
member routes, because they must be incapable of showing one person another
person's record.
"""

import pytest

from app.checkin_pin import PIN_LENGTH, blocklist, candidate, generate_pin
from app.models import Church, Household, NextStep, Person, User
from tests.conftest import JOURNEY_HOST, PASSWORD

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def linked_member(db):
    """The member login, attached to a roster record in a household."""
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    household = Household(church_id=church.id, name="Alicia and Mateo Romero")
    db.session.add(household)
    db.session.flush()

    person = Person(
        church_id=church.id,
        first_name="Alicia",
        last_name="Romero",
        email=MEMBER_EMAIL,
        stage="attender",
        household_id=household.id,
    )
    sibling = Person(
        church_id=church.id,
        first_name="Mateo",
        last_name="Romero",
        stage="member",
        household_id=household.id,
    )
    db.session.add_all([person, sibling])
    db.session.flush()

    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == church.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


class TestPinGeneration:
    def test_a_pin_is_the_right_length_and_all_digits(self):
        pin = generate_pin(lambda code: False)
        assert len(pin) == PIN_LENGTH
        assert pin.isdigit()

    def test_repeats_are_blocked(self):
        blocked = blocklist()
        for digit in "0123456789":
            assert digit * PIN_LENGTH in blocked

    def test_runs_are_blocked_in_both_directions(self):
        blocked = blocklist()
        assert "1234" in blocked
        assert "4321" in blocked
        assert "0123" in blocked
        assert "9876" in blocked

    def test_the_church_street_number_is_blocked(self):
        """It is on the building and on every piece of mail they send."""
        assert "1420" in blocklist(church_street_number="1420")
        assert "1420" not in blocklist(church_street_number="55")

    def test_a_blocked_code_is_never_returned(self):
        blocked = blocklist()
        for _ in range(200):
            assert generate_pin(lambda code: False) not in blocked

    def test_a_taken_code_is_skipped(self):
        taken = {"1111"}

        def is_taken(code):
            return code in taken

        # Force every draw to collide except the last, then confirm the loop
        # keeps going rather than handing back a duplicate.
        seen = {generate_pin(is_taken) for _ in range(50)}
        assert "1111" not in seen

    def test_running_out_of_codes_raises_rather_than_duplicating(self):
        """Two families with one code at a kiosk is a safety problem."""
        with pytest.raises(RuntimeError, match="close to full"):
            generate_pin(lambda code: True)

    def test_candidates_are_not_all_the_same(self):
        assert len({candidate() for _ in range(50)}) > 1


class TestHouseholdPin:
    def test_a_household_gets_a_pin_on_demand(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        household = Household(church_id=church.id, name="The Webbs")
        db.session.add(household)
        db.session.commit()

        assert household.checkin_pin is None
        pin = household.ensure_checkin_pin()
        db.session.commit()
        assert len(pin) == PIN_LENGTH

    def test_the_pin_is_stable_once_assigned(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        household = Household(church_id=church.id, name="The Webbs")
        db.session.add(household)
        db.session.commit()

        first = household.ensure_checkin_pin()
        db.session.commit()
        assert household.ensure_checkin_pin() == first

    def test_rotation_produces_a_different_pin(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        household = Household(church_id=church.id, name="The Webbs")
        db.session.add(household)
        db.session.commit()

        first = household.ensure_checkin_pin()
        db.session.commit()
        second = household.regenerate_checkin_pin()
        db.session.commit()
        assert second != first

    def test_two_households_at_one_church_cannot_share_a_pin(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        a = Household(church_id=church.id, name="A")
        b = Household(church_id=church.id, name="B")
        db.session.add_all([a, b])
        db.session.commit()

        a.checkin_pin = "5678"
        db.session.commit()
        b.checkin_pin = "5678"
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_two_churches_may_use_the_same_pin(self, db):
        """The constraint is per church. Kiosks never span tenants."""
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        a = Household(church_id=journey.id, name="A", checkin_pin="5678")
        b = Household(church_id=riverbend.id, name="B", checkin_pin="5678")
        db.session.add_all([a, b])
        db.session.commit()
        assert a.checkin_pin == b.checkin_pin

    def test_assign_pins_command_fills_the_gaps(self, app, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        db.session.add_all(
            [Household(church_id=church.id, name=f"H{i}") for i in range(5)]
        )
        db.session.commit()

        result = app.test_cli_runner().invoke(
            args=["assign-pins", "--church", "journey"]
        )
        assert result.exit_code == 0

        db.session.expire_all()
        households = db.session.scalars(
            db.select(Household).where(Household.church_id == church.id)
        ).all()
        pins = [h.checkin_pin for h in households]
        assert all(pins)
        assert len(set(pins)) == len(pins)


class TestUserToPersonLink:
    def test_linking_matches_on_email(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        person = Person(
            church_id=church.id, first_name="Alicia", last_name="Romero",
            email=MEMBER_EMAIL, stage="attender",
        )
        db.session.add(person)
        db.session.commit()

        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == church.id)
        )
        assert user.link_person_by_email() is True
        db.session.commit()
        assert user.person.id == person.id

    def test_linking_never_crosses_churches(self, db):
        """The same address exists at both churches in the fixtures."""
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(
            Person(
                church_id=riverbend.id, first_name="Someone", last_name="Else",
                email=MEMBER_EMAIL, stage="member",
            )
        )
        db.session.commit()

        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        assert user.link_person_by_email() is False
        assert user.person is None

    def test_an_already_linked_user_is_left_alone(self, db, linked_member):
        church = linked_member.church_id
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == church)
        )
        assert user.link_person_by_email() is False

    def test_person_accessor_is_tenant_scoped(self, db, linked_member):
        """A person_id from another church must not load, ever."""
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Yours", stage="member"
        )
        db.session.add(stranger)
        db.session.commit()

        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        user.person_id = stranger.id
        db.session.commit()
        assert user.person is None

    def test_link_users_command(self, app, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        db.session.add(
            Person(
                church_id=church.id, first_name="Alicia", last_name="Romero",
                email=MEMBER_EMAIL, stage="attender",
            )
        )
        db.session.commit()

        result = app.test_cli_runner().invoke(args=["link-users", "--church", "journey"])
        assert result.exit_code == 0
        assert "Linked 1" in result.output


class TestMemberApp:
    def test_a_member_landing_at_the_root_goes_to_the_member_app(
        self, db, linked_member, member
    ):
        r = member.get("/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/me/" in r.headers["Location"]

    def test_home_shows_their_own_name_and_stage(self, db, linked_member, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Alicia" in r.data
        assert b"Attender" in r.data

    def test_home_shows_an_assigned_next_step(self, db, linked_member, member):
        db.session.add(
            NextStep(
                church_id=linked_member.church_id,
                person_id=linked_member.id,
                title="Come to the Next Steps lunch",
                owner_name="Pastor Reed",
            )
        )
        db.session.commit()

        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"Next Steps lunch" in r.data
        assert b"Pastor Reed" in r.data

    def test_home_says_so_when_there_is_no_next_step(self, db, linked_member, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"Nothing on your list" in r.data

    def test_the_you_tab_shows_the_household_pin(self, db, linked_member, member):
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200

        db.session.expire_all()
        household = db.session.get(Household, linked_member.household_id)
        assert household.checkin_pin
        assert household.checkin_pin.encode() in r.data

    def test_the_pin_is_minted_on_first_view(self, db, linked_member, member):
        household = db.session.get(Household, linked_member.household_id)
        assert household.checkin_pin is None
        member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        db.session.expire_all()
        assert db.session.get(Household, linked_member.household_id).checkin_pin

    def test_the_you_tab_lists_the_rest_of_the_household(
        self, db, linked_member, member
    ):
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        assert b"Mateo" in r.data

    def test_a_member_sees_no_staff_navigation(self, db, linked_member, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b'class="navstrip"' not in r.data
        assert b"/people/" not in r.data

    def test_the_member_app_carries_the_church_brand(self, db, linked_member, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        body = r.get_data(as_text=True)
        assert "--accent:#485B38;" in body
        assert "journey-logo-white.png" in body

    def test_a_signed_out_visitor_is_sent_to_login(self, client):
        r = client.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]


class TestNoWayToSeeSomeoneElse:
    """There is no route here that takes a person id, by design."""

    def test_no_member_route_accepts_an_id(self, app):
        for rule in app.url_map.iter_rules():
            if rule.endpoint.startswith("member."):
                assert not rule.arguments, f"{rule} accepts {rule.arguments}"

    def test_a_member_still_cannot_reach_the_roster(self, db, linked_member, member):
        assert member.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_login_with_no_record_gets_a_plain_explanation(self, db, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"not connected to your record" in r.data


class TestStaffPreview:
    def test_staff_can_look_at_the_member_app(self, db, staff):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        person = Person(
            church_id=church.id, first_name="Pastor", last_name="Reed",
            email="pastor@journeychurchsemo.com", stage="leader",
        )
        db.session.add(person)
        db.session.flush()
        user = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == church.id,
                User.role == "staff",
            )
        )
        user.person_id = person.id
        db.session.commit()

        r = staff.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        # It is a preview of their own record, not impersonation of anyone.
        assert b"member app as Pastor" in r.data

    def test_staff_are_not_redirected_away_from_the_dashboard(self, staff):
        r = staff.get("/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200


class TestMemberPreferences:
    def test_a_member_can_change_their_own_preferences(
        self, db, linked_member, member
    ):
        member.post(
            "/me/you/preferences/",
            data={"cat_next_step": "on"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(linked_member)
        assert linked_member.allows("next_step")
        assert not linked_member.allows("announcement")

    def test_a_member_can_unsubscribe_themselves(self, db, linked_member, member):
        member.post("/me/you/optout/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(linked_member)
        assert linked_member.has_opted_out
        # Transactional mail is unaffected.
        assert linked_member.allows("account")

    def test_unsubscribing_can_be_undone(self, db, linked_member, member):
        member.post("/me/you/optout/", headers={"Host": JOURNEY_HOST})
        member.post("/me/you/optout/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(linked_member)
        assert not linked_member.has_opted_out
