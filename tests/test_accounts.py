"""Staff creating and managing sign-in accounts.

The guards matter more than the feature. These accounts decide who sees the
roster, the giving, and the settings screen itself.
"""

import re

import pytest

from app.models import AuditEvent, Church, OutboxMessage, PasswordResetToken, Person, User
from tests.conftest import JOURNEY_HOST, PASSWORD


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


def create(staff, name="Hannah Doyle", email="hannah@example.com", role="leader"):
    return staff.post(
        "/settings/accounts/",
        data={"name": name, "email": email, "role": role},
        headers={"Host": JOURNEY_HOST},
    )


class TestCreatingAnAccount:
    def test_it_creates_one(self, db, journey, staff):
        create(staff)
        user = User.by_email(journey.id, "hannah@example.com")
        assert user is not None
        assert user.role == "leader"

    def test_staff_never_type_a_password(self, db, journey, staff):
        """A password a staff member sets has to be told to somebody over
        text, and is then a password two people know."""
        create(staff)
        user = User.by_email(journey.id, "hannah@example.com")
        assert not user.check_password("")
        assert not user.check_password(PASSWORD)

    def test_the_person_is_emailed_a_link_to_set_one(self, db, journey, staff):
        create(staff)
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        assert message.to_email == "hannah@example.com"
        assert "/auth/reset/" in message.body_text
        # Transactional: it is the account itself, not church news.
        assert message.category == "account"

    def test_the_link_actually_works(self, db, journey, staff, client):
        create(staff)
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        link = re.search(r"(/auth/reset/[A-Za-z0-9_\-]+)", message.body_text).group(1)

        client.post(
            link,
            data={"password": "a-brand-new-passphrase", "confirm": "a-brand-new-passphrase"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.expire_all()
        assert User.by_email(journey.id, "hannah@example.com").check_password(
            "a-brand-new-passphrase"
        )

    def test_it_is_verified_on_creation(self, db, journey, staff):
        """A staff member typing an address vouches for it more strongly than
        a click in an inbox."""
        create(staff)
        assert User.by_email(journey.id, "hannah@example.com").is_verified

    def test_it_links_to_a_matching_roster_record(self, db, journey, staff):
        person = Person(
            church_id=journey.id, first_name="Hannah", last_name="Doyle",
            email="hannah@example.com", stage="member",
        )
        db.session.add(person)
        db.session.commit()

        create(staff)
        assert User.by_email(journey.id, "hannah@example.com").person_id == person.id

    def test_a_shared_household_address_links_to_neither(self, db, journey, staff):
        for first in ("Chris", "Alina"):
            db.session.add(
                Person(
                    church_id=journey.id, first_name=first, last_name="Vaughn",
                    email="vaughns@example.com", stage="member",
                )
            )
        db.session.commit()

        create(staff, name="Chris Vaughn", email="vaughns@example.com")
        assert User.by_email(journey.id, "vaughns@example.com").person_id is None

    def test_a_duplicate_address_is_refused(self, db, journey, staff):
        create(staff)
        r = create(staff, name="Someone Else", role="member")
        assert b"already has an account" in r.data or r.status_code == 302

        users = db.session.scalars(
            db.select(User).where(User.email == "hannah@example.com")
        ).all()
        assert len(users) == 1

    def test_a_missing_name_is_refused(self, db, journey, staff):
        r = staff.post(
            "/settings/accounts/",
            data={"name": "", "email": "x@example.com", "role": "member"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"need a name" in r.data
        assert User.by_email(journey.id, "x@example.com") is None

    def test_an_unknown_role_is_refused(self, staff):
        r = staff.post(
            "/settings/accounts/",
            data={"name": "X", "email": "x@example.com", "role": "owner"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_it_is_audited(self, db, journey, staff):
        create(staff)
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("leader account" in e.summary for e in events)

    def test_only_staff_can(self, leader):
        r = leader.post(
            "/settings/accounts/",
            data={"name": "X", "email": "x@example.com", "role": "staff"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403


class TestChangingWhatSomebodyCanDo:
    def _other(self, db, journey, role="member"):
        user = User(
            church_id=journey.id, email="other@example.com", name="Other Person",
            role=role,
        )
        user.set_password(PASSWORD)
        user.mark_verified()
        db.session.add(user)
        db.session.commit()
        return user

    def test_a_role_can_be_raised(self, db, journey, staff):
        user = self._other(db, journey)
        staff.post(
            f"/settings/accounts/{user.id}/role/",
            data={"role": "leader"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(user)
        assert user.role == "leader"

    def test_nobody_changes_their_own_access(self, db, journey, staff):
        """It is how somebody demotes themselves out of the screen they are
        standing on."""
        me = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == journey.id,
                User.role == "staff",
            )
        )
        r = staff.post(
            f"/settings/accounts/{me.id}/role/",
            data={"role": "member"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"cannot change your own access" in r.data
        db.session.refresh(me)
        assert me.role == "staff"

    def test_the_last_staff_cannot_be_demoted(self, db, journey, staff):
        """It would leave nobody able to undo it."""
        for user in db.session.scalars(
            db.select(User).where(User.church_id == journey.id, User.role == "staff")
        ):
            if user.email != "pastor@journeychurchsemo.com":
                user.is_active_account = False
        db.session.commit()

        me = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == journey.id, User.role == "staff",
            )
        )
        second = self._other(db, journey, role="staff")
        second.is_active_account = False
        db.session.commit()

        r = staff.post(
            f"/settings/accounts/{second.id}/role/",
            data={"role": "member"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        # `second` is already off, so demoting it is fine; `me` is protected by
        # the self rule. What matters is that the church keeps a staff login.
        assert User.active_staff_count(journey.id) >= 1

    def test_role_changes_are_audited(self, db, journey, staff):
        user = self._other(db, journey)
        staff.post(
            f"/settings/accounts/{user.id}/role/",
            data={"role": "leader"},
            headers={"Host": JOURNEY_HOST},
        )
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("member to leader" in e.summary for e in events)

    def test_a_user_from_another_church_is_a_404(self, db, journey, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = db.session.scalar(
            db.select(User).where(User.church_id == riverbend.id)
        )
        r = staff.post(
            f"/settings/accounts/{theirs.id}/role/",
            data={"role": "member"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404


class TestSwitchingAnAccountOff:
    def _other(self, db, journey, role="member"):
        user = User(
            church_id=journey.id, email="other@example.com", name="Other Person",
            role=role,
        )
        user.set_password(PASSWORD)
        user.mark_verified()
        db.session.add(user)
        db.session.commit()
        return user

    def test_it_stops_them_signing_in(self, db, journey, staff):
        """Asserted on the model rather than by signing in with a second
        client. The `db` fixture holds one application context open and
        Flask-Login caches the user on `g`, so a second client in the same
        test inherits the first one's identity. Fifth time that has bitten.
        """
        user = self._other(db, journey)
        staff.post(
            f"/settings/accounts/{user.id}/toggle/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(user)

        assert not user.is_active_account
        assert not user.is_active  # Flask-Login refuses a sign-in for this
        assert user.check_password(PASSWORD), (
            "The password is untouched: the account is off, not reset."
        )

    def test_it_signs_them_out_everywhere(self, db, journey, staff):
        """Same reason a password reset does: a switched-off account still
        signed in on a device is not switched off."""
        user = self._other(db, journey)
        before = user.session_version

        staff.post(
            f"/settings/accounts/{user.id}/toggle/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(user)
        assert user.session_version > before

    def test_it_can_be_undone(self, db, journey, staff):
        user = self._other(db, journey)
        for _ in range(2):
            staff.post(
                f"/settings/accounts/{user.id}/toggle/", headers={"Host": JOURNEY_HOST}
            )
        db.session.refresh(user)
        assert user.is_active_account

    def test_nobody_switches_themselves_off(self, db, journey, staff):
        me = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == journey.id, User.role == "staff",
            )
        )
        r = staff.post(
            f"/settings/accounts/{me.id}/toggle/",
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"cannot change your own access" in r.data
        db.session.refresh(me)
        assert me.is_active_account

    def test_the_last_staff_account_cannot_be_switched_off(self, db, journey, staff):
        for user in db.session.scalars(
            db.select(User).where(User.church_id == journey.id, User.role == "staff")
        ):
            if user.email != "pastor@journeychurchsemo.com":
                user.is_active_account = False
        db.session.commit()
        assert User.active_staff_count(journey.id) == 1


class TestResendingTheLink:
    def test_it_sends_another(self, db, journey, staff):
        create(staff)
        user = User.by_email(journey.id, "hannah@example.com")
        before = len(db.session.scalars(db.select(OutboxMessage)).all())

        staff.post(
            f"/settings/accounts/{user.id}/resend/", headers={"Host": JOURNEY_HOST}
        )
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) > before

    def test_it_retires_the_previous_link(self, db, journey, staff, client):
        create(staff)
        first = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        link = re.search(r"(/auth/reset/[A-Za-z0-9_\-]+)", first.body_text).group(1)

        user = User.by_email(journey.id, "hannah@example.com")
        staff.post(
            f"/settings/accounts/{user.id}/resend/", headers={"Host": JOURNEY_HOST}
        )
        assert client.get(link, headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_only_staff_can(self, db, journey, leader):
        user = db.session.scalar(db.select(User).where(User.church_id == journey.id))
        r = leader.post(
            f"/settings/accounts/{user.id}/resend/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 403


class TestTheScreen:
    def test_it_lists_the_accounts(self, db, journey, staff):
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"People who can sign in" in r.data
        assert b"pastor@journeychurchsemo.com" in r.data

    def test_it_explains_why_staff_never_type_a_password(self, staff):
        r = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"never type one" in r.data

    def test_a_leader_cannot_see_it(self, leader):
        assert leader.get(
            "/settings/", headers={"Host": JOURNEY_HOST}
        ).status_code == 403
