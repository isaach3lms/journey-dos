"""Increment 7: the Tithely link-out.

Almost all of the risk here is in one place. A staff member types a URL and a
congregation clicks it, so `validate_giving_url` is doing security work, not
tidying.
"""

import pytest

from app.giving import (
    PROVIDER_TITHELY,
    InvalidGivingURL,
    provider_label,
    validate_giving_url,
)
from app.models import Church, Person, User
from tests.conftest import JOURNEY_HOST

GOOD_ADMIN = "https://tithe.ly/account/dashboard"
GOOD_FORM = "https://tithe.ly/give_new/www/#/tithely/give-one-time/1234"
MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def configured(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.giving_provider = PROVIDER_TITHELY
    church.giving_admin_url = GOOD_ADMIN
    church.giving_form_url = GOOD_FORM
    db.session.commit()
    return church


@pytest.fixture
def linked_member(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    person = Person(
        church_id=church.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="attender",
    )
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == church.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


class TestURLValidation:
    def test_a_normal_tithely_link_passes(self):
        assert validate_giving_url(GOOD_ADMIN) == GOOD_ADMIN
        assert validate_giving_url(GOOD_FORM) == GOOD_FORM

    def test_a_subdomain_passes(self):
        assert validate_giving_url("https://give.tithe.ly/x") is not None

    def test_whitespace_is_trimmed(self):
        assert validate_giving_url(f"  {GOOD_ADMIN}  ") == GOOD_ADMIN

    def test_empty_clears_the_field(self):
        assert validate_giving_url("") is None
        assert validate_giving_url("   ") is None
        assert validate_giving_url(None) is None

    @pytest.mark.parametrize(
        "hostile",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
        ],
    )
    def test_a_script_scheme_is_refused(self, hostile):
        """An href runs in the clicking member's browser with their session."""
        with pytest.raises(InvalidGivingURL, match="https"):
            validate_giving_url(hostile)

    def test_plain_http_is_refused(self):
        with pytest.raises(InvalidGivingURL, match="https"):
            validate_giving_url("http://tithe.ly/give")

    def test_another_host_is_refused(self):
        with pytest.raises(InvalidGivingURL, match="tithe.ly"):
            validate_giving_url("https://evil.example.com/give")

    def test_a_lookalike_host_is_refused(self):
        """`endswith` alone would accept this. That is the whole trick."""
        with pytest.raises(InvalidGivingURL):
            validate_giving_url("https://nottithe.ly/give")
        with pytest.raises(InvalidGivingURL):
            validate_giving_url("https://tithe.ly.evil.example.com/give")

    def test_userinfo_smuggling_is_refused(self):
        """https://tithe.ly@evil.example.com reads as Tithely and is not."""
        with pytest.raises(InvalidGivingURL, match="plain link"):
            validate_giving_url("https://tithe.ly@evil.example.com/give")

    def test_a_bare_word_is_refused(self):
        with pytest.raises(InvalidGivingURL):
            validate_giving_url("tithe.ly")

    def test_an_absurdly_long_value_is_refused(self):
        with pytest.raises(InvalidGivingURL, match="too long"):
            validate_giving_url("https://tithe.ly/" + "a" * 600)

    def test_the_error_says_what_to_do(self):
        with pytest.raises(InvalidGivingURL) as err:
            validate_giving_url("https://evil.example.com")
        assert "tithe.ly" in str(err.value)

    def test_provider_label(self):
        assert provider_label(PROVIDER_TITHELY) == "Tithely"


class TestStaffScreen:
    def test_staff_can_open_it(self, staff):
        r = staff.get("/giving/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"You keep the platform you already use" in r.data

    def test_it_says_so_when_nothing_is_set_up(self, staff):
        r = staff.get("/giving/", headers={"Host": JOURNEY_HOST})
        assert b"No giving link set up yet" in r.data

    def test_saving_links(self, db, staff):
        staff.post(
            "/giving/",
            data={"provider": "tithely", "admin_url": GOOD_ADMIN, "form_url": GOOD_FORM},
            headers={"Host": JOURNEY_HOST},
        )
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.giving_admin_url == GOOD_ADMIN
        assert church.giving_form_url == GOOD_FORM

    def test_a_hostile_link_is_refused_with_a_message(self, db, staff):
        r = staff.post(
            "/giving/",
            data={"admin_url": "javascript:alert(1)"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"https" in r.data
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.giving_admin_url is None

    def test_one_bad_link_does_not_save_the_good_one(self, db, staff):
        """Both or neither, so a half-saved configuration cannot happen."""
        staff.post(
            "/giving/",
            data={"admin_url": GOOD_ADMIN, "form_url": "https://evil.example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.giving_admin_url is None
        assert church.giving_form_url is None

    def test_links_can_be_cleared(self, db, staff, configured):
        staff.post(
            "/giving/",
            data={"admin_url": "", "form_url": ""},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(configured)
        assert configured.giving_admin_url is None

    def test_the_link_opens_safely(self, staff, configured):
        """target=_blank without noopener hands window.opener to the new page."""
        r = staff.get("/giving/", headers={"Host": JOURNEY_HOST})
        body = r.get_data(as_text=True)
        assert 'rel="noopener noreferrer"' in body
        assert GOOD_ADMIN in body

    def test_a_leader_cannot_reach_it(self, leader):
        assert leader.get("/giving/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_member_cannot_reach_it(self, member):
        assert member.get("/giving/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_leader_cannot_change_the_links(self, db, leader):
        r = leader.post(
            "/giving/",
            data={"admin_url": GOOD_ADMIN},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403


class TestTenantIsolation:
    def test_configuring_only_touches_this_church(self, db, staff):
        staff.post(
            "/giving/",
            data={"admin_url": GOOD_ADMIN, "form_url": GOOD_FORM},
            headers={"Host": JOURNEY_HOST},
        )
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        assert riverbend.giving_admin_url is None


class TestMemberGiveTab:
    def test_the_tab_goes_straight_to_the_giving_page(
        self, db, configured, linked_member, member
    ):
        """One tap, not two: the tab is the church's giving page."""
        body = member.get("/me/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert GOOD_FORM in body
        assert 'rel="noopener noreferrer"' in body
        # target=_blank so the iOS wrapper opens the system browser rather
        # than replacing the app with a page that has no way back.
        assert 'target="_blank"' in body

    def test_the_tab_is_hidden_when_it_is_not(self, db, linked_member, member):
        """A tab that leads to an apology is worse than no tab."""
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"/me/give/" not in r.data
        assert GOOD_FORM.encode() not in r.data

    def test_an_old_link_to_the_give_page_still_lands_there(
        self, db, configured, linked_member, member
    ):
        """A bookmark or an emailed link from before the tab changed."""
        r = member.get("/me/give/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert r.headers["Location"] == GOOD_FORM

    def test_it_says_so_when_the_church_has_not_set_it_up(
        self, db, linked_member, member
    ):
        r = member.get("/me/give/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"has not set up online giving" in r.data

    def test_a_signed_out_visitor_cannot_reach_it(self, client):
        r = client.get("/me/give/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_member_route_still_takes_no_person_id(self, app):
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "member.give":
                assert rule.arguments == set()


class TestNoPaymentSurface:
    """The claim that makes this increment worth shipping as it is."""

    def test_no_model_stores_a_card_or_an_amount(self):
        from app.models import Church

        columns = {c.name for c in Church.__table__.columns}
        for forbidden in ("card", "cvv", "account_number", "routing"):
            assert not any(forbidden in name for name in columns)

    def test_the_church_row_holds_links_and_nothing_else(self, db, configured):
        assert configured.giving_admin_url.startswith("https://")
        assert configured.giving_is_configured
