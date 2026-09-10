"""Self-registration.

One rule carries almost all the risk: **a new account is never attached to an
existing person until the address is confirmed.** A linked record holds a
household check-in PIN, giving history, and contact details, so linking on an
unconfirmed address would let anyone who knows a member's email address claim
that member.
"""

import re

import pytest

from app.models import (
    Church,
    EmailVerificationToken,
    OutboxMessage,
    Person,
    SequenceEnrollment,
    User,
)
from app.models.base import utcnow
from app.models.email_verification import MAX_REQUESTS_PER_HOUR, hash_token
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

NEW_EMAIL = "nina.ibarra@example.com"
NEW_PASSWORD = "a-brand-new-passphrase"
STAFF = "pastor@journeychurchsemo.com"


@pytest.fixture
def open_church(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.allow_self_signup = True
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


def join(client, email=NEW_EMAIL, name="Nina Ibarra", host=JOURNEY_HOST):
    return client.post(
        "/auth/join",
        data={"name": name, "email": email, "password": NEW_PASSWORD},
        headers={"Host": host},
    )


def verify_link(db):
    message = db.session.scalars(
        db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
    ).first()
    assert message is not None, "No verification email was queued"
    match = re.search(r"(/auth/verify/[A-Za-z0-9_\-]+)", message.body_text)
    assert match, message.body_text[:200]
    return match.group(1)


class TestTheChurchDecides:
    def test_it_is_off_by_default(self, db):
        """A church that has not thought about it should not discover that
        strangers can read its announcements."""
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.allow_self_signup is False

    def test_a_closed_church_does_not_offer_the_form(self, db, client):
        r = client.get("/auth/join", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404
        assert b"church creates accounts" in r.data

    def test_a_closed_church_refuses_a_posted_signup(self, db, client):
        join(client)
        assert db.session.scalar(
            db.select(User).where(User.email == NEW_EMAIL)
        ) is None

    def test_the_login_page_only_links_it_when_open(self, db, client, open_church):
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert b"/auth/join" in r.data

        open_church.allow_self_signup = False
        db.session.commit()
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert b"/auth/join" not in r.data

    def test_staff_can_open_and_close_it(self, db, staff):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        staff.post("/settings/signup/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(church)
        assert church.allow_self_signup

        staff.post("/settings/signup/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(church)
        assert not church.allow_self_signup

    def test_a_leader_cannot_open_it(self, leader):
        r = leader.post("/settings/signup/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 403

    def test_opening_it_is_audited(self, db, staff):
        from app.models import AuditEvent

        staff.post("/settings/signup/", headers={"Host": JOURNEY_HOST})
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        events = db.session.scalars(AuditEvent.recent(church.id)).all()
        assert any("Self-registration turned on" in e.summary for e in events)

    def test_one_church_opening_does_not_open_another(self, db, open_church, client):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        assert not riverbend.allow_self_signup
        r = client.get("/auth/join", headers={"Host": RIVERBEND_HOST})
        assert r.status_code == 404


class TestSigningUp:
    def test_an_account_is_created_unverified(self, db, client, open_church):
        join(client)
        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        assert user is not None
        assert user.role == "member"
        assert not user.is_verified

    def test_a_confirmation_email_is_queued(self, db, client, open_church):
        join(client)
        message = db.session.scalars(db.select(OutboxMessage)).one()
        assert message.category == "account"
        assert "/auth/verify/" in message.body_text

    def test_an_unverified_account_cannot_sign_in(self, db, client, open_church):
        join(client)
        r = client.post(
            "/auth/login",
            data={"email": NEW_EMAIL, "password": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        assert b"Confirm your email" in r.data

    def test_signing_in_again_resends_the_link(self, db, client, open_church):
        join(client)
        before = len(db.session.scalars(db.select(OutboxMessage)).all())
        client.post(
            "/auth/login",
            data={"email": NEW_EMAIL, "password": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert len(db.session.scalars(db.select(OutboxMessage)).all()) > before

    def test_a_short_password_is_refused(self, db, client, open_church):
        client.post(
            "/auth/join",
            data={"name": "Nina", "email": NEW_EMAIL, "password": "short"},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalar(
            db.select(User).where(User.email == NEW_EMAIL)
        ) is None

    def test_a_new_signup_gets_no_staff_access(self, db, client, open_church):
        join(client)
        client.get(verify_link(db), headers={"Host": JOURNEY_HOST})
        assert client.get("/people/", headers={"Host": JOURNEY_HOST}).status_code == 403
        assert client.get("/giving/", headers={"Host": JOURNEY_HOST}).status_code == 403


class TestNoEnumeration:
    """The form must not reveal who already has an account here."""

    def test_an_existing_address_gets_the_same_response(self, db, client, open_church):
        new = join(client)
        existing = join(client, email=STAFF, name="Someone Else")
        assert new.status_code == existing.status_code
        assert b"If that address can be used here" in new.data
        assert b"If that address can be used here" in existing.data

    def test_an_existing_account_is_not_overwritten(self, db, client, open_church):
        """Otherwise the form is a way to take over any account by knowing its
        address."""
        before = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        original_hash, original_role = before.password_hash, before.role

        join(client, email=STAFF, name="Attacker")
        db.session.expire_all()
        after = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        assert after.password_hash == original_hash
        assert after.role == original_role
        assert after.name != "Attacker"

    def test_an_existing_verified_account_gets_no_email(self, db, client, open_church):
        join(client, email=STAFF)
        assert db.session.scalars(db.select(OutboxMessage)).all() == []


class TestLinkingNeedsAConfirmedAddress:
    """The rule that carries the risk."""

    def test_no_person_is_attached_before_confirmation(self, db, client, open_church):
        person = Person(
            church_id=open_church.id, first_name="Nina", last_name="Ibarra",
            email=NEW_EMAIL, stage="attender",
        )
        db.session.add(person)
        db.session.commit()

        join(client)
        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        assert user.person_id is None

    def test_confirming_attaches_the_roster_record(self, db, client, open_church):
        person = Person(
            church_id=open_church.id, first_name="Nina", last_name="Ibarra",
            email=NEW_EMAIL, stage="attender",
        )
        db.session.add(person)
        db.session.commit()

        join(client)
        client.get(verify_link(db), headers={"Host": JOURNEY_HOST})

        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        assert user.is_verified
        assert user.person_id == person.id

    def test_a_household_address_never_resolves_to_a_guess(
        self, db, client, open_church
    ):
        """Two people share the address, so neither is attached."""
        for first in ("Nina", "Andre"):
            db.session.add(
                Person(
                    church_id=open_church.id, first_name=first, last_name="Ibarra",
                    email=NEW_EMAIL, stage="attender",
                )
            )
        db.session.commit()

        join(client)
        client.get(verify_link(db), headers={"Host": JOURNEY_HOST})

        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        linked = Person.get_for_church(open_church.id, user.person_id)
        # A brand new record, not either of the two that share the address.
        assert linked.first_name == "Nina"
        assert linked.stage == "visitor"
        assert len(
            db.session.scalars(
                db.select(Person).where(Person.email == NEW_EMAIL)
            ).all()
        ) == 3

    def test_somebody_new_becomes_a_visitor_on_the_roster(
        self, db, client, open_church
    ):
        """The church sees them the way they would see anyone who walked in."""
        join(client)
        client.get(verify_link(db), headers={"Host": JOURNEY_HOST})

        person = db.session.scalar(
            db.select(Person).where(Person.email == NEW_EMAIL)
        )
        assert person is not None
        assert person.stage == "visitor"
        assert person.first_seen_on is not None

    def test_a_new_person_enters_the_welcome_sequence(self, db, client, open_church):
        join(client)
        client.get(verify_link(db), headers={"Host": JOURNEY_HOST})

        person = db.session.scalar(
            db.select(Person).where(Person.email == NEW_EMAIL)
        )
        enrollments = db.session.scalars(
            SequenceEnrollment.for_person(open_church.id, person.id)
        ).all()
        assert [e.sequence_code for e in enrollments] == ["first_visit_welcome"]

    def test_confirming_signs_them_in(self, db, client, open_church):
        join(client)
        r = client.get(
            verify_link(db), headers={"Host": JOURNEY_HOST}, follow_redirects=True
        )
        assert r.status_code == 200
        assert b"Nina" in r.data


class TestVerificationTokens:
    def test_the_raw_token_is_never_stored(self, db, client, open_church):
        join(client)
        raw = verify_link(db).rsplit("/", 1)[-1]
        token = db.session.scalars(db.select(EmailVerificationToken)).one()
        assert token.token_hash != raw
        assert token.token_hash == hash_token(raw)

    def test_a_link_works_once(self, db, client, open_church):
        join(client)
        link = verify_link(db)
        client.get(link, headers={"Host": JOURNEY_HOST})
        assert client.get(link, headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_an_expired_link_is_refused(self, db, client, open_church):
        from datetime import timedelta

        join(client)
        link = verify_link(db)
        token = db.session.scalars(db.select(EmailVerificationToken)).one()
        token.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()

        assert client.get(link, headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_a_made_up_token_is_refused(self, db, client, open_church):
        r = client.get("/auth/verify/" + "x" * 43, headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_token_is_inert_at_another_church(self, db, client, open_church):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        riverbend.allow_self_signup = True
        db.session.commit()

        join(client)
        link = verify_link(db)
        assert client.get(link, headers={"Host": RIVERBEND_HOST}).status_code == 404

    def test_resending_retires_the_previous_link(self, db, client, open_church):
        join(client)
        first = verify_link(db)
        client.post(
            "/auth/login",
            data={"email": NEW_EMAIL, "password": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert client.get(first, headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_rate_limiting_stops_inbox_bombing(self, db, client, open_church):
        join(client)
        for _ in range(MAX_REQUESTS_PER_HOUR + 3):
            client.post(
                "/auth/login",
                data={"email": NEW_EMAIL, "password": NEW_PASSWORD},
                headers={"Host": JOURNEY_HOST},
            )
        assert len(
            db.session.scalars(db.select(OutboxMessage)).all()
        ) == MAX_REQUESTS_PER_HOUR


class TestExistingAccountsAreUnaffected:
    def test_accounts_made_by_staff_are_verified_on_creation(self, db, app):
        """Somebody with roster access vouching for an address is a stronger
        signal than a click in an inbox."""
        result = app.test_cli_runner().invoke(
            args=["create-user", "--church", "journey",
                  "--email", "new.staff@journeychurchsemo.com",
                  "--name", "New Staff", "--role", "staff",
                  "--password", "a-long-enough-passphrase"],
        )
        assert result.exit_code == 0
        user = db.session.scalar(
            db.select(User).where(User.email == "new.staff@journeychurchsemo.com")
        )
        assert user.is_verified

    def test_the_fixture_accounts_can_still_sign_in(self, db, client):
        r = client.post(
            "/auth/login",
            data={"email": STAFF, "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"Foundation" in r.data

    def test_a_deactivated_account_is_not_the_same_as_unverified(self, db, client):
        """Different states, different fixes, different messages."""
        r = client.post(
            "/auth/login",
            data={"email": "gone@journeychurchsemo.com", "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert b"Confirm your email" not in r.data


class TestTheVerificationDefault:
    """The direction of this default is the decision, and it was wrong first.

    Every path that creates a user except self-registration involves a staff
    member vouching for the address. So the default is verified, and the one
    route that cannot vouch clears it, rather than every other path having to
    remember to set it.
    """

    def test_a_user_created_in_code_is_not_verified(self, db):
        """Fail-closed.

        Defaulting to verified would mean any future code path that creates a
        user and forgets to clear the flag hands out a confirmed account, and
        that failure is silent. Every place that legitimately vouches for an
        address says so explicitly instead.
        """
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = User(
            church_id=church.id, email="vouched@journeychurchsemo.com",
            name="Vouched", role="member",
        )
        user.set_password("a-long-enough-passphrase")
        db.session.add(user)
        db.session.commit()
        assert not user.is_verified

        user.mark_verified()
        db.session.commit()
        assert user.is_verified

    def test_only_self_registration_starts_unverified(self, db, client, open_church):
        join(client)
        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        assert not user.is_verified

    def test_the_flag_survives_the_insert(self, db, client, open_church):
        """A column `default=` fires at INSERT and would overwrite a None set
        at construction. Same trap as sequence enrollments in increment 14."""
        join(client)
        db.session.expire_all()
        user = db.session.scalar(db.select(User).where(User.email == NEW_EMAIL))
        assert user.email_verified_at is None


class TestCopyReadsWellWithAnyChurchName:
    """Most church names start with an article, so any template that puts the
    name in a possessive slot reads badly. "Confirm your The Journey Church
    account" was the first version."""

    def test_no_subject_line_puts_the_name_in_a_possessive_slot(self):
        from app.content import AUTH, KIDS

        subjects = [
            AUTH["verify_email_subject"], AUTH["reset_email_subject"],
            KIDS["forgot_email_subject"],
        ]
        for template in subjects:
            rendered = template.format(church="The Journey Church")
            assert "your The" not in rendered, template
            assert "a The" not in rendered, template

    def test_they_read_correctly_across_the_shapes_of_church_names(self):
        from app.content import AUTH

        for name in ("The Journey Church", "Riverbend Fellowship", "St Andrews"):
            rendered = AUTH["verify_email_subject"].format(church=name)
            assert rendered.endswith(name)


class TestTheDeployDoesNotLockAnybodyOut:
    """`email_verified_at` is nullable and fail-closed, which means every
    account that existed before this feature reads as unverified.

    Without a backfill the migration signs out every user at every church,
    including the person who would have to fix it. The migration sets
    `email_verified_at = created_at` for existing rows, because an account
    created before this existed was created by a staff member, which is exactly
    the signal the column records.
    """

    def test_the_migration_backfills_existing_accounts(self):
        import glob
        from pathlib import Path

        path = glob.glob(
            str(Path(__file__).resolve().parent.parent
                / "migrations" / "versions" / "*self_registration*.py")
        )
        assert path, "The self-registration migration is missing"
        body = Path(path[0]).read_text()
        assert "UPDATE" in body and "email_verified_at" in body

    def test_a_legacy_account_signs_in(self, db, client):
        """Simulates a row that predates the column."""
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(
            db.select(User).where(
                User.email == "pastor@journeychurchsemo.com",
                User.church_id == church.id,
            )
        )
        user.email_verified_at = None
        db.session.commit()

        # Before the backfill they are locked out, which is the hazard.
        blocked = client.post(
            "/auth/login",
            data={"email": user.email, "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert blocked.status_code == 403

        db.session.execute(db.text(
            'UPDATE "user" SET email_verified_at = created_at '
            "WHERE email_verified_at IS NULL"
        ))
        db.session.commit()

        allowed = client.post(
            "/auth/login",
            data={"email": user.email, "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert allowed.status_code == 302
