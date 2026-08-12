import pytest

from app import create_app
from app.extensions import db, utcnow
from app.models import AccessToken, Person, Stage, consume_token, issue_token
from app.ratelimit import _hits
from app.seed import seed


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SECRET_KEY", "test")
    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with application.app_context():
        db.create_all()
        seed()
        yield application
        db.session.remove()
    _hits.clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mail(monkeypatch):
    box = []
    monkeypatch.setattr(
        "app.blueprints.auth.send_email",
        lambda to, subject, html, reply_to=None: box.append(
            {"to": to, "subject": subject, "html": html}
        )
        or True,
    )
    return box


def a_person(email="member@example.com", password=None):
    stage = Stage.query.order_by(Stage.position).first()
    person = Person(
        church_id=stage.church_id,
        first_name="Casey",
        last_name="Reed",
        email=email,
        source="connect card",
        stage_id=stage.id,
    )
    if password:
        person.set_password(password)
    db.session.add(person)
    db.session.commit()
    return person


def link_from(mail_item) -> str:
    body = mail_item["html"]
    start = body.index("/account/set-password/")
    end = body.index("'", start)
    return body[start:end]


# --- the hole this closes ---------------------------------------------------


def test_knowing_an_email_does_not_grant_an_account(app, client, mail):
    """Guessing a member's email must not let anyone set a password on their
    record and read their journey."""
    person = a_person()
    client.post("/account/claim", data={"email": "member@example.com"}, follow_redirects=True)

    db.session.refresh(person)
    assert person.password_hash is None  # nothing was set by submitting the form
    assert len(mail) == 1
    assert mail[0]["to"] == "member@example.com"  # the link went to them, not the visitor


def test_claim_form_does_not_reveal_who_attends(app, client, mail):
    known = client.post(
        "/account/claim", data={"email": "member@example.com"}, follow_redirects=True
    )
    a_person()
    unknown = client.post(
        "/account/claim", data={"email": "stranger@example.com"}, follow_redirects=True
    )
    assert b"If that email is in our system" in known.data
    assert b"If that email is in our system" in unknown.data
    assert mail == []  # neither address existed at the time of its own request


# --- link mechanics ---------------------------------------------------------


def test_the_link_sets_a_password_and_signs_you_in(app, client, mail):
    a_person()
    client.post("/account/claim", data={"email": "member@example.com"})
    url = link_from(mail[0])

    assert client.get(url).status_code == 200  # a GET does not spend the token
    response = client.post(
        url, data={"password": "a-good-password", "confirm": "a-good-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert client.get("/app/").status_code == 200  # signed in


def test_a_link_works_exactly_once(app, client, mail):
    a_person()
    client.post("/account/claim", data={"email": "member@example.com"})
    url = link_from(mail[0])
    client.post(url, data={"password": "a-good-password", "confirm": "a-good-password"})
    client.get("/account/logout")

    second = client.post(url, data={"password": "another-password", "confirm": "another-password"})
    assert second.status_code == 400


def test_an_expired_link_is_refused(app, client):
    person = a_person()
    raw, record = issue_token(person, "claim")
    record.expires_at = utcnow().replace(year=utcnow().year - 1)
    db.session.commit()
    assert consume_token(raw, "claim") is None


def test_requesting_a_new_link_retires_the_old_one(app, client, mail):
    a_person()
    client.post("/account/claim", data={"email": "member@example.com"})
    first_url = link_from(mail[0])
    client.post("/account/claim", data={"email": "member@example.com"})

    assert client.get(first_url).status_code == 400
    assert client.get(link_from(mail[1])).status_code == 200


def test_only_the_hash_is_stored(app):
    person = a_person()
    raw, record = issue_token(person, "claim")
    db.session.commit()
    assert raw not in record.token_hash
    assert AccessToken.query.filter_by(token_hash=raw).first() is None


def test_existing_account_gets_a_reset_not_a_claim(app, client, mail):
    a_person(password="original-password")
    client.post("/account/claim", data={"email": "member@example.com"})
    assert "Reset" in mail[0]["subject"]
    assert AccessToken.query.one().purpose == "reset"


# --- rate limiting ----------------------------------------------------------


def test_password_guessing_gets_cut_off(app, client):
    a_person(password="original-password")
    for _ in range(10):
        client.post("/account/login", data={"email": "member@example.com", "password": "wrong"})
    blocked = client.post(
        "/account/login", data={"email": "member@example.com", "password": "wrong"}
    )
    assert blocked.status_code == 429


def test_a_successful_login_clears_the_counter(app, client):
    a_person(password="original-password")
    for _ in range(5):
        client.post("/account/login", data={"email": "member@example.com", "password": "wrong"})
    client.post("/account/login", data={"email": "member@example.com", "password": "original-password"})
    client.get("/account/logout")
    for _ in range(5):
        client.post("/account/login", data={"email": "member@example.com", "password": "wrong"})
    assert client.get("/account/login").status_code == 200


def test_intake_flooding_gets_cut_off(app, client):
    app.config["INTAKE_TOKEN"] = "secret-token"
    headers = {"X-Intake-Token": "secret-token"}
    for index in range(30):
        client.post(
            "/api/intake",
            json={"first_name": "Bot", "email": f"bot{index}@example.com"},
            headers=headers,
        )
    blocked = client.post(
        "/api/intake", json={"first_name": "Bot", "email": "final@example.com"}, headers=headers
    )
    assert blocked.status_code == 429


# --- redirect safety --------------------------------------------------------


def test_next_cannot_send_someone_off_site(app, client):
    a_person(password="original-password")
    response = client.post(
        "/account/login?next=https://evil.example.com",
        data={"email": "member@example.com", "password": "original-password"},
    )
    assert "evil.example.com" not in response.headers["Location"]
    assert "/app/" in response.headers["Location"]
