"""The `church` table. Adding a church is a row, not a migration.

Everything that makes one tenant look and behave differently from another
lives in these columns. There is no per-tenant code, no per-tenant template,
and no per-tenant deploy.
"""

from __future__ import annotations

# SQLAlchemy evaluates the annotation inside `Mapped[...]` at class-definition
# time, so `from __future__ import annotations` does not defer it the way it
# defers ordinary function annotations. `str | None` therefore needs a Python
# that can evaluate PEP 604 unions at runtime, which means 3.10 or newer.
# `Optional[...]` resolves on every version, so the models do not depend on
# which interpreter happens to be on the machine.
from typing import Optional

import re

from sqlalchemy import String, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")


class Church(TimestampMixin, db.Model):
    __tablename__ = "church"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_church_slug"),
        UniqueConstraint("custom_domain", name="uq_church_custom_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(80))

    # Routing. `slug` resolves the platform subdomain. `custom_domain` is the
    # optional vanity host a church points at us later.
    custom_domain: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # Branding. These two columns are the entire theming surface. See
    # app/brand.py, which is the sole lever. Templates carry no colors.
    palette_key: Mapped[str] = mapped_column(
        String(40), nullable=False, default="between-sundays"
    )
    accent_hex: Mapped[Optional[str]] = mapped_column(String(7))
    logo_reversed_path: Mapped[Optional[str]] = mapped_column(String(255))

    # App store presentation, shown on the Settings screen.
    app_name: Mapped[Optional[str]] = mapped_column(String(120))
    app_domain: Mapped[Optional[str]] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Church {self.slug} {self.name!r}>"

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_slug(slug: str) -> str:
        """A slug becomes a hostname label, so it has to be a legal one."""
        slug = (slug or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise ValueError(
                f"{slug!r} is not a valid subdomain label. Use lowercase "
                f"letters, digits, and hyphens, starting and ending with a "
                f"letter or digit."
            )
        return slug

    # -- lookups ------------------------------------------------------------

    @classmethod
    def by_slug(cls, slug: str) -> "Church | None":
        if not slug:
            return None
        return db.session.scalar(
            db.select(cls).where(cls.slug == slug.lower(), cls.is_active.is_(True))
        )

    @classmethod
    def by_custom_domain(cls, host: str) -> "Church | None":
        if not host:
            return None
        return db.session.scalar(
            db.select(cls).where(
                cls.custom_domain == host.lower(), cls.is_active.is_(True)
            )
        )

    @property
    def display_app_name(self) -> str:
        return self.app_name or self.name

    @property
    def display_app_domain(self) -> str:
        return self.app_domain or self.custom_domain or f"{self.slug}.example.org"
