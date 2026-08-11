import time
from datetime import timedelta

import pytest

from app import create_app
from app.automations import enroll, run_sequences
from app.extensions import db, utcnow
from app.importers import import_giving_csv, parse_rows
from app.models import Enrollment, GivingRecord, Person, Stage
from app.seed import seed


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SECRET_KEY", "test")
    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SITE_URL="https://example.org")
    with application.app_context():
        db.create_all()
        seed()
        yield application
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sent(monkeypatch):
    """Capture outbound email instead of calling Resend."""
    box = []

    def fake_send(to, subject, html, reply_to=None):
        box.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr("app.automations.send_email", fake_send)
    monkeypatch.setattr("app.blueprints.public.notify_new_person", lambda *a, **k: None)
    return box


def make_person(email="test@example.com", source="connect card", stage_index=0):
    stages = Stage.query.order_by(Stage.position).all()
    person = Person(
        church_id=stages[0].church_id,
        first_name="Test",
        last_name="Person",
        email=email,
        source=source,
    )
    db.session.add(person)
    db.session.flush()
    person.move_to_stage(stages[stage_index])
    db.session.commit()
    return person


# --- public pages -----------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/about", "/launch-team", "/connect", "/give", "/account/login"]
)
def test_public_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_connect_card_creates_person_and_enrolls(client, sent):
    response = client.post(
        "/connect",
        data={
            "first_name": "Marcus",
            "last_name": "Webb",
            "email": "marcus@example.com",
            "started_at": str(time.time() - 30),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    person = Person.query.filter_by(email="marcus@example.com").one()
    assert person.stage.name == "Interested"
    assert Enrollment.query.filter_by(person_id=person.id).count() == 1


def test_honeypot_blocks_submission(client, sent):
    client.post(
        "/connect",
        data={
            "first_name": "Bot",
            "email": "bot@example.com",
            "website": "http://spam",
            "started_at": str(time.time() - 30),
        },
    )
    assert Person.query.filter_by(email="bot@example.com").first() is None


def test_timing_gate_blocks_instant_submission(client, sent):
    client.post(
        "/connect",
        data={"first_name": "Fast", "email": "fast@example.com", "started_at": str(time.time())},
    )
    assert Person.query.filter_by(email="fast@example.com").first() is None


# --- journey tracking -------------------------------------------------------


def test_stuck_flag_uses_stage_threshold(app):
    person = make_person()
    assert person.is_stuck is False
    person.stage_since = utcnow() - timedelta(days=person.stage.stuck_after_days + 1)
    db.session.commit()
    assert person.is_stuck is True


def test_stage_move_writes_history(app):
    person = make_person()
    stages = Stage.query.order_by(Stage.position).all()
    person.move_to_stage(stages[2], note="joined")
    db.session.commit()
    assert person.stage_id == stages[2].id
    assert person.stage_events[0].to_stage.name == "Launch team"
    assert person.days_in_stage == 0


# --- automation rules -------------------------------------------------------


def test_first_step_sends_immediately(app, sent):
    person = make_person()
    enroll(person)
    result = run_sequences("https://example.org")
    assert result["sent"] == 1
    assert "Test" in sent[0]["html"]
    assert person.interactions[0].kind == "automated"


def test_no_duplicate_send_on_second_run(app, sent):
    enroll(make_person())
    run_sequences()
    assert run_sequences()["sent"] == 0


def test_human_contact_stops_the_sequence(app, sent):
    person = make_person()
    enrollment = enroll(person)
    person.log_contact("call", "Talked at the coffee shop")
    db.session.commit()
    run_sequences()
    db.session.refresh(enrollment)
    assert enrollment.stopped_at is not None
    assert enrollment.stop_reason == "a person made contact"
    assert sent == []


def test_reaching_the_target_stage_stops_the_sequence(app, sent):
    person = make_person()
    enrollment = enroll(person)
    stages = Stage.query.order_by(Stage.position).all()
    person.move_to_stage(stages[2])  # Launch team
    db.session.commit()
    run_sequences()
    db.session.refresh(enrollment)
    assert enrollment.stopped_at is not None


def test_later_steps_wait_for_their_delay(app, sent):
    enrollment = enroll(make_person())
    run_sequences()
    enrollment.last_sent_at = utcnow() - timedelta(days=2)
    db.session.commit()
    assert run_sequences()["sent"] == 0  # step two is day three
    enrollment.enrolled_at = utcnow() - timedelta(days=4)
    db.session.commit()
    assert run_sequences()["sent"] == 1


def test_unknown_source_is_not_enrolled(app):
    person = make_person(email="walkin@example.com", source="added by staff")
    assert enroll(person) is None


# --- giving import ----------------------------------------------------------


CSV = (
    "Transaction ID,Date,Amount,Fund,Name,Email,Payment Type,Recurring\n"
    "tx-1,08/03/2026,\"$1,200.00\",General,Priya Anand,priya@example.com,Card,Yes\n"
    "tx-2,08/04/2026,$75.50,Building,Walk In,,Cash,No\n"
    "tx-3,08/05/2026,$0.00,General,Zero Gift,zero@example.com,Card,No\n"
)


def test_parser_normalizes_money_and_dates():
    rows = list(parse_rows(CSV.encode()))
    assert len(rows) == 2  # the zero gift is dropped
    assert rows[0]["amount_cents"] == 120000
    assert rows[0]["is_recurring"] is True
    assert rows[0]["given_at"].year == 2026


def test_import_matches_by_email_and_dedupes(app):
    church_id = Stage.query.first().church_id
    make_person(email="priya@example.com")
    first = import_giving_csv(CSV.encode(), church_id)
    assert first == {"added": 2, "skipped": 0, "unmatched": 1}

    second = import_giving_csv(CSV.encode(), church_id)
    assert second["added"] == 0 and second["skipped"] == 2
    assert GivingRecord.query.count() == 2

    matched = GivingRecord.query.filter_by(external_id="tx-1").one()
    assert matched.person_id is not None


# --- access control ---------------------------------------------------------


def test_staff_pages_hidden_from_anonymous(client):
    assert client.get("/staff/").status_code in (302, 401)


def test_member_cannot_reach_staff_pages(app, client):
    person = make_person(email="member@example.com")
    person.set_password("passw0rd123")
    db.session.commit()
    client.post("/account/login", data={"email": "member@example.com", "password": "passw0rd123"})
    assert client.get("/staff/").status_code == 404
    assert client.get("/app/").status_code == 200
