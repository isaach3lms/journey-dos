"""Self-serve password reset.

The tests that carry weight here are the ones about what a reset link cannot
do: outlive its hour, work twice, work at another church, be recovered from the
database, or leave an old session alive.
"""

import re
from datetime import timedelta

import pytest

from app.models import OutboxMessage, PasswordResetToken, User
from app.models.base import utcnow
from app.models.password_reset import (
    LIFETIME_MINUTES,
    MAX_REQUESTS_PER_HOUR,
    hash_token,
)
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

STAFF = "pastor@journeychurchsemo.com"
NEW_PASSWORD = "a-brand-new-passphrase"


def request_reset(client, email, host=JOURNEY_HOST):
    return client.post(
        "/auth/forgot", data={"email": email}, headers={"Host": host}
    )


def link_from_outbox(db, church_id):
    """Pull the reset URL out of the queued email, as a person would."""
    message = db.session.scalars(
        db.select(OutboxMessage)
        .where(OutboxMessage.church_id == church_id)
        .order_by(OutboxMessage.id.desc())
    ).first()
    assert message is not None, "No reset email was queued"
    match = re.search(r"(/auth/reset/[A-Za-z0-9_\-]+)", message.body_text)
    assert match, f"No reset link in the email body: {message.body_text[:200]}"
    return match.group(1)


class TestRequesting:
    def test_the_page_is_public(self, client):
        r = client.get("/auth/forgot", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Reset your password" in r.data

    def test_the_login_page_links_to_it(self, client):
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert b"/auth/forgot" in r.data
        # The old promise is gone.
        assert b"arrives at increment 4" not in r.data

    def test_a_request_queues_a_transactional_email(self, db, client):
        request_reset(client, STAFF)
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        assert message is not None
        # Transactional, so it reaches somebody who unsubscribed from the rest.
        assert message.category == "account"
        assert "reset" in message.subject.lower()

    def test_it_reaches_a_user_who_opted_out_of_everything_else(self, db, client):
        from app.mail import opt_out
        from app.models import Person

        church_id = db.session.scalar(
            db.select(User.church_id).where(User.email == STAFF)
        )
        person = Person(
            church_id=church_id, first_name="Pastor", last_name="Reed",
            email=STAFF, stage="leader",
        )
        db.session.add(person)
        db.session.flush()  # opt_out records an event against person.id
        opt_out(person)
        db.session.commit()

        request_reset(client, STAFF)
        assert db.session.scalars(db.select(OutboxMessage)).all() != []


class TestNoEnumeration:
    """The form must not reveal who has an account."""

    def test_an_unknown_address_gets_the_same_response(self, client):
        known = request_reset(client, STAFF)
        unknown = request_reset(client, "nobody@journeychurchsemo.com")
        assert known.status_code == unknown.status_code
        assert b"If that address has an account" in known.data
        assert b"If that address has an account" in unknown.data

    def test_no_email_is_queued_for_an_unknown_address(self, db, client):
        request_reset(client, "nobody@journeychurchsemo.com")
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_a_deactivated_account_gets_no_email_and_no_hint(self, db, client):
        r = request_reset(client, "gone@journeychurchsemo.com")
        assert b"If that address has an account" in r.data
        assert db.session.scalars(db.select(OutboxMessage)).all() == []


class TestTokenHandling:
    def test_the_raw_token_is_never_stored(self, db, client):
        """A database read must not yield working reset links."""
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))
        raw = link.rsplit("/", 1)[-1]

        token = db.session.scalars(db.select(PasswordResetToken)).one()
        assert token.token_hash != raw
        assert raw not in token.token_hash
        assert token.token_hash == hash_token(raw)
        assert len(token.token_hash) == 64

    def test_requesting_again_retires_the_first_link(self, db, client):
        request_reset(client, STAFF)
        first = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        request_reset(client, STAFF)
        r = client.get(first, headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_link_works_once(self, db, client):
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        client.post(
            link,
            data={"password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        again = client.get(link, headers={"Host": JOURNEY_HOST})
        assert again.status_code == 404

    def test_an_expired_link_is_refused(self, db, client):
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        token = db.session.scalars(db.select(PasswordResetToken)).one()
        token.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()

        assert client.get(link, headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_the_lifetime_is_an_hour(self, db, client):
        request_reset(client, STAFF)
        token = db.session.scalars(db.select(PasswordResetToken)).one()
        window = (token.expires_at - token.created_at).total_seconds() / 60
        assert 55 < window <= LIFETIME_MINUTES + 1

    def test_a_made_up_token_is_refused(self, client):
        r = client.get("/auth/reset/" + "x" * 43, headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_short_token_is_refused_without_a_query(self, client):
        assert client.get("/auth/reset/abc", headers={"Host": JOURNEY_HOST}).status_code == 404

    def test_a_token_from_one_church_is_inert_at_another(self, db, client):
        request_reset(client, STAFF, host=JOURNEY_HOST)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        r = client.get(link, headers={"Host": RIVERBEND_HOST})
        assert r.status_code == 404

    def test_rate_limiting_stops_inbox_bombing(self, db, client):
        for _ in range(MAX_REQUESTS_PER_HOUR + 3):
            request_reset(client, STAFF)
        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(queued) == MAX_REQUESTS_PER_HOUR

    def test_purging_old_tokens(self, db, client):
        request_reset(client, STAFF)
        token = db.session.scalars(db.select(PasswordResetToken)).one()
        token.created_at = utcnow() - timedelta(days=30)
        db.session.commit()

        assert PasswordResetToken.purge_expired(older_than_days=7) == 1
        db.session.commit()
        assert db.session.scalars(db.select(PasswordResetToken)).all() == []


class TestSettingANewPassword:
    def _reset_to(self, db, client, password):
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))
        return client.post(
            link,
            data={"password": password, "confirm": password},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )

    def test_the_new_password_works_and_the_old_one_does_not(self, db, client):
        self._reset_to(db, client, NEW_PASSWORD)
        client.post("/auth/logout", headers={"Host": JOURNEY_HOST})

        old = client.post(
            "/auth/login",
            data={"email": STAFF, "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert old.status_code == 401

        new = client.post(
            "/auth/login",
            data={"email": STAFF, "password": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert b"Welcome To Your Dashboard" in new.data

    def test_mismatched_confirmation_is_refused(self, db, client):
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        r = client.post(
            link,
            data={"password": NEW_PASSWORD, "confirm": "something-else-entirely"},
            headers={"Host": JOURNEY_HOST},
        )
        assert b"do not match" in r.data

        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        assert user.check_password(PASSWORD)

    def test_a_short_password_is_refused(self, db, client):
        request_reset(client, STAFF)
        link = link_from_outbox(db, db.session.scalar(db.select(User.church_id)))

        client.post(
            link, data={"password": "short", "confirm": "short"},
            headers={"Host": JOURNEY_HOST},
        )
        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        assert user.check_password(PASSWORD)

    def test_a_reset_clears_a_lockout(self, db, client):
        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        user.locked_until = utcnow() + timedelta(minutes=30)
        user.failed_login_count = 10
        db.session.commit()

        self._reset_to(db, client, NEW_PASSWORD)
        db.session.refresh(user)
        assert not user.is_locked
        assert user.failed_login_count == 0

    def test_the_user_is_signed_in_afterwards(self, db, client):
        r = self._reset_to(db, client, NEW_PASSWORD)
        assert b"Welcome To Your Dashboard" in r.data


class TestOtherSessionsAreSignedOut:
    """The reason a reset is useful to somebody whose device was taken."""

    def test_an_existing_session_stops_working(self, app):
        """Deliberately does not request the `db` fixture.

        That fixture holds one application context open for the whole test,
        and Flask-Login caches the signed-in user on `g._login_user`. With two
        clients in play the second one inherits the first one's identity, and
        the reset request silently redirects as an already-authenticated user.
        Each request here gets its own context, as in production.
        """
        thief = app.test_client()
        thief.post(
            "/auth/login",
            data={"email": STAFF, "password": PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        assert thief.get("/", headers={"Host": JOURNEY_HOST}).status_code == 200

        owner = app.test_client()
        request_reset(owner, STAFF)
        with app.app_context():
            from app.extensions import db as _db

            link = link_from_outbox(_db, _db.session.scalar(_db.select(User.church_id)))
        owner.post(
            link,
            data={"password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )

        # The other device is now anonymous.
        r = thief.get("/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_session_version_moves_on_every_password_change(self, db):
        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        before = user.session_version
        user.set_password("another-long-passphrase")
        db.session.commit()
        assert user.session_version == before + 1

    def test_a_cookie_carrying_an_old_version_does_not_load(self, db, app):
        from flask import g

        from app.security import load_user
        from app.tenancy import resolve_church

        user = db.session.scalar(
            db.select(User).where(User.email == STAFF, User.role == "staff")
        )
        stale = user.get_id()
        user.set_password("another-long-passphrase")
        db.session.commit()

        with app.test_request_context("/", headers={"Host": JOURNEY_HOST}):
            g.church = resolve_church()
            assert load_user(stale) is None
            assert load_user(user.get_id()) is not None
