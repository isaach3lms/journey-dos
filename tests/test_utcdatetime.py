"""Invariant 2: no naive datetime ever reaches or leaves the database."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect

from app.extensions import db as _db
from app.models.church import ChurchDomain
from app.types import UTC, UTCDateTime, utcnow


def test_utcnow_is_always_aware():
    assert utcnow().tzinfo is not None


def test_naive_datetime_is_rejected_not_assumed(app, journey):
    domain = _db.session.query(ChurchDomain).first()
    domain.verified_at = dt.datetime(2026, 8, 15, 12, 0, 0)  # naive on purpose
    with pytest.raises(Exception) as exc:
        _db.session.flush()
    assert "Naive datetime" in str(exc.value)
    _db.session.rollback()


def test_roundtrip_preserves_utc_on_sqlite(app, journey):
    """SQLite hands back naive values. The decorator must reattach UTC."""
    moment = dt.datetime(2026, 8, 15, 17, 30, tzinfo=UTC)
    domain = _db.session.query(ChurchDomain).first()
    domain_id = domain.id
    domain.verified_at = moment
    _db.session.commit()
    _db.session.expunge_all()

    reloaded = _db.session.query(ChurchDomain).filter_by(id=domain_id).one()
    assert reloaded.verified_at.tzinfo is not None
    assert reloaded.verified_at == moment


def test_non_utc_input_is_converted_not_stored_as_local(app, journey):
    central = dt.timezone(dt.timedelta(hours=-5))
    local_noon = dt.datetime(2026, 8, 15, 12, 0, tzinfo=central)
    domain = _db.session.query(ChurchDomain).first()
    domain_id = domain.id
    domain.verified_at = local_noon
    _db.session.commit()
    _db.session.expunge_all()

    reloaded = _db.session.query(ChurchDomain).filter_by(id=domain_id).one()
    assert reloaded.verified_at.hour == 17
    assert reloaded.verified_at.utcoffset() == dt.timedelta(0)


def test_every_datetime_column_uses_the_decorator(app):
    """Guardrail: a bare DateTime column added later fails this test."""
    offenders = []
    for mapper in _db.Model.registry.mappers:
        table = mapper.local_table
        if table is None:
            continue
        for column in table.columns:
            type_name = type(column.type).__name__
            if "DateTime" in type_name and not isinstance(column.type, UTCDateTime):
                offenders.append(f"{table.name}.{column.name}")
    assert offenders == [], f"Bare DateTime columns found: {offenders}"


def test_every_tenant_table_carries_church_id(app):
    """Guardrail: v1 has no global tables. Adding one must be deliberate."""
    exempt = {"church", "alembic_version"}
    missing = [
        mapper.local_table.name
        for mapper in _db.Model.registry.mappers
        if mapper.local_table is not None
        and mapper.local_table.name not in exempt
        and "church_id" not in mapper.local_table.columns
    ]
    assert missing == [], f"Tables missing church_id: {missing}"
