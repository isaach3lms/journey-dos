"""The 500 page must render when the database does not."""

from pathlib import Path

from sqlalchemy.exc import OperationalError


class TestStatic500:
    def test_the_file_exists_in_static(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "app" / "static" / "500.html"
        )
        assert path.exists(), "Run: flask build-error-pages"

    def test_it_does_not_extend_base(self):
        path = (
            Path(__file__).resolve().parent.parent
            / "app" / "static" / "500.html"
        )
        body = path.read_text(encoding="utf-8")
        assert "{%" not in body and "{{" not in body, "The 500 page must not render Jinja."
        assert "<style>" in body, "The 500 page must carry its own CSS, not link to it."


class TestErrorHandling:
    def test_a_broken_view_returns_the_static_page_not_a_traceback(self, app, client):
        app.config["FORCE_ERROR_PAGES"] = True
        app.debug = False

        @app.get("/boom")
        def boom():
            raise RuntimeError("kaboom")

        r = client.get("/boom?tenant=journey", headers={"Host": "localhost"})
        assert r.status_code == 500
        assert b"kaboom" not in r.data
        assert b"Something broke on our end" in r.data

    def test_the_500_page_renders_with_the_database_unreachable(self, app, client):
        """The reason this page is decoupled in the first place."""
        app.config["FORCE_ERROR_PAGES"] = True
        app.debug = False

        @app.get("/dbdown")
        def dbdown():
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        r = client.get("/dbdown?tenant=journey", headers={"Host": "localhost"})
        assert r.status_code == 500
        assert b"Something broke on our end" in r.data

    def test_a_404_inside_a_known_tenant_is_branded(self, client):
        r = client.get("/nope/?tenant=journey", headers={"Host": "localhost"})
        assert r.status_code == 404
        assert b"The Journey Church" in r.data

    def test_a_non_500_http_error_is_not_reported_as_a_crash(self, app, client):
        app.config["FORCE_ERROR_PAGES"] = True
        app.debug = False

        @app.get("/forbidden")
        def forbidden():
            from flask import abort
            abort(403)

        r = client.get("/forbidden?tenant=journey", headers={"Host": "localhost"})
        assert r.status_code == 403
