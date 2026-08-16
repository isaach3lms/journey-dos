from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("APP_ENV", "testing")

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models.church import Church, ChurchDomain  # noqa: E402
from app.types import utcnow  # noqa: E402


@pytest.fixture()
def app():
    application = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        PLATFORM_DOMAIN="dos.betweensundaysconsulting.com",
        DEFAULT_CHURCH_SLUG=None,
        PROPAGATE_EXCEPTIONS=False,
    )
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    return _db


@pytest.fixture()
def journey(db):
    church = Church(
        slug="journey",
        name="The Journey Church",
        city="Jackson",
        state="MO",
        timezone="America/Chicago",
        accent_hex="#2563FF",
    )
    db.session.add(church)
    db.session.flush()
    db.session.add_all([
        ChurchDomain(
            church_id=church.id,
            host="app.thejourneychurchsemo.com",
            is_primary=True,
            verified_at=utcnow(),
        ),
        ChurchDomain(
            church_id=church.id,
            host="journey.dos.betweensundaysconsulting.com",
            is_primary=False,
            verified_at=utcnow(),
        ),
    ])
    db.session.commit()
    return church


@pytest.fixture()
def client(app):
    return app.test_client()
