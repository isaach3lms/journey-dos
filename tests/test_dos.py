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


def test_root_sends_anonymous_visitors_to_sign_in(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/account/login" in response.headers["Location"]


def test_login_and_embed_render(client):
    assert client.get("/account/login").status_code == 200
    assert client.get("/embed/connect").status_code == 200


def test_embedded_form_creates_person_and_enrolls(client, sent):
    response = client.post(
        "/embed/connect",
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
        "/embed/connect",
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
        "/embed/connect",
        data={"first_name": "Fast", "email": "fast@example.com", "started_at": str(time.time())},
    )
    assert Person.query.filter_by(email="fast@example.com").first() is None


# --- intake API -------------------------------------------------------------


def test_api_rejects_a_bad_token(app, client, sent):
    app.config["INTAKE_TOKEN"] = "secret-token"
    response = client.post(
        "/api/intake",
        json={"first_name": "Nope", "email": "nope@example.com", "token": "wrong"},
    )
    assert response.status_code == 401
    assert Person.query.filter_by(email="nope@example.com").first() is None


def test_api_accepts_json_from_the_church_website(app, client, sent):
    app.config["INTAKE_TOKEN"] = "secret-token"
    response = client.post(
        "/api/intake",
        json={
            "first_name": "Rachel",
            "last_name": "Kim",
            "email": "rachel@example.com",
            "phone": "573-555-0110",
            "message": "New to Jackson",
            "form": "launch team",
        },
        headers={"X-Intake-Token": "secret-token"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "created": True}
    person = Person.query.filter_by(email="rachel@example.com").one()
    assert person.stage.name == "Launch team"
    assert person.source == "launch team"
    assert "New to Jackson" in person.notes


def test_api_splits_a_single_name_field(app, client, sent):
    app.config["INTAKE_TOKEN"] = "secret-token"
    client.post(
        "/api/intake",
        json={"name": "Dana Whitfield", "email": "dana@example.com"},
        headers={"X-Intake-Token": "secret-token"},
    )
    person = Person.query.filter_by(email="dana@example.com").one()
    assert (person.first_name, person.last_name) == ("Dana", "Whitfield")


def test_api_resubmission_updates_rather_than_duplicates(app, client, sent):
    app.config["INTAKE_TOKEN"] = "secret-token"
    payload = {"first_name": "Sam", "email": "sam@example.com", "phone": "111"}
    client.post("/api/intake", json=payload, headers={"X-Intake-Token": "secret-token"})
    second = client.post(
        "/api/intake",
        json={**payload, "phone": "222"},
        headers={"X-Intake-Token": "secret-token"},
    )
    assert second.get_json()["created"] is False
    person = Person.query.filter_by(email="sam@example.com").one()
    assert person.phone == "222"


def test_api_requires_name_and_email(app, client, sent):
    app.config["INTAKE_TOKEN"] = "secret-token"
    response = client.post(
        "/api/intake", json={"email": ""}, headers={"X-Intake-Token": "secret-token"}
    )
    assert response.status_code == 400


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


def test_first_step_sends_the_moment_someone_enrolls(app, sent):
    """The day zero email is the confirmation the visitor expects on submit.
    It must not wait for the nightly cron."""
    person = make_person()
    enroll(person)
    assert len(sent) == 1
    assert "Test" in sent[0]["html"]
    assert person.interactions[0].kind == "automated"
    # and the runner must not send it a second time
    assert run_sequences("https://example.org")["sent"] == 0


def test_no_duplicate_send_on_second_run(app, sent):
    enroll(make_person())
    run_sequences()
    assert run_sequences()["sent"] == 0
    assert len(sent) == 1


def test_human_contact_stops_the_sequence(app, sent):
    person = make_person()
    enrollment = enroll(person)
    assert len(sent) == 1  # the day zero confirmation already went out

    person.log_contact("call", "Talked at the coffee shop")
    db.session.commit()
    run_sequences()

    db.session.refresh(enrollment)
    assert enrollment.stopped_at is not None
    assert enrollment.stop_reason == "a person made contact"
    assert len(sent) == 1  # nothing further


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


# --- the DOS now owns intake notification -----------------------------------


def test_staff_alert_carries_what_the_site_used_to_send(app, client, monkeypatch):
    """The public site no longer emails on submit, so this alert has to stand
    on its own: identity, source, stage, the message, and a way to act."""
    outbox = []
    monkeypatch.setattr(
        "app.emails.send_email",
        lambda to, subject, html, reply_to=None: outbox.append(
            {"to": to, "subject": subject, "html": html, "reply_to": reply_to}
        )
        or True,
    )
    monkeypatch.setattr("app.automations.send_email", lambda *a, **k: True)
    app.config["INTAKE_TOKEN"] = "secret-token"
    app.config["NOTIFY_TO"] = "pastors@example.com"

    client.post(
        "/api/intake",
        json={
            "first_name": "Morgan",
            "last_name": "Ellis",
            "email": "morgan@example.com",
            "phone": "573-555-0180",
            "message": "Moving to Jackson in September",
            "form": "launch team",
        },
        headers={"X-Intake-Token": "secret-token"},
    )

    alert = outbox[0]
    assert alert["to"] == "pastors@example.com"
    assert alert["reply_to"] == "morgan@example.com"  # reply goes to the person
    assert "New launch team: Morgan Ellis" == alert["subject"]
    assert "Moving to Jackson in September" in alert["html"]
    assert "Launch team" in alert["html"]  # the stage they landed in
    assert "/staff/people/" in alert["html"]  # a link straight to the record


def test_a_returning_person_is_labelled_as_such(app, client, monkeypatch):
    outbox = []
    monkeypatch.setattr(
        "app.emails.send_email",
        lambda to, subject, html, reply_to=None: outbox.append({"subject": subject}) or True,
    )
    monkeypatch.setattr("app.automations.send_email", lambda *a, **k: True)
    app.config["INTAKE_TOKEN"] = "secret-token"
    payload = {"first_name": "Sam", "email": "sam@example.com"}
    headers = {"X-Intake-Token": "secret-token"}

    client.post("/api/intake", json=payload, headers=headers)
    client.post("/api/intake", json=payload, headers=headers)

    assert outbox[0]["subject"].startswith("New")
    assert outbox[1]["subject"].startswith("Returning")
