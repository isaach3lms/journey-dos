"""The shell renders one tenant's brand and nothing of another's."""


class TestShellRendersFromTheRow:
    def test_journey_gets_journey_tokens(self, client):
        r = client.get("/", headers={"Host": "journey.dos.test"})
        body = r.get_data(as_text=True)
        assert "--accent:#485B38;" in body
        assert "--chrome:#2F3E24;" in body
        assert "journey-logo-white.png" in body
        assert "Montserrat" in body

    def test_riverbend_gets_different_tokens_from_the_same_code(self, client):
        r = client.get("/", headers={"Host": "riverbend.dos.test"})
        body = r.get_data(as_text=True)
        assert "--accent:#2563FF;" in body
        assert "#485B38" not in body

    def test_every_nav_item_resolves(self, client):
        from app.content import NAV_ITEMS

        for item in NAV_ITEMS:
            path = "/" if item.key == "dashboard" else f"/{item.key}/"
            r = client.get(path, headers={"Host": "journey.dos.test"})
            assert r.status_code == 200, f"{item.key} returned {r.status_code}"

    def test_placeholders_name_their_increment(self, client):
        r = client.get("/kids/", headers={"Host": "journey.dos.test"})
        assert b"increment 11" in r.data

    def test_the_shell_is_not_indexable_while_it_is_being_built(self, client):
        r = client.get("/", headers={"Host": "journey.dos.test"})
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_security_headers_are_present(self, client):
        r = client.get("/", headers={"Host": "journey.dos.test"})
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
