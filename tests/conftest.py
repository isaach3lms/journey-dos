import pytest

from app import create_app
from app.extensions import db as _db
from app.models import Church


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        _db.create_all()
        _db.session.add_all(
            [
                Church(
                    slug="journey",
                    name="The Journey Church",
                    city="Jackson, MO",
                    palette_key="journey",
                    accent_hex="#485B38",
                    logo_reversed_path="img/journey-logo-white.png",
                    app_name="The Journey Church",
                    app_domain="app.thejourneychurchsemo.com",
                ),
                Church(
                    slug="riverbend",
                    name="Riverbend Fellowship",
                    city="Aurora, IL",
                    palette_key="between-sundays",
                ),
                Church(
                    slug="closed",
                    name="Closed Church",
                    palette_key="journey",
                    is_active=False,
                ),
            ]
        )
        _db.session.commit()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    return _db
