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


class TestTheDashboardIsForAPastor:
    """The build scaffolding is gone.

    "What this page proves" and "What comes next" existed to show the platform
    was real and to track what was still coming. Everything is built, so they
    became a pastor's dashboard talking to him about increments.
    """

    def test_shipped_set_matches_what_is_actually_built(self):
        from app.content import INCREMENT_NAMES, SHIPPED_INCREMENTS

        # Still read by the nav to decide whether a section is a working
        # screen or a placeholder.
        assert SHIPPED_INCREMENTS <= set(INCREMENT_NAMES)
        assert SHIPPED_INCREMENTS == set(range(16))

    def test_the_dashboard_says_nothing_about_increments(self, staff):
        body = staff.get(
            "/", headers={"Host": "journey.dos.test"}
        ).get_data(as_text=True)
        for scaffolding in (
            "What this page proves", "What comes next", "increments live",
            "of 16 shipped", "SHIPPED",
        ):
            assert scaffolding not in body, scaffolding

    def test_it_still_shows_the_things_a_pastor_opens_it_for(self, staff):
        body = staff.get(
            "/", headers={"Host": "journey.dos.test"}
        ).get_data(as_text=True)
        assert "Needs a person, not an email" in body
        assert "Running without staff time" in body


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


class TestTheNavDoesNotCallBuiltThingsUnbuilt:
    """The badge means "arrives at increment N".

    Dashboard was built in increment 3 and never marked ready, so it carried a
    "3" for thirteen increments. Nothing failed, the screen just quietly told
    every pastor a finished feature was not finished.
    """

    def test_no_shipped_section_still_shows_an_increment_badge(self):
        from app.content import NAV_ITEMS, SHIPPED_INCREMENTS

        lying = [
            item.key for item in NAV_ITEMS
            if not item.ready and item.increment in SHIPPED_INCREMENTS
        ]
        assert not lying, (
            f"These are built but still badged as unbuilt: {lying}"
        )

    def test_the_badge_is_absent_from_the_sidebar_when_everything_is_built(
        self, staff
    ):
        from app.content import NAV_ITEMS

        response = staff.get("/", headers={"Host": "journey.dos.test"})
        body = response.get_data(as_text=True)
        sidebar = body[body.index('class="navstrip"'): body.index('class="sidefoot"')]

        if all(item.ready for item in NAV_ITEMS):
            assert 'class="inc"' not in sidebar


class TestFlashesAreRenderedOnce:
    """Flashes were rendered per page, and the roster never had the block.

    Every flash that redirected there vanished, including the approval message
    from the previous increment. Nothing failed; the screen just said nothing.
    """

    def test_the_staff_shell_renders_them(self):
        from pathlib import Path

        base = (
            Path(__file__).resolve().parent.parent
            / "app" / "templates" / "base.html"
        ).read_text()
        assert "get_flashed_messages" in base

    def test_no_staff_page_renders_its_own(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app" / "templates"
        offenders = [
            path.name for path in root.rglob("*.html")
            if '{% extends "base.html" %}' in path.read_text()
            and "get_flashed_messages" in path.read_text()
        ]
        assert not offenders, f"These duplicate the shell's flash block: {offenders}"

    def test_a_flash_reaches_the_roster(self, staff):
        r = staff.post(
            "/people/archive/",
            headers={"Host": "journey.dos.test"},
            follow_redirects=True,
        )
        assert b"Tick somebody first" in r.data


class TestTheStaffPageIsWhite:
    """The work area behind the cards, which used to be the cream page
    colour. Everything else that needs a page colour still has one."""

    CSS = None

    @classmethod
    def css(cls):
        from pathlib import Path

        if cls.CSS is None:
            cls.CSS = (Path(__file__).resolve().parent.parent
                       / "app" / "static" / "css" / "app.css").read_text()
        return cls.CSS

    def rule(self, selector):
        css = self.css()
        block = css[css.index(selector):]
        return block[:block.index("}")]

    def test_the_work_area_is_white(self):
        assert "background:var(--white)" in self.rule(".main{")

    def test_the_sidebar_is_untouched(self):
        assert "background:var(--chrome)" in self.rule(".sidebar{")

    def test_the_error_page_keeps_a_page_colour(self):
        """It has a white card on it, and it must not touch the database, so
        it cannot be fixed later by a template change."""
        assert "background:var(--bone)" in self.rule(".errwrap{")

    def test_the_member_preview_keeps_a_page_colour(self):
        """Staff see the member app inside a white phone frame."""
        assert "background:var(--bone)" in self.rule(".memberbody{")

    def test_cards_still_read_against_it(self):
        card = self.rule(".card{")
        assert "background:var(--white)" in card
        assert "box-shadow:var(--shadow-sm)" in card
        assert "border:1px solid var(--line-soft)" in card
