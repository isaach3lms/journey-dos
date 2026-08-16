"""Invariant 4: the 500 page renders with the database unreachable."""

from __future__ import annotations

import pytest

from app import create_app

# A database that cannot be opened, on any dialect, with no server required.
UNREACHABLE_DB = "sqlite:////nonexistent-directory/dos.sqlite"


def test_500_renders_when_the_database_is_gone():
    """The exact production fault: Postgres unreachable, every request 500s.

    If the 500 template extended base.html and base.html queried anything,
    this test would raise instead of returning a page.
    """
    app = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=UNREACHABLE_DB,
        PLATFORM_DOMAIN="dos.example.com",
        PROPAGATE_EXCEPTIONS=False,
        TESTING=False,
    )
    client = app.test_client()
    response = client.get("/", headers={"Host": "app.thejourneychurchsemo.com"})

    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert "Something went wrong on our end" in body
    assert "topbar" not in body  # base.html was not involved
    assert "Traceback" not in body


def test_500_page_contains_no_stack_trace_or_config(app, client, journey):
    @app.route("/boom-for-test")
    def boom():
        raise RuntimeError("secret connection string in the message")

    response = client.get("/boom-for-test", headers={"Host": "app.thejourneychurchsemo.com"})
    body = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "secret connection string" not in body


def test_readyz_reports_degraded_instead_of_crashing():
    app = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=UNREACHABLE_DB,
        PLATFORM_DOMAIN="dos.example.com",
        PROPAGATE_EXCEPTIONS=False,
        TESTING=False,
    )
    response = app.test_client().get("/readyz")
    assert response.status_code == 503
    assert response.json["database"] == "unreachable"


def test_livez_answers_without_a_database_or_a_tenant():
    app = create_app(
        "testing",
        SQLALCHEMY_DATABASE_URI=UNREACHABLE_DB,
        PLATFORM_DOMAIN="dos.example.com",
    )
    response = app.test_client().get("/livez", headers={"Host": "unknown.example.com"})
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_unknown_host_is_404_not_a_default_tenant(client, journey):
    response = client.get("/", headers={"Host": "someone-elses-church.com"})
    assert response.status_code == 404
    assert "The Journey Church" not in response.get_data(as_text=True)
