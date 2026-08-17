import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Church, User

# Reserved TLDs like .test and .local are rejected by email validation on
# principle, so fixtures use a realistic domain. Hostnames are unaffected.
PASSWORD = "correct-horse-battery"

JOURNEY_HOST = "journey.dos.test"
RIVERBEND_HOST = "riverbend.dos.test"


@pytest.fixture
def app():
    """Build the app and seed it, then release the application context.

    Holding an app context open across the whole test would make every request
    reuse one `g`, and Flask-Login caches the signed-in user on `g._login_user`.
    That cache would survive from one request to the next and quietly defeat
    any test about session handling: a cross-tenant replay would look like it
    was accepted when in reality the loader was never consulted. Requests get
    their own context, exactly as they do in production.
    """
    app = create_app("testing")
    ctx = app.app_context()
    ctx.push()
    try:
        _db.create_all()

        journey = Church(
            slug="journey",
            name="The Journey Church",
            city="Jackson, MO",
            palette_key="journey",
            accent_hex="#485B38",
            logo_reversed_path="img/journey-logo-white.png",
            app_name="The Journey Church",
            app_domain="app.thejourneychurchsemo.com",
        )
        riverbend = Church(
            slug="riverbend",
            name="Riverbend Fellowship",
            city="Aurora, IL",
            palette_key="between-sundays",
        )
        closed = Church(
            slug="closed", name="Closed Church", palette_key="journey", is_active=False
        )
        _db.session.add_all([journey, riverbend, closed])
        _db.session.flush()

        def make(church, email, name, role, active=True):
            u = User(
                church_id=church.id,
                email=email,
                name=name,
                role=role,
                is_active_account=active,
            )
            u.set_password(PASSWORD)
            return u

        _db.session.add_all(
            [
                make(journey, "pastor@journeychurchsemo.com", "Pastor Reed", "staff"),
                make(journey, "leader@journeychurchsemo.com", "Dana Webb", "leader"),
                make(journey, "member@journeychurchsemo.com", "Alicia Romero", "member"),
                make(journey, "gone@journeychurchsemo.com", "Former Staff", "staff", active=False),
                # The same address at a different church. Proves the uniqueness
                # constraint is scoped per tenant rather than globally.
                make(riverbend, "pastor@journeychurchsemo.com", "Other Pastor", "staff"),
            ]
        )
        _db.session.commit()
    finally:
        ctx.pop()

    yield app

    ctx.push()
    _db.session.remove()
    _db.drop_all()
    ctx.pop()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """An application context for tests that query directly."""
    with app.app_context():
        yield _db


@pytest.fixture
def sign_in(client):
    """Sign a user in. Defaults to Journey's host."""

    def _sign_in(email, password=PASSWORD, host=JOURNEY_HOST, follow=True):
        return client.post(
            "/auth/login",
            data={"email": email, "password": password},
            headers={"Host": host},
            follow_redirects=follow,
        )

    return _sign_in


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


@pytest.fixture
def leader(client, sign_in):
    sign_in("leader@journeychurchsemo.com")
    return client


@pytest.fixture
def member(client, sign_in):
    sign_in("member@journeychurchsemo.com")
    return client
