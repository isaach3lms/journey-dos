"""Boot-time and failure-mode guards.

These cover the three things that only break in production, which is exactly
where nobody wants to discover them:

1. A missing DATABASE_URL on Render silently writing to ephemeral disk.
2. A 500 that crashes inside its own error handler when Postgres is down.
3. Schema drift, because db.create_all() never alters an existing table.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app import ConfigError, _database_uri, create_app

ROOT = Path(__file__).resolve().parent.parent

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _strip_html_comments(markup: str) -> str:
    """Drop HTML comments before asserting on markup.

    The comments in 500.html deliberately name base.html, the masthead, and
    brand.py while explaining why the page touches none of them.
    """
    return _HTML_COMMENT.sub("", markup)


# ----------------------------------------------------------------------
# 1. Configuration
# ----------------------------------------------------------------------
class TestDatabaseUri:
    def test_render_legacy_scheme_gets_the_explicit_driver(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
        assert _database_uri() == "postgresql+psycopg2://u:p@host:5432/db"

    def test_bare_postgresql_scheme_gets_the_explicit_driver(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
        assert _database_uri() == "postgresql+psycopg2://u:p@host/db"

    def test_only_the_scheme_is_rewritten(self, monkeypatch):
        # A password containing the scheme string must survive intact.
        monkeypatch.setenv("DATABASE_URL", "postgres://user:postgres://@host/db")
        assert _database_uri().count("postgresql+psycopg2://") == 1

    def test_sqlite_is_untouched(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///journey.db")
        assert _database_uri() == "sqlite:///journey.db"

    def test_local_dev_still_falls_back_to_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        assert _database_uri() == "sqlite:///journey.db"

    def test_missing_database_url_on_render_refuses_to_boot(self, monkeypatch):
        """The expensive failure this prevents.

        Without the guard, Render boots, writes every person, gift, and
        check-in of the day to a SQLite file on ephemeral disk, and loses all
        of it on the next deploy with no error anywhere.
        """
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("RENDER", "true")
        with pytest.raises(ConfigError, match="DATABASE_URL"):
            _database_uri()

    def test_the_app_will_not_start_without_a_database_on_render(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("RENDER", "true")
        with pytest.raises(ConfigError):
            create_app()


# ----------------------------------------------------------------------
# 2. The 500 page
# ----------------------------------------------------------------------
@pytest.fixture()
def crashing_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("RENDER", raising=False)
    app = create_app()

    @app.route("/__boom")
    def boom():
        raise RuntimeError("simulated failure")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


class TestServerErrorPage:
    def test_it_returns_500_and_renders(self, crashing_app):
        response = crashing_app.test_client().get("/__boom")
        assert response.status_code == 500
        assert b"This one is on us" in response.data

    def test_it_renders_when_the_database_is_unreachable(self, crashing_app, monkeypatch):
        """The real outage scenario, where any query inside the handler dies."""
        from app.extensions import db

        def explode(*args, **kwargs):
            raise RuntimeError("database is unreachable")

        monkeypatch.setattr(db.session, "execute", explode, raising=False)
        monkeypatch.setattr(db.session, "scalar", explode, raising=False)
        monkeypatch.setattr(db.session, "rollback", explode, raising=False)

        response = crashing_app.test_client().get("/__boom")
        assert response.status_code == 500
        assert b"This one is on us" in response.data

    def test_it_loads_no_external_stylesheet(self, crashing_app):
        body = crashing_app.test_client().get("/__boom").data
        assert b"<style>" in body
        assert b'rel="stylesheet"' not in body
        assert b"app.css" not in body

    def test_it_renders_no_masthead(self, crashing_app):
        body = _strip_html_comments(
            crashing_app.test_client().get("/__boom").data.decode()
        )
        assert "masthead" not in body
        assert "<nav" not in body


class TestFiveHundredTemplateStaysDecoupled:
    """Structural guards, so a future tidy-up cannot quietly reintroduce the bug."""

    template = ROOT / "app" / "templates" / "500.html"

    def test_it_extends_nothing(self):
        body = _strip_html_comments(self.template.read_text())
        assert "{% extends" not in body, (
            "500.html must not extend base.html. base.html renders the "
            "masthead and reads `brand` from a context processor, so the "
            "error handler would crash during the exact outage this page "
            "exists to survive."
        )

    def test_it_reads_no_template_context(self):
        # Comments explain WHY the page is decoupled and legitimately name
        # the things it must not use. Assert on markup, not prose.
        body = _strip_html_comments(self.template.read_text())
        for forbidden in ("brand.", "url_for", "current_user", "config."):
            assert forbidden not in body, (
                f"500.html references {forbidden!r}. It must render with no "
                "application or database context available."
            )


# ----------------------------------------------------------------------
# 3. Migrations
# ----------------------------------------------------------------------
class TestMigrations:
    def test_the_migrations_directory_exists(self):
        assert (ROOT / "migrations" / "env.py").exists(), (
            "Schema must come from migrations. db.create_all() creates "
            "missing tables but never alters existing ones, so a new column "
            "would pass locally and silently never appear on Render."
        )

    def test_there_is_exactly_one_head(self):
        versions = list((ROOT / "migrations" / "versions").glob("*.py"))
        assert versions, "No migration revisions found."
        revisions, down_revisions = set(), set()
        for path in versions:
            text = path.read_text()
            rev = re.search(r"^revision\s*=\s*['\"]([^'\"]+)", text, re.M)
            down = re.search(r"^down_revision\s*=\s*['\"]([^'\"]+)", text, re.M)
            if rev:
                revisions.add(rev.group(1))
            if down:
                down_revisions.add(down.group(1))
        heads = revisions - down_revisions
        assert len(heads) == 1, f"Expected one head, found {len(heads)}: {heads}"

    def test_no_migration_imports_the_app_package(self):
        """Autogenerate emits app.extensions.UTCDateTime unless env.py renders
        the impl instead, and the migration then dies with a NameError on
        deploy, before the app is even up."""
        for path in (ROOT / "migrations" / "versions").glob("*.py"):
            text = path.read_text()
            assert "app.extensions" not in text and "app.types" not in text, (
                f"{path.name} references the app package. Migrations must "
                "stand alone. See render_item in migrations/env.py."
            )

    def test_init_db_no_longer_uses_create_all(self):
        """Assert on the AST, so the docstring that explains the change does
        not itself trip the check."""
        tree = ast.parse((ROOT / "app" / "__init__.py").read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_all"
        ]
        assert not calls, (
            "init-db must run migrations so local and deployed schemas come "
            "from one source."
        )
