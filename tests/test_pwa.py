"""Increment 16: installable to a home screen.

The caching rules carry the risk. A church tablet and a family iPad are both
shared devices, and a cache that outlives a session shows the next person
somebody else's record.
"""

import json

import pytest

from app.blueprints.pwa import CACHE_VERSION
from app.models import Church
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


class TestManifest:
    def _manifest(self, client, host=JOURNEY_HOST):
        response = client.get("/manifest.webmanifest", headers={"Host": host})
        return response, json.loads(response.get_data(as_text=True))

    def test_it_is_served_with_the_right_mime_type(self, client, journey):
        """A browser silently ignores a manifest sent as text/html, and the
        install prompt never appears with nothing in the console to say why."""
        response, _ = self._manifest(client)
        assert response.status_code == 200
        assert response.mimetype == "application/manifest+json"

    def test_it_carries_the_church_name_not_ours(self, client, journey):
        """It should read as their church's app on a home screen."""
        _, document = self._manifest(client)
        assert "Journey" in document["name"]
        assert "Between Sundays" not in json.dumps(document)

    def test_the_short_name_identifies_the_church(self, db, client, journey):
        """"The Journey Church" under an icon should read "Journey".

        Taking the first word produced "The", which is wrong for the majority
        of church names because most of them start with an article.
        """
        _, document = self._manifest(client)
        assert document["short_name"] == "Journey"
        assert len(document["short_name"]) <= 12

    @pytest.mark.parametrize(
        "full,expected",
        [
            ("The Journey Church", "Journey"),
            ("Riverbend Fellowship", "Riverbend"),
            ("Grace Community Church", "Grace"),
            ("St Andrews", "St Andrews"),
            ("Church of the Redeemer", "Redeemer"),
        ],
    )
    def test_short_names_across_the_shapes_churches_use(self, full, expected):
        from app.blueprints.pwa import short_name_for

        class Stub:
            app_name = None
            name = full

        assert short_name_for(Stub()) == expected

    def test_a_name_made_only_of_skipped_words_still_produces_something(self):
        from app.blueprints.pwa import short_name_for

        class Stub:
            app_name = None
            name = "The Church"

        assert short_name_for(Stub())

    def test_it_uses_the_church_brand_colours(self, client, journey):
        _, document = self._manifest(client)
        assert document["theme_color"] == "#2F3E24"
        assert document["background_color"] == "#F2F0E7"

    def test_standalone_display(self, client, journey):
        """Without it an installed app still shows an address bar and looks
        like a website."""
        _, document = self._manifest(client)
        assert document["display"] == "standalone"

    def test_it_offers_a_maskable_icon(self, client, journey):
        """Android crops to the launcher's shape and a non-maskable icon comes
        out with its corners cut off."""
        _, document = self._manifest(client)
        purposes = {icon.get("purpose") for icon in document["icons"]}
        assert "maskable" in purposes

    def test_required_icon_sizes_are_present(self, client, journey):
        _, document = self._manifest(client)
        sizes = {icon["sizes"] for icon in document["icons"]}
        assert {"192x192", "512x512"} <= sizes

    def test_every_icon_file_actually_exists(self, client, journey, app):
        """A manifest pointing at a missing icon fails the install prompt with
        no visible error."""
        from pathlib import Path

        _, document = self._manifest(client)
        static = Path(app.static_folder)
        for icon in document["icons"]:
            name = icon["src"].split("/static/", 1)[-1]
            path = static / name
            assert path.exists(), icon["src"]
            assert path.stat().st_size > 0

    def test_two_churches_get_two_different_manifests(self, db, client, journey):
        _, mine = self._manifest(client)
        _, theirs = self._manifest(client, host=RIVERBEND_HOST)
        assert mine["name"] != theirs["name"]
        assert mine["theme_color"] != theirs["theme_color"]

    def test_an_unmapped_host_does_not_serve_a_manifest(self, app, client):
        app.config["ALLOW_TENANT_QUERY_OVERRIDE"] = False
        app.config["PLATFORM_DOMAIN"] = ""
        response = client.get(
            "/manifest.webmanifest", headers={"Host": "nobody.example.com"}
        )
        assert response.status_code == 404

    def test_it_needs_no_sign_in(self, client, journey):
        """The manifest is fetched before anybody has signed in."""
        response, _ = self._manifest(client)
        assert response.status_code == 200


class TestServiceWorker:
    def _worker(self, client):
        return client.get("/sw.js", headers={"Host": JOURNEY_HOST})

    def test_it_is_served_from_the_root(self, app):
        """A worker only controls paths at or below its own URL. From /static/
        it would control nothing that matters."""
        rules = [str(r) for r in app.url_map.iter_rules() if r.endpoint == "pwa.service_worker"]
        assert rules == ["/sw.js"]

    def test_it_is_javascript(self, client, journey):
        response = self._worker(client)
        assert response.status_code == 200
        assert "javascript" in response.mimetype

    def test_it_is_never_cached_itself(self, client, journey):
        """Otherwise a phone keeps running last month's caching rules."""
        response = self._worker(client)
        assert "no-store" in response.headers["Cache-Control"]

    def test_it_declares_root_scope(self, client, journey):
        assert self._worker(client).headers["Service-Worker-Allowed"] == "/"

    def test_it_carries_a_version(self, client, journey):
        body = self._worker(client).get_data(as_text=True)
        assert CACHE_VERSION in body

    def test_it_separates_personal_pages_from_static_assets(self, client, journey):
        """The whole privacy design: one cache is deletable, one is not
        sensitive."""
        body = self._worker(client).get_data(as_text=True)
        assert "-assets" in body
        assert "-pages" in body

    def test_it_clears_personal_pages_on_a_message(self, client, journey):
        body = self._worker(client).get_data(as_text=True)
        assert "clear-personal-cache" in body
        assert "caches.delete(PAGES)" in body

    def test_it_never_caches_auth_pages(self, client, journey):
        """A cached reset link is a security problem, not a convenience."""
        body = self._worker(client).get_data(as_text=True)
        assert "/auth/" in body
        assert "isAuth" in body

    def test_it_only_handles_get(self, client, journey):
        """Caching a POST would replay somebody's action."""
        body = self._worker(client).get_data(as_text=True)
        assert "request.method !== 'GET'" in body

    def test_it_only_handles_its_own_origin(self, client, journey):
        """A tenant subdomain gets its own worker and its own caches, so
        nothing crosses between churches."""
        body = self._worker(client).get_data(as_text=True)
        assert "url.origin !== self.location.origin" in body

    def test_pages_are_network_first(self, client, journey):
        """A member should see current data whenever they can. The cache is a
        fallback for a tunnel, not the default."""
        body = self._worker(client).get_data(as_text=True)
        assert body.index("fetch(request)") < body.index("caches.match(OFFLINE_URL)")

    def test_it_deletes_caches_from_older_versions(self, client, journey):
        body = self._worker(client).get_data(as_text=True)
        assert "caches.delete(name)" in body
        assert "startsWith(VERSION)" in body


class TestOfflinePage:
    def test_it_renders_without_a_session(self, client, journey):
        response = client.get("/offline/", headers={"Host": JOURNEY_HOST})
        assert response.status_code == 200
        assert b"You are offline" in response.data

    def test_it_says_nothing_about_a_person(self, db, client, journey):
        """It renders from a cache on a device that may have been handed to
        somebody else."""
        response = client.get("/offline/", headers={"Host": JOURNEY_HOST})
        body = response.get_data(as_text=True)
        for personal in ("Alicia", "member@", "Signed in as", "Sign out"):
            assert personal not in body

    def test_it_carries_the_church_brand(self, client, journey):
        response = client.get("/offline/", headers={"Host": JOURNEY_HOST})
        assert b"--accent:#485B38;" in response.data


class TestPagesAdvertiseTheApp:
    def test_the_member_app_links_the_manifest(self, db, journey, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b'rel="manifest"' in r.data

    def test_the_login_page_links_the_manifest(self, client, journey):
        """Install often happens before anybody signs in."""
        r = client.get("/auth/login", headers={"Host": JOURNEY_HOST})
        assert b'rel="manifest"' in r.data

    def test_the_theme_colour_matches_the_church(self, db, journey, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b'<meta name="theme-color" content="#2F3E24">' in r.data

    def test_an_apple_touch_icon_is_offered(self, db, journey, member):
        """iOS ignores the manifest icons and uses this."""
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"apple-touch-icon" in r.data

    def test_the_ios_title_is_the_church_not_the_page(self, db, journey, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b'name="apple-mobile-web-app-title"' in r.data

    def test_the_worker_is_registered(self, db, journey, member):
        r = member.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"serviceWorker.register" in r.data

    def test_signing_out_asks_the_worker_to_forget(self, db, journey, member):
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        assert b"clear-personal-cache" in r.data


class TestNoRegression:
    def test_the_manifest_does_not_break_the_no_colour_rule(self):
        """Brand values come from tokens, not from markup, as everywhere else."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app" / "templates" / "pwa"
        for path in root.rglob("*"):
            if path.is_file():
                assert not re.search(r"#[0-9A-Fa-f]{6}\b", path.read_text()), path.name

    def test_static_assets_still_serve(self, client, journey):
        r = client.get("/static/css/app.css", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200


class TestEveryShellCarriesTheSameHead:
    """Five templates had their own <head> and drifted apart.

    The login page ended up without a manifest link, on the one screen most
    people are looking at when they decide to install. One partial now, so the
    next addition cannot land in four places out of five.
    """

    SHELLS = (
        "base.html", "public.html", "member/base.html",
        "auth/login.html", "kids/kiosk_base.html",
    )

    def test_no_shell_defines_its_own_head_tags(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app" / "templates"
        for name in self.SHELLS:
            body = (root / name).read_text(encoding="utf-8")
            assert 'include "partials/head_meta.html"' in body, name
            assert "fonts.googleapis.com" not in body, name

    def test_every_shell_registers_the_worker(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app" / "templates"
        for name in self.SHELLS:
            body = (root / name).read_text(encoding="utf-8")
            assert 'include "partials/pwa_script.html"' in body, name

    def test_the_kiosk_and_the_staff_app_still_render(self, staff):
        for path in ("/", "/kids/kiosk/", "/auth/login"):
            r = staff.get(path, headers={"Host": JOURNEY_HOST})
            assert r.status_code in (200, 302), path
