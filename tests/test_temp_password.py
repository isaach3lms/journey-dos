"""Staff handing somebody a password to use once.

For a person whose email is dead, or who is standing in front of you. Staff
never choose the password: one a staff member picks becomes a password two
people know, is usually sent over text, and is often one the person already
uses elsewhere.
"""

import re

import pytest

from app.models import AuditEvent, Church, User
from tests.conftest import JOURNEY_HOST, PASSWORD


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def target(db, journey):
    user = User(
        church_id=journey.id, email="olive@example.com", name="Olive Marsh",
        role="member",
    )
    user.set_password(PASSWORD)
    user.mark_verified()
    db.session.add(user)
    db.session.commit()
    return user


def issue(staff, user):
    return staff.post(
        f"/settings/accounts/{user.id}/temp-password/",
        headers={"Host": JOURNEY_HOST}, follow_redirects=True,
    )


def password_from(response) -> str:
    body = response.get_data(as_text=True)
    return re.search(r"password for [^:]+: ([A-Za-z0-9\-]+)\.", body).group(1)


class TestIssuingOne:
    def test_it_is_shown_once_on_screen(self, db, journey, target, staff):
        r = issue(staff, target)
        raw = password_from(r)
        assert len(raw) >= 12
        db.session.refresh(target)
        assert target.check_password(raw)

    def test_the_old_password_stops_working(self, db, target, staff):
        issue(staff, target)
        db.session.refresh(target)
        assert not target.check_password(PASSWORD)

    def test_it_avoids_characters_that_get_misheard(self, db, target, staff):
        """Staff will be reading this to somebody down a phone line."""
        raw = password_from(issue(staff, target))
        assert not re.search(r"[O0Il1S5Z2]", raw)

    def test_it_is_never_the_same_twice(self, db, target, staff):
        first = password_from(issue(staff, target))
        second = password_from(issue(staff, target))
        assert first != second

    def test_it_signs_them_out_everywhere(self, db, target, staff):
        """A session opened with the old password must not survive."""
        before = target.session_version
        issue(staff, target)
        db.session.refresh(target)
        assert target.session_version > before

    def test_it_clears_a_lockout(self, db, target, staff):
        target.failed_login_count = 9
        db.session.commit()
        issue(staff, target)
        db.session.refresh(target)
        assert target.failed_login_count == 0
        assert target.locked_until is None

    def test_the_audit_entry_does_not_contain_the_password(self, db, journey, target, staff):
        """The log is designed to be read, kept, and exported. A working
        password in it is worse than no log."""
        raw = password_from(issue(staff, target))
        event = db.session.scalars(AuditEvent.recent(journey.id)).first()
        assert "temporary password" in event.summary
        assert raw not in f"{event.summary} {event.detail} {event.subject_label}"

    def test_nobody_issues_one_to_themselves(self, db, journey, staff):
        me = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == journey.id, User.role == "staff",
            )
        )
        r = issue(staff, me)
        assert b"forgot-password link for your own account" in r.data
        db.session.refresh(me)
        assert not me.must_change_password

    def test_a_leader_cannot(self, db, target, leader):
        r = leader.post(
            f"/settings/accounts/{target.id}/temp-password/",
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        db.session.refresh(target)
        assert target.check_password(PASSWORD)

    def test_a_user_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = db.session.scalar(db.select(User).where(User.church_id == riverbend.id))
        r = staff.post(
            f"/settings/accounts/{theirs.id}/temp-password/",
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404


def issue_directly(db, user) -> str:
    """Issue through the model rather than the staff client.

    Every test below signs in as the affected person, and the `db` fixture
    holds one application context open while Flask-Login caches the user on
    `g`. A test that used the staff client first would find the second client
    inheriting the staff identity. That trap has now bitten seven times.
    """
    raw = user.issue_temporary_password()
    db.session.commit()
    return raw


class TestItMustBeReplaced:
    """A temporary password that stays in use is a shared password with a
    nicer name."""

    def test_the_flag_is_set(self, db, target, staff):
        issue(staff, target)
        db.session.refresh(target)
        assert target.must_change_password

    def test_every_page_redirects_to_the_change_screen(self, db, target, app):
        raw = issue_directly(db, target)

        client = app.test_client()
        client.post(
            "/auth/login",
            data={"email": "olive@example.com", "password": raw},
            headers={"Host": JOURNEY_HOST},
        )
        for path in ("/", "/me/", "/people/"):
            r = client.get(path, headers={"Host": JOURNEY_HOST})
            assert r.status_code == 302, path
            assert "/auth/change-password" in r.headers["Location"], path

    def test_signing_out_still_works(self, db, target, app):
        """Otherwise somebody is trapped on one screen with no way off it."""
        raw = issue_directly(db, target)
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"email": "olive@example.com", "password": raw},
            headers={"Host": JOURNEY_HOST},
        )
        r = client.post("/auth/logout", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/change-password" not in r.headers["Location"]

    def test_changing_it_lets_them_in(self, db, target, app):
        raw = issue_directly(db, target)
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"email": "olive@example.com", "password": raw},
            headers={"Host": JOURNEY_HOST},
        )
        client.post(
            "/auth/change-password",
            data={"password": "a-password-only-i-know", "confirm": "a-password-only-i-know"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        user = User.by_email(target.church_id, "olive@example.com")
        assert not user.must_change_password
        assert user.check_password("a-password-only-i-know")

    def test_reusing_the_temporary_one_is_refused(self, db, target, app):
        raw = issue_directly(db, target)
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"email": "olive@example.com", "password": raw},
            headers={"Host": JOURNEY_HOST},
        )
        r = client.post(
            "/auth/change-password",
            data={"password": raw, "confirm": raw},
            headers={"Host": JOURNEY_HOST},
        )
        assert b"That is the temporary one" in r.data
        db.session.expire_all()
        assert User.by_email(target.church_id, "olive@example.com").must_change_password

    def test_a_short_password_is_refused(self, db, target, app):
        raw = issue_directly(db, target)
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"email": "olive@example.com", "password": raw},
            headers={"Host": JOURNEY_HOST},
        )
        client.post(
            "/auth/change-password",
            data={"password": "short", "confirm": "short"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert User.by_email(target.church_id, "olive@example.com").must_change_password

    def test_the_emailed_reset_link_also_clears_it(self, db, journey, target, client):
        """Whichever route they took, they have chosen their own password."""
        from app.models import OutboxMessage

        issue_directly(db, target)
        client.post(
            "/auth/forgot", data={"email": "olive@example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        link = re.search(r"(/auth/reset/[A-Za-z0-9_\-]+)", message.body_text).group(1)

        client.post(
            link,
            data={"password": "chosen-by-me-entirely", "confirm": "chosen-by-me-entirely"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert not User.by_email(journey.id, "olive@example.com").must_change_password


class TestTheScreen:
    def test_the_button_is_there(self, db, target, staff):
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"Give them a temporary password" in r.data

    def test_it_explains_why_staff_do_not_choose_it(self, staff):
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"never becomes a password two people know" in r.data
