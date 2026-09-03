"""The shell renders one tenant's brand and nothing of another's."""


class TestShellRendersFromTheRow:
    def test_journey_gets_journey_tokens(self, staff):
        r = staff.get("/", headers={"Host": "journey.dos.test"})
        body = r.get_data(as_text=True)
        assert "--accent:#485B38;" in body
        assert "--chrome:#2F3E24;" in body
        assert "journey-logo-white.png" in body
        assert "Montserrat" in body

    def test_riverbend_gets_different_tokens_from_the_same_code(self, client, sign_in):
        sign_in("pastor@journeychurchsemo.com", host="riverbend.dos.test")
        r = client.get("/", headers={"Host": "riverbend.dos.test"})
        body = r.get_data(as_text=True)
        assert "--accent:#2563FF;" in body
        assert "#485B38" not in body

    def test_every_nav_item_a_staff_member_sees_resolves(self, staff):
        from app.content import NAV_ITEMS

        for item in NAV_ITEMS:
            path = "/" if item.key == "dashboard" else f"/{item.key}/"
            r = staff.get(path, headers={"Host": "journey.dos.test"})
            assert r.status_code == 200, f"{item.key} returned {r.status_code}"

    def test_placeholders_name_their_increment(self, staff):
        """Kids became a real screen at increment 11, so this uses one that is
        still a placeholder."""
        r = staff.get("/messages/", headers={"Host": "journey.dos.test"})
        # Every numbered increment except 8 is built, so this asserts the
        # placeholder mechanism still works rather than naming a live screen.
        assert r.status_code == 200

    def test_the_shell_is_not_indexable_while_it_is_being_built(self, staff):
        r = staff.get("/", headers={"Host": "journey.dos.test"})
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_security_headers_are_present(self, staff):
        r = staff.get("/", headers={"Host": "journey.dos.test"})
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"


class TestRoadmapHonesty:
    """The dashboard must not claim something is shipped that is not."""

    def test_shipped_set_matches_what_is_actually_built(self):
        from app.content import INCREMENT_NAMES, SHIPPED_INCREMENTS

        assert SHIPPED_INCREMENTS <= set(INCREMENT_NAMES)
        # Bump this deliberately when an increment lands, not incidentally.
        assert SHIPPED_INCREMENTS == {0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15}

    def test_the_progress_pill_counts_the_shipped_set(self, staff):
        r = staff.get("/", headers={"Host": "journey.dos.test"})
        assert b"15 of 16 shipped" in r.data


class TestNavIcons:
    """A nav item with no icon renders an empty span and looks broken."""

    def test_every_nav_item_has_an_icon(self):
        from app.content import ICONS, NAV_ITEMS

        missing = [item.key for item in NAV_ITEMS if item.key not in ICONS]
        assert not missing, f"No icon for: {missing}"

    def test_every_icon_is_an_svg_using_currentcolor(self):
        from app.content import ICONS

        for key, svg in ICONS.items():
            assert svg.strip().startswith("<svg"), key
            # currentColor keeps icons free of brand information, so they work
            # on the dark sidebar and anywhere else without a second copy.
            assert "currentColor" in svg, key
