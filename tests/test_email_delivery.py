"""Getting account email out, and being able to tell when it is not.

Prompted by a real failure: somebody signed up, saw "check your email", and
nothing came. The sign-up screen is identical whether or not an email left, by
design, so the only way to find out was a shell. Three fixes, each tested here:

1. Account email is sent inside the request that queued it, so a confirmation
   link does not depend on the five minute worker at all.
2. Settings shows whether email is leaving, and a test button returns the
   provider's own answer.
3. The worker runs every step in isolation, so a failure in sequences can no
   longer stop sending.
"""

import pytest

import app.mail.outbox as outbox_module
from app.mail import MemoryTransport, SendFailed
from app.mail.health import email_health, explain
from app.models import Church, OutboxMessage, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

NEW_EMAIL = "nina.ibarra@example.com"
STAFF = "pastor@journeychurchsemo.com"


@pytest.fixture
def transport(app, monkeypatch):
    shared = MemoryTransport()
    monkeypatch.setattr(outbox_module, "build_transport", lambda config: shared)
    app.config["MAIL_SEND_NOW"] = True
    yield shared
    app.config["MAIL_SEND_NOW"] = False


@pytest.fixture
def open_church(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.allow_self_signup = True
    db.session.commit()
    return church


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def join(client, email=NEW_EMAIL):
    return client.post(
        "/auth/join",
        data={"name": "Nina Ibarra", "email": email, "password": "a-brand-new-passphrase"},
        headers={"Host": JOURNEY_HOST},
    )


def latest(db):
    return db.session.scalars(
        db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
    ).first()


class TestAccountEmailLeavesImmediately:
    def test_signup_confirmation_is_sent_in_the_request(self, client, db, open_church, transport):
        response = join(client)
        assert response.status_code == 200
        assert [m.to_email for m in transport.sent] == [NEW_EMAIL]
        message = latest(db)
        assert message.status == "sent"
        assert message.category == "account"

    def test_it_does_not_wait_for_the_worker(self, client, db, open_church, transport):
        join(client)
        # Nothing left for the cron job to do.
        assert db.session.scalar(
            db.select(OutboxMessage).where(OutboxMessage.status == "queued")
        ) is None

    def test_a_provider_failure_leaves_it_queued_for_the_worker(
        self, client, db, open_church, transport
    ):
        transport.fail_with = SendFailed("Network error: timed out", permanent=False)
        response = join(client)
        # The person still gets their screen.
        assert response.status_code == 200
        message = latest(db)
        assert message.status == "queued"
        assert message.attempts == 1
        assert "timed out" in message.last_error

    def test_an_unexpected_crash_never_costs_the_response(
        self, client, db, open_church, transport, monkeypatch
    ):
        def boom(**kwargs):
            raise RuntimeError("provider library exploded")

        monkeypatch.setattr(transport, "send", boom)
        response = join(client)
        assert response.status_code == 200
        assert latest(db).status == "queued"

    def test_password_reset_is_sent_immediately(self, client, db, transport):
        client.post(
            "/auth/forgot",
            data={"email": "member@journeychurchsemo.com"},
            headers={"Host": JOURNEY_HOST},
        )
        assert [m.to_email for m in transport.sent] == ["member@journeychurchsemo.com"]

    def test_church_news_still_waits_for_the_worker(self, db, app, transport):
        from app.mail import queue

        with app.test_request_context(headers={"Host": JOURNEY_HOST}):
            queue(
                church_id=journey(db).id,
                category="announcement",
                subject="Picnic",
                body_text="Sunday after service.",
                to_email="someone@example.com",
            )
            db.session.commit()
            from app.mail import deliver_queued_now

            assert deliver_queued_now() is None
        assert transport.sent == []

    def test_a_rolled_back_signup_sends_nothing(self, db, app, transport):
        from app.mail import deliver_queued_now, queue

        with app.test_request_context(headers={"Host": JOURNEY_HOST}):
            queue(
                church_id=journey(db).id,
                category="account",
                subject="Confirm",
                body_text="Link",
                to_email="ghost@example.com",
            )
            db.session.rollback()
            assert deliver_queued_now() is None
        assert transport.sent == []

    def test_switch_off_leaves_everything_to_the_worker(self, client, db, open_church, transport, app):
        app.config["MAIL_SEND_NOW"] = False
        join(client)
        assert transport.sent == []
        assert latest(db).status == "queued"

    def test_the_worker_cannot_send_it_twice(self, client, db, open_church, transport):
        from app.mail import send_pending

        join(client)
        counts = send_pending()
        assert counts["sent"] == 0
        assert len(transport.sent) == 1


class TestEmailHealth:
    def queue_row(self, db, **values):
        row = OutboxMessage(
            church_id=journey(db).id,
            to_email=values.pop("to_email", "a@example.com"),
            category="account",
            subject=values.pop("subject", "Confirm your email"),
            body_text="secret link body",
            status=values.pop("status", "queued"),
            queued_at=values.pop("queued_at", utcnow()),
            **values,
        )
        db.session.add(row)
        db.session.commit()
        return row

    def test_counts_by_status(self, db):
        self.queue_row(db, status="sent", sent_at=utcnow())
        self.queue_row(db)
        self.queue_row(db, status="failed", last_error="HTTP 403: nope", attempts=1)
        health = email_health(journey(db).id)
        assert (health["sent"], health["queued"], health["failed"]) == (1, 1, 1)

    def test_waiting_too_long_means_the_worker_is_down(self, db):
        from datetime import timedelta

        self.queue_row(db, queued_at=utcnow() - timedelta(minutes=25))
        health = email_health(journey(db).id)
        assert health["stale"] is True
        assert health["waiting_minutes"] >= 24

    def test_fresh_queue_is_not_stale(self, db):
        self.queue_row(db)
        assert email_health(journey(db).id)["stale"] is False

    def test_other_churches_are_not_counted(self, db):
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        db.session.add(OutboxMessage(
            church_id=other.id, to_email="x@example.com", category="account",
            subject="x", body_text="x", status="failed", queued_at=utcnow(),
            last_error="HTTP 403: other church",
        ))
        db.session.commit()
        health = email_health(journey(db).id)
        assert health["failed"] == 0
        assert health["problems"] == []

    def test_domain_error_is_explained(self, db):
        self.queue_row(
            db, status="failed", attempts=1,
            last_error="HTTP 403: The thejourneychurchsemo.com domain is not verified. "
                       "Please, add and verify your domain on https://resend.com/domains",
        )
        health = email_health(journey(db).id)
        assert health["latest_reason"] == "domain_unverified"

    @pytest.mark.parametrize("error,key", [
        ("HTTP 401: API key is invalid", "bad_key"),
        ("HTTP 403: You can only send testing emails to your own email address", "test_mode"),
        ("HTTP 422: Invalid `from` field.", "bad_from"),
        ("HTTP 429: Too many requests, rate limit exceeded", "rate_limited"),
        ("Network error: timed out", "network"),
        ("HTTP 500: who knows", None),
        (None, None),
    ])
    def test_explanations(self, error, key):
        assert explain(error) == key

    def test_every_explanation_has_copy(self):
        from app.content import SETTINGS
        from app.mail.health import _EXPLANATIONS

        for _, key in _EXPLANATIONS:
            assert f"email_why_{key}" in SETTINGS

    def test_never_exposes_the_key(self, app, db):
        app.config["RESEND_API_KEY"] = "re_supersecret"
        try:
            health = email_health(journey(db).id)
        finally:
            app.config["RESEND_API_KEY"] = ""
        assert health["key_present"] is True
        assert "re_supersecret" not in repr(health)


class TestSettingsPanel:
    def test_staff_see_the_panel(self, staff, db):
        page = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert page.status_code == 200
        assert b"Email delivery" in page.data
        assert b"Send a test email to me" in page.data

    def test_memory_transport_reads_as_not_sending(self, staff, db):
        page = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"Not sending real email" in page.data

    def test_bodies_are_never_shown(self, staff, db):
        db.session.add(OutboxMessage(
            church_id=journey(db).id, to_email="a@example.com", category="account",
            subject="Confirm your email", body_text="https://secret-reset-link",
            status="failed", queued_at=utcnow(), last_error="HTTP 403: no",
        ))
        db.session.commit()
        page = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert b"Confirm your email" in page.data
        assert b"secret-reset-link" not in page.data

    def test_members_cannot_see_it(self, member):
        response = member.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert response.status_code in (302, 403, 404)

    def test_members_cannot_send_tests(self, member, db):
        response = member.post("/settings/email/test/", headers={"Host": JOURNEY_HOST})
        assert response.status_code in (302, 403, 404)
        assert latest(db) is None or latest(db).subject.startswith("Test email") is False


class TestTestButton:
    def test_sends_to_the_person_pressing_it(self, staff, db, transport):
        response = staff.post(
            "/settings/email/test/", headers={"Host": JOURNEY_HOST}, follow_redirects=True
        )
        assert response.status_code == 200
        assert [m.to_email for m in transport.sent] == [STAFF]
        assert b"Sent. Check" in response.data

    def test_shows_the_providers_answer_and_the_fix(self, staff, db, transport):
        transport.fail_with = SendFailed(
            "HTTP 403: The thejourneychurchsemo.com domain is not verified.",
            permanent=True,
        )
        response = staff.post(
            "/settings/email/test/", headers={"Host": JOURNEY_HOST}, follow_redirects=True
        )
        assert b"domain is not verified" in response.data
        assert b"Nothing sends until it says Verified" in response.data
        assert latest(db).status == "failed"

    def test_works_even_with_immediate_send_switched_off(self, staff, db, transport, app):
        app.config["MAIL_SEND_NOW"] = False
        staff.post("/settings/email/test/", headers={"Host": JOURNEY_HOST})
        assert len(transport.sent) == 1


class TestWorkerTick:
    def run(self, app):
        return app.test_cli_runner().invoke(args=["worker-tick"])

    def test_runs_clean(self, app, db):
        result = self.run(app)
        assert result.exit_code == 0, result.output
        assert "worker-tick finished" in result.output

    def test_a_failing_step_does_not_stop_sending(self, app, db, monkeypatch):
        import app.automation as automation
        from app.mail import queue

        def broken(**kwargs):
            raise RuntimeError("sequence engine broke")

        monkeypatch.setattr(automation, "run_due", broken)
        shared = MemoryTransport()
        monkeypatch.setattr(outbox_module, "build_transport", lambda config: shared)

        queue(
            church_id=journey(db).id, category="account", subject="Confirm",
            body_text="Link", to_email="waiting@example.com",
        )
        db.session.commit()

        result = self.run(app)
        assert [m.to_email for m in shared.sent] == ["waiting@example.com"]
        # Still reported as a failure, so Render shows the job red.
        assert result.exit_code == 1
        assert "run-sequences" in result.output

    def test_render_uses_it(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "render.yaml").read_text()
        assert "startCommand: flask worker-tick" in text
        assert "&& flask send-outbox" not in text
