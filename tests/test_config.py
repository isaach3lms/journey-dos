"""Invariants 1 and 3: URL normalization and the production hard fail."""

from __future__ import annotations

import pytest

from app.config import ConfigError, normalize_database_url, resolve_config


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("sqlite:///local.sqlite", "sqlite:///local.sqlite"),
        (None, None),
        ("", ""),
    ],
)
def test_render_postgres_scheme_is_normalized(raw, expected):
    assert normalize_database_url(raw) == expected


def test_production_refuses_to_boot_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("PLATFORM_DOMAIN", "dos.example.com")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        resolve_config("production")


def test_production_refuses_to_boot_without_secret_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("PLATFORM_DOMAIN", "dos.example.com")
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        resolve_config("production")


def test_production_refuses_to_boot_without_platform_domain(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)
    with pytest.raises(ConfigError, match="PLATFORM_DOMAIN"):
        resolve_config("production")


def test_production_normalizes_render_url_on_boot(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("PLATFORM_DOMAIN", "dos.example.com")
    config = resolve_config("production")
    assert config.SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg2://")


def test_unknown_app_env_is_refused():
    with pytest.raises(ConfigError, match="Unknown APP_ENV"):
        resolve_config("staging-ish")
