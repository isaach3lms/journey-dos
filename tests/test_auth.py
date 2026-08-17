"""Increment 1: identity and roles.

The tests that matter most in this file are in `TestCrossTenantIsolation`.
Everything else is ordinary login behavior; those are the ones that catch the
failure mode unique to a multi-tenant app.
"""

import pytest

from app.models import User
from app.models.user import MAX_FAILED_LOGINS
from app.security import safe_next_url
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

STAFF = "pastor@journeychurchsemo.com"
LEADER = "leader@journeychurchsemo.com"
MEMBER = "member@journeychurchsemo.com"
DEACTIVATED = "gone@journeychurchsemo.com"


class TestSigningIn:
    def test_the_login_page_is_public(self, client):
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Sign in" in r.data

    def test_correct_credentials_reach_the_dashboard(self, sign_in):
        r = sign_in(STAFF)
        assert r.status_code == 200
        assert b"Foundation" in r.data
        assert b"Pastor Reed" in r.data

    def test_a_wrong_password_is_rejected(self, sign_in):
        r = sign_in(STAFF, password="not-the-password", follow=False)
        assert r.status_code == 401
        assert b"do not match" in r.data

    def test_a_deactivated_account_cannot_sign_in(self, sign_in):
        r = sign_in(DEACTIVATED, follow=False)
        assert r.status_code == 401

    def test_email_is_case_insensitive(self, sign_in):
        r = sign_in(STAFF.upper())
        assert b"Pastor Reed" in r.data

    def test_signing_out_ends_the_session(self, staff):
        assert staff.get("/", headers={"Host": JOURNEY_HOST}).status_code == 200
        staff.post("/auth/logout", headers={"Host": JOURNEY_HOST})
        assert staff.get("/", headers={"Host": JOURNEY_HOST}).status_code == 302

    def test_logout_refuses_a_get(self, staff):
        """A GET logout fires from any image tag on any page on the internet."""
        r = staff.get("/auth/logout", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 405

    def test_last_login_is_recorded(self, db, sign_in):
        sign_in(STAFF)
        user = db.session.scalar(db.select(User).where(User.email == STAFF, User.role == "staff"))
        assert user.last_login_at is not None


class TestUserEnumeration:
    """A login form must not reveal who attends the church."""

    def test_unknown_and_wrong_password_give_the_same_message(self, sign_in):
        unknown = sign_in("stranger@journeychurchsemo.com", follow=False)
        wrong = sign_in(STAFF, password="not-the-password", follow=False)
        assert unknown.status_code == wrong.status_code == 401
        assert b"do not match" in unknown.data
        assert b"do not match" in wrong.data

    def test_a_deactivated_account_is_not_distinguishable(self, sign_in):
        gone = sign_in(DEACTIVATED, follow=False)
        unknown = sign_in("stranger@journeychurchsemo.com", follow=False)
        assert gone.status_code == unknown.status_code


class TestLockout:
    def test_repeated_failures_lock_the_account(self, db, sign_in):
        for _ in range(MAX_FAILED_LOGINS):
            sign_in(STAFF, password="wrong", follow=False)

        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        assert user.is_locked

        # Even the correct password is refused while locked.
        r = sign_in(STAFF, follow=False)
        assert r.status_code == 429
        assert b"locked" in r.data

    def test_a_successful_login_clears_the_counter(self, db, sign_in):
        sign_in(STAFF, password="wrong", follow=False)
        sign_in(STAFF)
        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        assert user.failed_login_count == 0


class TestCrossTenantIsolation:
    """The failure mode that only exists in a multi-tenant app."""

    def test_a_session_from_one_church_is_rejected_at_another(self, client, sign_in):
        sign_in(STAFF, host=JOURNEY_HOST)
        assert client.get("/", headers={"Host": JOURNEY_HOST}).status_code == 200

        # Same cookie jar, different tenant host. This is the replay.
        r = client.get("/", headers={"Host": RIVERBEND_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_same_address_at_two_churches_is_two_accounts(self, client, sign_in):
        """Riverbend's pastor shares an address with Journey's. Different people."""
        sign_in(STAFF, host=RIVERBEND_HOST)
        r = client.get("/", headers={"Host": RIVERBEND_HOST})
        assert b"Other Pastor" in r.data
        assert b"Pastor Reed" not in r.data

    def test_a_tampered_cookie_signs_the_user_out_rather_than_erroring(
        self, client, sign_in
    ):
        from app.security import load_user

        sign_in(STAFF, host=JOURNEY_HOST)
        with client.application.test_request_context(
            "/", headers={"Host": JOURNEY_HOST}
        ):
            from flask import g

            from app.tenancy import resolve_church

            g.church = resolve_church()
            assert load_user("not-a-composite-id") is None
            assert load_user("99999:1") is None

    def test_get_id_carries_the_church(self, db):
        user = db.session.scalar(db.select(User).where(User.email == STAFF))
        assert user.get_id() == f"{user.church_id}:{user.id}"

    def test_the_session_cookie_is_not_shared_across_subdomains(self, app):
        """The third leg of the defense, after get_id and load_user."""
        assert not app.config.get("SESSION_COOKIE_DOMAIN")

    def test_boot_fails_if_a_shared_cookie_domain_is_ever_configured(self, app):
        from app.security import assert_cookie_scope_is_safe

        app.config["SESSION_COOKIE_DOMAIN"] = ".betweensundaysdos.com"
        with pytest.raises(RuntimeError, match="cross-tenant"):
            assert_cookie_scope_is_safe(app)


class TestRoles:
    # Asserting on link targets, not on visible words. The roadmap card on the
    # dashboard lists every increment by name, so "Settings" appears in the
    # body copy for everyone. Only the sidebar href is the navigation.
    @staticmethod
    def _nav_targets(response) -> set[str]:
        import re

        body = response.get_data(as_text=True)
        sidebar = body[body.index('class="navstrip"'): body.index('class="sidefoot"')]
        return set(re.findall(r'href="(/[a-z]*/?)"', sidebar))

    def test_staff_sees_every_nav_item(self, staff):
        targets = self._nav_targets(staff.get("/", headers={"Host": JOURNEY_HOST}))
        assert {"/", "/people/", "/services/", "/kids/", "/giving/",
                "/resources/", "/messages/", "/settings/"} <= targets

    def test_a_member_does_not_see_staff_navigation(self, member):
        targets = self._nav_targets(member.get("/", headers={"Host": JOURNEY_HOST}))
        assert "/giving/" not in targets
        assert "/settings/" not in targets
        assert "/people/" not in targets
        # But they do see what is theirs.
        assert {"/", "/resources/", "/messages/"} <= targets

    def test_a_leader_sees_people_but_not_giving(self, leader):
        targets = self._nav_targets(leader.get("/", headers={"Host": JOURNEY_HOST}))
        assert "/people/" in targets
        assert "/giving/" not in targets
        assert "/settings/" not in targets

    def test_hiding_a_link_is_not_the_enforcement(self, member):
        """A member who guesses the URL is still refused."""
        r = member.get("/giving/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 403
        assert b"do not have access" in r.data

    def test_a_leader_is_refused_the_staff_only_area(self, leader):
        assert leader.get("/settings/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_a_leader_reaches_a_leader_area(self, leader):
        assert leader.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 200

    def test_role_hierarchy(self, db):
        staff = db.session.scalar(db.select(User).where(User.role == "staff", User.email == STAFF))
        member = db.session.scalar(db.select(User).where(User.role == "member"))
        assert staff.at_least("member")
        assert staff.at_least("staff")
        assert not member.at_least("leader")
        assert member.at_least("member")

    def test_an_invalid_role_is_rejected_by_the_database(self, db):
        from app.models import Church

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = User(
            church_id=church.id,
            email="odd@journeychurchsemo.com",
            name="Odd",
            role="superadmin",
        )
        user.set_password(PASSWORD)
        db.session.add(user)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


class TestAccessControl:
    def test_signed_out_users_are_sent_to_login(self, client):
        r = client.get("/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_where_they_were_going_is_preserved(self, client):
        r = client.get("/people/", headers={"Host": JOURNEY_HOST})
        assert "next=%2Fpeople%2F" in r.headers["Location"]

    def test_health_endpoints_stay_public(self, client):
        assert client.get("/healthz", headers={"Host": JOURNEY_HOST}).status_code == 200


class TestOpenRedirect:
    """`?next=` is an open redirect unless every value is validated."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "https://evil.example.com/steal",
            "//evil.example.com/steal",
            "http://evil.example.com",
            "evil.example.com",
        ],
    )
    def test_offsite_targets_are_discarded(self, app, hostile):
        with app.test_request_context("/", headers={"Host": JOURNEY_HOST}):
            assert safe_next_url(hostile, "shell.index") == "/"

    def test_onsite_targets_are_kept(self, app):
        with app.test_request_context("/", headers={"Host": JOURNEY_HOST}):
            assert safe_next_url("/people/", "shell.index") == "/people/"

    def test_no_target_falls_back(self, app):
        with app.test_request_context("/", headers={"Host": JOURNEY_HOST}):
            assert safe_next_url(None, "shell.index") == "/"


class TestPasswords:
    def test_short_passwords_are_refused(self, db):
        user = db.session.scalar(db.select(User).where(User.email == STAFF))
        with pytest.raises(ValueError, match="12 characters"):
            user.set_password("short")

    def test_the_hash_is_never_the_password(self, db):
        user = db.session.scalar(db.select(User).where(User.email == STAFF))
        assert PASSWORD not in user.password_hash

    def test_two_users_with_one_password_get_different_hashes(self, db):
        a = db.session.scalar(db.select(User).where(User.email == STAFF, User.role == "staff"))
        b = db.session.scalar(db.select(User).where(User.email == LEADER))
        assert a.password_hash != b.password_hash


class TestCSRF:
    def test_csrf_protection_is_on_by_default_outside_tests(self):
        from app.config import DevelopmentConfig, ProductionConfig

        assert getattr(ProductionConfig, "WTF_CSRF_ENABLED", True) is True
        assert getattr(DevelopmentConfig, "WTF_CSRF_ENABLED", True) is True

    def test_a_post_without_a_token_is_rejected_when_csrf_is_on(self, app, client, sign_in):
        sign_in(STAFF)
        app.config["WTF_CSRF_ENABLED"] = True
        r = client.post("/auth/logout", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 400
