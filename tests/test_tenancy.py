"""Tenant resolution, including the C.2 amendment for custom domains."""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.church import Church, ChurchDomain
from app.tenancy import normalize_host, resolve_church, subdomain_slug


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("App.TheJourneyChurchSEMO.com", "app.thejourneychurchsemo.com"),
        ("app.thejourneychurchsemo.com:443", "app.thejourneychurchsemo.com"),
        ("www.thejourneychurchsemo.com", "thejourneychurchsemo.com"),
        ("localhost:5000", "localhost"),
        ("app.thejourneychurchsemo.com.", "app.thejourneychurchsemo.com"),
        (None, ""),
    ],
)
def test_host_normalization(raw, expected):
    assert normalize_host(raw) == expected


def test_custom_domain_resolves_the_tenant(app, journey):
    assert resolve_church("app.thejourneychurchsemo.com").id == journey.id


def test_custom_domain_is_case_and_port_insensitive(app, journey):
    assert resolve_church("APP.TheJourneyChurchSEMO.com:443").id == journey.id


def test_platform_alias_resolves_the_same_tenant(app, journey):
    """Both hosts must work at once, or DNS cutover has a dark window."""
    assert resolve_church("journey.dos.betweensundaysconsulting.com").id == journey.id


def test_platform_subdomain_fallback_by_slug(app, journey):
    """A tenant with no domain row still reaches its slug host."""
    db.session.query(ChurchDomain).delete()
    db.session.commit()
    assert resolve_church("journey.dos.betweensundaysconsulting.com").id == journey.id


def test_unknown_host_resolves_to_nothing(app, journey):
    assert resolve_church("randomchurch.org") is None


def test_deeper_subdomain_is_not_treated_as_a_slug(app, journey):
    assert resolve_church("a.b.dos.betweensundaysconsulting.com") is None


def test_suspended_church_does_not_load(app, client, journey):
    journey.status = "suspended"
    db.session.commit()
    response = client.get("/", headers={"Host": "app.thejourneychurchsemo.com"})
    assert response.status_code == 404


def test_two_churches_never_share_a_host(app, journey):
    other = Church(slug="riverbend", name="Riverbend Fellowship")
    db.session.add(other)
    db.session.flush()
    db.session.add(
        ChurchDomain(church_id=other.id, host="app.thejourneychurchsemo.com")
    )
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_subdomain_slug_helper():
    platform = "dos.betweensundaysconsulting.com"
    assert subdomain_slug("journey.dos.betweensundaysconsulting.com", platform) == "journey"
    assert subdomain_slug("dos.betweensundaysconsulting.com", platform) is None
    assert subdomain_slug("app.thejourneychurchsemo.com", platform) is None
