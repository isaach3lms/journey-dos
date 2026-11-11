"""Tenant resolution. The rule is: resolve exactly one church, or fail closed."""

import pytest

from app.tenancy import host_without_port, subdomain_for


class TestSubdomainParsing:
    def test_reads_the_leftmost_label(self):
        assert subdomain_for("journey.dos.test", "dos.test") == "journey"

    def test_ignores_the_port(self):
        assert subdomain_for("journey.dos.test:8080", "dos.test") == "journey"

    def test_is_case_insensitive(self):
        assert subdomain_for("JOURNEY.DOS.TEST", "dos.test") == "journey"

    def test_rejects_a_deeper_host(self):
        assert subdomain_for("a.b.dos.test", "dos.test") is None

    def test_rejects_the_apex(self):
        assert subdomain_for("dos.test", "dos.test") is None

    def test_rejects_a_foreign_domain(self):
        assert subdomain_for("journey.evil.test", "dos.test") is None

    def test_rejects_a_suffix_that_only_looks_like_the_platform(self):
        assert subdomain_for("journey.notdos.test", "dos.test") is None

    def test_no_platform_domain_means_no_match(self):
        assert subdomain_for("journey.dos.test", "") is None

    def test_host_without_port(self):
        assert host_without_port("Journey.DOS.test:5000") == "journey.dos.test"


class TestResolutionInRequests:
    def test_subdomain_resolves_the_church(self, client):
        r = client.get("/auth/login", headers={"Host": "journey.dos.test"})
        assert r.status_code == 200
        assert b"The Journey Church" in r.data

    def test_a_different_subdomain_resolves_a_different_church(self, client):
        r = client.get("/auth/login", headers={"Host": "riverbend.dos.test"})
        assert r.status_code == 200
        assert b"Riverbend Fellowship" in r.data
        assert b"The Journey Church" not in r.data

    def test_unknown_subdomain_is_a_404_not_a_fallback(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        r = client.get("/auth/login", headers={"Host": "nosuchchurch.dos.test"})
        assert r.status_code == 404

    def test_reserved_subdomain_never_resolves_a_church(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        r = client.get("/auth/login", headers={"Host": "www.dos.test"})
        assert r.status_code == 404

    def test_inactive_church_does_not_resolve(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        r = client.get("/auth/login", headers={"Host": "closed.dos.test"})
        assert r.status_code == 404

    def test_custom_domain_wins_over_subdomain(self, app, client, db):
        from app.models import Church

        church = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        church.custom_domain = "riverbendchurch.org"
        db.session.commit()

        r = client.get("/auth/login", headers={"Host": "riverbendchurch.org"})
        assert r.status_code == 200
        assert b"Riverbend Fellowship" in r.data

    def test_query_override_works_in_development_only(self, app, client):
        r = client.get("/auth/login?tenant=riverbend", headers={"Host": "localhost"})
        assert r.status_code == 200
        assert b"Riverbend Fellowship" in r.data

        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        r = client.get("/auth/login?tenant=riverbend", headers={"Host": "localhost"})
        assert r.status_code == 404

    def test_health_endpoints_need_no_tenant(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        assert client.get("/healthz", headers={"Host": "unknown.test"}).status_code == 200
        assert client.get("/readyz", headers={"Host": "unknown.test"}).status_code == 200


class TestProductionReachability:
    """A deploy with no resolvable host 404s everything while looking healthy."""

    def test_a_custom_domain_is_how_a_tenant_is_reached_without_a_platform_domain(
        self, app, client, db
    ):
        from app.models import Church

        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        app.config["PLATFORM_DOMAIN"] = ""

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        church.custom_domain = "journey-dos-app.onrender.com"
        db.session.commit()

        r = client.get("/auth/login", headers={"Host": "journey-dos-app.onrender.com"})
        assert r.status_code == 200
        assert b"The Journey Church" in r.data

    def test_one_host_cannot_point_at_two_churches(self, db):
        from app.models import Church

        a = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        b = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        a.custom_domain = "shared.example.org"
        db.session.commit()

        b.custom_domain = "shared.example.org"
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_the_health_check_passes_even_when_nothing_resolves(self, app, client):
        """Which is exactly why the boot warning exists."""
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        app.config["PLATFORM_DOMAIN"] = ""
        assert client.get("/healthz", headers={"Host": "unknown.example"}).status_code == 200
        assert client.get("/", headers={"Host": "unknown.example"}).status_code == 404
