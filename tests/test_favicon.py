"""The icon in a browser tab.

There was none. No screen carried a `rel="icon"` tag, so every browser fell
back to asking the site root for `/favicon.ico`, which did not exist either.
Two consequences, and the second is the one that matters: a blank page icon on
every tab, and a 404 on every cold load of every page.

The rule being protected here is not "a favicon exists". It is that the icon
belongs to the church whose address was used. A platform serving several
churches from one codebase has exactly one way to get this badly wrong, which
is to put a path in a template instead of a token in the brand, and then one
church's logo sits in another church's tab strip.
"""

from pathlib import Path

import pytest

from app.brand import BETWEEN_SUNDAYS, JOURNEY, PALETTES
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}
THEIRS = {"Host": RIVERBEND_HOST}

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


class TestTheFilesAreThere:
    def test_every_size_a_browser_asks_for_exists(self):
        """A link tag pointing at a missing file is worse than no tag: the
        browser asks, gets a 404, and falls back to nothing anyway."""
        for size in (16, 32, 192):
            assert (STATIC / JOURNEY.icon_dir / f"favicon-{size}.png").exists(), size

    def test_the_ico_carries_the_small_sizes(self):
        """One file answering the bare root request, with the sizes a tab
        strip and a bookmark bar actually pick from."""
        from PIL import Image

        with Image.open(STATIC / JOURNEY.icon_dir / "favicon.ico") as ico:
            assert {(16, 16), (32, 32), (48, 48)} <= set(ico.info["sizes"])

    def test_the_mark_is_square_at_every_size(self):
        """The source is padded and 408 by 404. Scaling that straight to a
        square turns a circular logo into an oval, which is invisible in the
        file and obvious in a tab."""
        from PIL import Image

        for size in (16, 32, 192):
            with Image.open(STATIC / JOURNEY.icon_dir / f"favicon-{size}.png") as img:
                assert img.size == (size, size)

    def test_the_small_sizes_stay_small(self):
        """A favicon is fetched on the first paint of every page. A 40KB one
        is a design that was never looked at."""
        for size, ceiling in ((16, 4_000), (32, 8_000)):
            path = STATIC / JOURNEY.icon_dir / f"favicon-{size}.png"
            assert path.stat().st_size < ceiling, (size, path.stat().st_size)


class TestThePagesPointAtIt:
    def test_the_sign_in_page_carries_the_icon(self, client):
        """The screen this was asked for. Somebody signing in on a laptop is
        looking at a tab, and until now it was blank."""
        page = client.get("/auth/login", headers=H).get_data(as_text=True)

        assert 'rel="icon"' in page
        assert JOURNEY.icon_dir in page

    def test_the_staff_app_carries_it(self, client, staff):
        page = client.get("/", headers=H).get_data(as_text=True)

        assert 'rel="icon"' in page

    def test_the_member_app_carries_it(self, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        page = client.get("/me/", headers=H).get_data(as_text=True)

        assert 'rel="icon"' in page

    def test_the_public_pages_carry_it(self, client):
        """A guest filling in a connect card has a tab open too."""
        page = client.get("/welcome/", headers=H).get_data(as_text=True)

        assert 'rel="icon"' in page

    def test_the_link_is_versioned(self, client):
        """A church that changes its mark and sees the old one for a week
        files it as a bug in the logo, not in a cache."""
        page = client.get("/auth/login", headers=H).get_data(as_text=True)

        assert "favicon-32.png?v=" in page


class TestItBelongsToOneChurch:
    def test_a_church_with_no_mark_gets_no_tag(self, client):
        """Not a fallback to somebody else's logo. A blank tab icon is a
        church that has not supplied a mark; another church's logo in the tab
        strip is a platform that leaks tenants."""
        page = client.get("/auth/login", headers=THEIRS).get_data(as_text=True)

        assert 'rel="icon"' not in page
        assert JOURNEY.icon_dir not in page

    def test_the_other_church_has_no_icon_folder_set(self):
        assert BETWEEN_SUNDAYS.icon_dir == ""

    def test_the_path_is_a_brand_token_not_a_template_literal(self):
        """The rule this file exists to enforce. A path written into the
        markup is how one church ends up wearing another's logo, and it would
        look correct on the only church anybody tested."""
        head = (Path(__file__).resolve().parent.parent / "app" / "templates"
                / "partials" / "head_meta.html").read_text()

        assert "palette.icon_dir" in head
        assert "img/icons/journey" not in head

    def test_every_palette_declares_the_token(self):
        for key, palette in PALETTES.items():
            assert hasattr(palette, "icon_dir"), key


class TestTheRootRequest:
    def test_the_bare_path_serves_the_icon(self, client):
        """Browsers ask for this on their own, for a typed address, a
        bookmark or a history entry, whatever the head says. Every one of
        those was a 404 before."""
        answer = client.get("/favicon.ico", headers=H)

        assert answer.status_code == 200
        assert answer.mimetype in ("image/x-icon", "image/vnd.microsoft.icon")
        assert answer.data[:4] == b"\x00\x00\x01\x00"

    def test_it_needs_no_sign_in(self, client):
        """The request happens before anybody has signed in, on the sign-in
        page itself."""
        assert client.get("/favicon.ico", headers=H).status_code == 200

    def test_a_church_without_a_mark_gets_a_404(self, client):
        answer = client.get("/favicon.ico", headers=THEIRS)

        assert answer.status_code == 404

    def test_it_is_not_one_file_for_every_church(self, client):
        """A static file at the root would serve one church's logo to all of
        them, which is why this is a route."""
        ours = client.get("/favicon.ico", headers=H)
        theirs = client.get("/favicon.ico", headers=THEIRS)

        assert ours.status_code != theirs.status_code

    def test_it_is_cached_by_the_browser(self, client):
        """Served on the first paint of every page. Without a long max-age
        it is re-fetched all day for a file that changes once a year."""
        answer = client.get("/favicon.ico", headers=H)

        assert answer.cache_control.max_age
        assert answer.cache_control.max_age >= 60 * 60 * 24

    def test_a_kiosk_can_still_fetch_it(self, app, db, client):
        """The check-in guard sends a kiosk login back to the kiosk for any
        endpoint not on its allowlist. A favicon redirected to a PIN screen
        renders nothing and costs a request on every page the tablet opens.
        """
        from app.kiosk import KIOSK_ENDPOINTS

        assert "pwa.favicon" in KIOSK_ENDPOINTS


class TestChangingTheMarkTakesEffect:
    def test_the_icon_is_part_of_the_asset_fingerprint(self):
        """The cache version is derived from the files it busts. An icon left
        out of it means a church swaps its logo, deploys, and keeps seeing
        the old one with nothing to explain why."""
        source = (Path(__file__).resolve().parent.parent / "app" / "blueprints"
                  / "pwa.py").read_text()
        fingerprint = source.split("def _asset_fingerprint")[1].split("def ")[0]

        assert "favicon" in fingerprint

    def test_the_fingerprint_moves_when_the_icon_does(self, tmp_path):
        """Asserted by changing the file, because a name appearing in the
        function is not proof it is being read."""
        import hashlib
        import importlib

        from app.blueprints import pwa

        before = pwa._asset_fingerprint()
        icon = STATIC / JOURNEY.icon_dir / "favicon-32.png"
        original = icon.read_bytes()
        try:
            icon.write_bytes(original + b"\x00")
            assert pwa._asset_fingerprint() != before
        finally:
            icon.write_bytes(original)

        assert pwa._asset_fingerprint() == before
