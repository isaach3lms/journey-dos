"""Boot rules: URL normalization and the production hard fail."""

import pytest

from app.config import MissingDatabaseURL, ProductionConfig, normalize_database_url


class TestNormalizeDatabaseURL:
    def test_render_scheme_gets_a_driver(self):
        assert normalize_database_url("postgres://u:p@h:5432/d") == \
            "postgresql+psycopg2://u:p@h:5432/d"

    def test_bare_postgresql_gets_a_driver(self):
        assert normalize_database_url("postgresql://u:p@h/d") == \
            "postgresql+psycopg2://u:p@h/d"

    def test_already_normalized_is_untouched(self):
        url = "postgresql+psycopg2://u:p@h/d"
        assert normalize_database_url(url) == url

    def test_sqlite_is_untouched(self):
        assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"

    def test_none_survives(self):
        assert normalize_database_url(None) is None

    def test_credentials_containing_the_scheme_are_not_mangled(self):
        url = "postgres://user:postgres://@host/db"
        assert normalize_database_url(url).startswith("postgresql+psycopg2://user:")


class TestProductionHardFail:
    def test_missing_database_url_refuses_to_boot(self):
        class FakeApp:
            config = {"SQLALCHEMY_DATABASE_URI": None, "SECRET_KEY": "real-secret"}

        with pytest.raises(MissingDatabaseURL):
            ProductionConfig.init_app(FakeApp())

    def test_default_secret_key_refuses_to_boot(self):
        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "dev-only-not-for-production",
            }

        with pytest.raises(RuntimeError):
            ProductionConfig.init_app(FakeApp())

    def test_valid_production_config_boots(self):
        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "real-secret",
            }

        ProductionConfig.init_app(FakeApp())


class TestMigrationsAreSafeOnPopulatedTables:
    """Local migrations run against an empty database. Production does not.

    A NOT NULL column added with no server default succeeds on an empty table
    and fails on one with rows, so this class of bug is invisible in
    development and fatal at deploy time. That happened once.
    """

    def test_every_not_null_column_added_to_an_existing_table_has_a_default(self):
        import re
        from pathlib import Path

        versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
        offenders = []

        for path in sorted(versions.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            # add_column means the table already exists; create_table does not.
            for match in re.finditer(
                r"add_column\(\s*sa\.Column\((.*?)\)\s*\)", source, re.S
            ):
                column = match.group(1)
                if "nullable=False" in column and "server_default" not in column:
                    offenders.append(f"{path.name}: {column[:90]}")

        assert not offenders, (
            "These add a NOT NULL column to a table that already has rows, "
            "with nothing to put in them:\n" + "\n".join(offenders)
        )
