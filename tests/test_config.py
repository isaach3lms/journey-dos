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
