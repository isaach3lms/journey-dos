"""Model primitives: UTC handling and slug validation."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Church, utcnow


class TestUTCDateTime:
    def test_timestamps_come_back_timezone_aware(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.created_at.tzinfo is not None
        assert church.created_at.utcoffset() == timedelta(0)

    def test_an_aware_timestamp_can_be_compared_without_raising(self, db):
        """The failure this type decorator exists to prevent.

        Naive on SQLite and aware on Postgres means this comparison raises in
        production only. It must not raise here either.
        """
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.created_at <= utcnow()

    def test_a_naive_datetime_is_stored_as_utc(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        naive = datetime(2026, 1, 1, 12, 0, 0)
        church.updated_at = naive
        db.session.commit()
        db.session.expire_all()

        reloaded = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert reloaded.updated_at.tzinfo is not None
        assert reloaded.updated_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def test_an_offset_datetime_is_converted_to_utc(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        central = timezone(timedelta(hours=-6))
        church.updated_at = datetime(2026, 1, 1, 6, 0, 0, tzinfo=central)
        db.session.commit()
        db.session.expire_all()

        reloaded = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert reloaded.updated_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def test_a_non_datetime_is_rejected_loudly(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        church.updated_at = "2026-01-01"
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


class TestSlug:
    @pytest.mark.parametrize("slug", ["journey", "the-journey", "j2", "a1-b2"])
    def test_valid_slugs(self, slug):
        assert Church.validate_slug(slug) == slug

    @pytest.mark.parametrize(
        "slug", ["", "-journey", "journey-", "the journey", "Journey.Church", "a" * 60]
    )
    def test_invalid_slugs_are_rejected(self, slug):
        with pytest.raises(ValueError):
            Church.validate_slug(slug)

    def test_case_is_normalized(self):
        assert Church.validate_slug("JOURNEY") == "journey"


class TestAddingAChurchIsARow:
    def test_a_new_church_needs_no_migration(self, db):
        before = db.session.scalars(db.select(Church)).all()
        db.session.add(Church(slug="newhope", name="New Hope", palette_key="journey"))
        db.session.commit()
        after = db.session.scalars(db.select(Church)).all()
        assert len(after) == len(before) + 1

    def test_slugs_are_unique(self, db):
        db.session.add(Church(slug="journey", name="Duplicate"))
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()
