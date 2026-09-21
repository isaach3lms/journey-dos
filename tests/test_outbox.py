"""Increment 4: the outbox and notification preferences.

The tests that matter most are the ones about *not* sending. A mail system
that sends is easy to verify by looking at it. A mail system that correctly
declines to send is invisible until someone complains, and by then the trust
is gone.
"""

from datetime import timedelta

import pytest

from app.categories import CATEGORIES, OPTIONAL_CATEGORIES, TRANSACTIONAL_CODES
from app.mail import NotQueued, MemoryTransport, opt_out, queue, send_pending
from app.mail.transport import SendFailed
from app.models import (
    MAX_ATTEMPTS,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENT,
    STATUS_SUPPRESSED,
    Church,
    OutboxMessage,
    Person,
    PersonEvent,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST


@pytest.fixture
def transport():
    return MemoryTransport()


@pytest.fixture
def person(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    p = Person(
        church_id=church.id,
        first_name="Marcus",
        last_name="Webb",
        email="marcus@example.com",
        stage="guest",
    )
    db.session.add(p)
    db.session.commit()
    return p


def queue_one(db, person, category="announcement", **kwargs):
    message = queue(
        church_id=person.church_id,
        category=category,
        subject=kwargs.pop("subject", "Sunday"),
        body_text=kwargs.pop("body_text", "See you Sunday."),
        person=person,
        **kwargs,
    )
    db.session.commit()
    return message


class TestCategories:
    def test_every_category_declares_whether_it_is_transactional(self):
        for category in CATEGORIES:
            assert isinstance(category.is_transactional, bool)

    def test_transactional_and_optional_do_not_overlap(self):
        optional = {c.code for c in OPTIONAL_CATEGORIES}
        assert optional & TRANSACTIONAL_CODES == set()

    def test_account_mail_is_transactional(self):
        """A password reset has to reach someone who left the newsletter."""
        assert "account" in TRANSACTIONAL_CODES

    def test_announcements_are_not_transactional(self):
        assert "announcement" not in TRANSACTIONAL_CODES


class TestQueuing:
    def test_a_message_is_queued_not_sent(self, db, person, transport):
        message = queue_one(db, person)
        assert message.status == STATUS_QUEUED
        assert transport.sent == []

    def test_a_message_needs_an_address(self, db, person):
        person.email = None
        db.session.commit()
        with pytest.raises(NotQueued, match="email address"):
            queue_one(db, person)

    def test_a_message_needs_a_subject_and_a_body(self, db, person):
        with pytest.raises(NotQueued):
            queue_one(db, person, subject="  ")
        with pytest.raises(NotQueued):
            queue_one(db, person, body_text="  ")

    def test_an_unknown_category_is_refused(self, db, person):
        with pytest.raises(NotQueued, match="not a notification category"):
            queue_one(db, person, category="spam")

    def test_a_dedupe_key_makes_queuing_idempotent(self, db, person):
        """A double-clicked button must not send two emails."""
        first = queue_one(db, person, dedupe_key="welcome:1")
        second = queue_one(db, person, dedupe_key="welcome:1")
        assert first is not None
        assert second is None
        assert db.session.scalars(
            OutboxMessage.for_person(person.church_id, person.id)
        ).all() == [first]

    def test_the_address_is_normalized(self, db, person):
        message = queue_one(db, person, to_email="  MARCUS@Example.COM  ")
        assert message.to_email == "marcus@example.com"


class TestSending:
    def test_a_queued_message_sends_and_is_recorded(self, db, person, transport):
        message = queue_one(db, person)
        counts = send_pending(transport=transport)

        assert counts["sent"] == 1
        db.session.refresh(message)
        assert message.status == STATUS_SENT
        assert message.sent_at is not None
        assert message.provider_message_id
        assert transport.sent[0].to_email == "marcus@example.com"

    def test_sending_lands_on_the_timeline(self, db, person, transport):
        queue_one(db, person, subject="Come to the lunch")
        send_pending(transport=transport)

        events = db.session.scalars(
            PersonEvent.for_person(person.church_id, person.id)
        ).all()
        assert any(e.kind == "email" and "lunch" in e.summary for e in events)

    def test_a_sent_message_is_not_sent_twice(self, db, person, transport):
        queue_one(db, person)
        send_pending(transport=transport)
        send_pending(transport=transport)
        assert len(transport.sent) == 1

    def test_the_batch_limit_is_respected(self, db, person, transport):
        for i in range(5):
            queue_one(db, person, dedupe_key=f"k{i}")
        send_pending(limit=2, transport=transport)
        assert len(transport.sent) == 2

    def test_sending_can_be_scoped_to_one_church(self, db, person, transport):
        other_church = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        other = Person(
            church_id=other_church.id, first_name="Other", last_name="Person",
            email="other@example.com", stage="guest",
        )
        db.session.add(other)
        db.session.commit()

        queue_one(db, person)
        queue_one(db, other)
        send_pending(church_id=person.church_id, transport=transport)

        assert [m.to_email for m in transport.sent] == ["marcus@example.com"]


class TestNotSending:
    """The half of this feature that is invisible when it works."""

    def test_an_opted_out_person_is_suppressed_not_sent(self, db, person, transport):
        message = queue_one(db, person)

        # Unsubscribes after queuing, before sending. This is the real case:
        # a queue-time check alone would send this message.
        opt_out(person)
        db.session.commit()

        counts = send_pending(transport=transport)
        assert counts["suppressed"] == 1
        assert transport.sent == []
        db.session.refresh(message)
        assert message.status == STATUS_SUPPRESSED
        assert "Opted out" in message.last_error

    def test_transactional_mail_still_reaches_an_opted_out_person(
        self, db, person, transport
    ):
        """Otherwise unsubscribing locks someone out of their own account."""
        opt_out(person)
        db.session.commit()

        queue_one(db, person, category="account", subject="Reset your password")
        counts = send_pending(transport=transport)

        assert counts["sent"] == 1
        assert transport.sent[0].subject == "Reset your password"

    def test_queuing_optional_mail_for_an_opted_out_person_is_refused(self, db, person):
        opt_out(person)
        db.session.commit()
        with pytest.raises(NotQueued, match="opted out"):
            queue_one(db, person)

    def test_a_category_preference_is_honored(self, db, person, transport):
        person.set_preference("announcement", False)
        db.session.commit()

        assert not person.allows("announcement")
        assert person.allows("next_step")
        with pytest.raises(NotQueued):
            queue_one(db, person, category="announcement")

    def test_a_preference_set_after_queuing_still_suppresses(
        self, db, person, transport
    ):
        message = queue_one(db, person, category="announcement")
        person.set_preference("announcement", False)
        db.session.commit()

        send_pending(transport=transport)
        db.session.refresh(message)
        assert message.status == STATUS_SUPPRESSED
        assert transport.sent == []

    def test_a_category_that_is_off_by_default_stays_off(self, db, person):
        assert not person.allows("digest")
        person.set_preference("digest", True)
        db.session.commit()
        assert person.allows("digest")

    def test_the_global_opt_out_outranks_a_category_preference(self, db, person):
        person.set_preference("announcement", True)
        opt_out(person)
        db.session.commit()
        assert not person.allows("announcement")
        assert person.allows("account")

    def test_an_unknown_category_cannot_be_set(self, db, person):
        with pytest.raises(ValueError):
            person.set_preference("not-a-category", True)


class TestFailureHandling:
    def test_a_temporary_failure_goes_back_to_the_queue(self, db, person, transport):
        message = queue_one(db, person)
        transport.fail_with = SendFailed("Network error", permanent=False)

        counts = send_pending(transport=transport)
        assert counts["retrying"] == 1
        db.session.refresh(message)
        assert message.status == STATUS_QUEUED
        assert message.attempts == 1
        assert message.claim_token is None

    def test_a_retry_eventually_succeeds(self, db, person, transport):
        message = queue_one(db, person)
        transport.fail_with = SendFailed("Network error", permanent=False)
        send_pending(transport=transport)

        transport.fail_with = None
        send_pending(transport=transport)
        db.session.refresh(message)
        assert message.status == STATUS_SENT

    def test_a_permanent_failure_is_not_retried(self, db, person, transport):
        """Retrying a rejected address damages a sending reputation that every
        church on this platform shares."""
        message = queue_one(db, person)
        transport.fail_with = SendFailed("HTTP 422: invalid recipient", permanent=True)

        counts = send_pending(transport=transport)
        assert counts["failed"] == 1
        db.session.refresh(message)
        assert message.status == STATUS_FAILED
        assert message.attempts == 1

    def test_retries_stop_after_the_limit(self, db, person, transport):
        message = queue_one(db, person)
        transport.fail_with = SendFailed("Network error", permanent=False)

        for _ in range(MAX_ATTEMPTS + 2):
            send_pending(transport=transport)

        db.session.refresh(message)
        assert message.status == STATUS_FAILED
        assert message.attempts == MAX_ATTEMPTS

    def test_a_failed_message_is_kept_not_deleted(self, db, person, transport):
        """Silently dropping mail is how a church finds out in March."""
        message = queue_one(db, person)
        transport.fail_with = SendFailed("HTTP 422", permanent=True)
        send_pending(transport=transport)

        db.session.refresh(message)
        assert message.last_error
        assert db.session.get(OutboxMessage, message.id) is not None


class TestClaiming:
    def test_a_claimed_row_is_not_claimed_again(self, db, person, transport):
        """Two workers running at once must not send the same message twice."""
        from app.mail.outbox import _claim

        queue_one(db, person)
        first_token, first = _claim(10)
        second_token, second = _claim(10)

        assert len(first) == 1
        assert second == []
        assert first_token != second_token

    def test_an_abandoned_claim_can_be_released(self, app, db, person):
        from app.mail.outbox import _claim

        message = queue_one(db, person)
        _claim(10)
        db.session.refresh(message)
        assert message.claim_token is not None

        # Simulate a worker that died between claiming and sending.
        message.claimed_at = utcnow() - timedelta(hours=2)
        db.session.commit()

        result = app.test_cli_runner().invoke(args=["release-claims", "--minutes", "15"])
        assert result.exit_code == 0
        db.session.expire_all()
        assert db.session.get(OutboxMessage, message.id).claim_token is None


class TestTransportSelection:
    def test_production_without_an_api_key_refuses_to_boot(self):
        from app.config import ProductionConfig

        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "real-secret",
                "MAIL_TRANSPORT": "resend",
                "RESEND_API_KEY": "",
            }

        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            ProductionConfig.init_app(FakeApp())

    def test_the_resend_transport_refuses_an_empty_key(self):
        from app.mail import ResendTransport

        with pytest.raises(ValueError):
            ResendTransport("")

    def test_development_defaults_to_the_console(self):
        from app.config import DevelopmentConfig
        from app.mail import build_transport

        assert build_transport(
            {"MAIL_TRANSPORT": DevelopmentConfig.MAIL_TRANSPORT}
        ).name == "console"


class TestUnsubscribeLink:
    def _token(self, db, person):
        token = person.ensure_unsubscribe_token()
        db.session.commit()
        return token

    def test_the_link_works_without_signing_in(self, db, person, client):
        token = self._token(db, person)
        r = client.get(f"/unsubscribe/{token}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Unsubscribe" in r.data

    def test_a_get_does_not_unsubscribe(self, db, person, client):
        """Mail clients and spam filters fetch every link in a message."""
        token = self._token(db, person)
        client.get(f"/unsubscribe/{token}/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(person)
        assert not person.has_opted_out

    def test_a_post_unsubscribes(self, db, person, client):
        token = self._token(db, person)
        r = client.post(f"/unsubscribe/{token}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        db.session.refresh(person)
        assert person.has_opted_out

    def test_a_bad_token_is_a_404(self, client):
        r = client.get("/unsubscribe/" + "x" * 40 + "/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_token_from_another_church_does_nothing(self, db, person, client):
        """Scoped to the host, so one tenant's link is inert on another."""
        token = self._token(db, person)
        r = client.get(f"/unsubscribe/{token}/", headers={"Host": "riverbend.dos.test"})
        assert r.status_code == 404
        db.session.refresh(person)
        assert not person.has_opted_out

    def test_tokens_are_unique_per_person(self, db, person):
        church = person.church_id
        other = Person(
            church_id=church, first_name="Dana", last_name="Webb",
            email="dana@example.com", stage="member",
        )
        db.session.add(other)
        db.session.commit()
        assert person.ensure_unsubscribe_token() != other.ensure_unsubscribe_token()

    def test_the_token_is_stable_once_minted(self, db, person):
        first = person.ensure_unsubscribe_token()
        assert person.ensure_unsubscribe_token() == first


class TestRoutes:
    def test_a_leader_can_queue_a_message(self, db, person, staff):
        staff.post(
            f"/people/{person.id}/email/",
            data={"category": "announcement", "subject": "Sunday",
                  "message_body": "See you Sunday."},
            headers={"Host": JOURNEY_HOST},
        )
        messages = db.session.scalars(
            OutboxMessage.for_person(person.church_id, person.id)
        ).all()
        assert len(messages) == 1
        assert messages[0].status == STATUS_QUEUED

    def test_queuing_mints_an_unsubscribe_token(self, db, person, staff):
        assert person.unsubscribe_token is None
        staff.post(
            f"/people/{person.id}/email/",
            data={"category": "announcement", "subject": "Sunday", "message_body": "Hi"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.unsubscribe_token

    def test_a_member_cannot_queue_a_message(self, person, member):
        r = member.post(
            f"/people/{person.id}/email/",
            data={"category": "announcement", "subject": "x", "message_body": "y"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403

    def test_email_cannot_be_queued_across_churches(self, db, staff):
        other_church = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        stranger = Person(
            church_id=other_church.id, first_name="Other", last_name="Person",
            email="other@example.com", stage="guest",
        )
        db.session.add(stranger)
        db.session.commit()

        r = staff.post(
            f"/people/{stranger.id}/email/",
            data={"category": "announcement", "subject": "x", "message_body": "y"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        assert db.session.scalars(
            OutboxMessage.for_person(stranger.church_id, stranger.id)
        ).all() == []

    def test_preferences_can_be_set_from_the_person_page(self, db, person, staff):
        staff.post(
            f"/people/{person.id}/preferences/",
            data={"cat_next_step": "on"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(person)
        assert person.allows("next_step")
        assert not person.allows("announcement")

    def test_opt_out_can_be_toggled(self, db, person, staff):
        staff.post(f"/people/{person.id}/optout/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(person)
        assert person.has_opted_out

        staff.post(f"/people/{person.id}/optout/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(person)
        assert not person.has_opted_out
