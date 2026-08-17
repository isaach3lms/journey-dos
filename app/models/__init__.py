"""Model package. Import every model here so Alembic autogenerate sees them."""

from app.models.base import (  # noqa: F401
    TenantScoped,
    TimestampMixin,
    UTCDateTime,
    utcnow,
)
from app.models.church import Church  # noqa: F401
from app.models.user import ROLES, User  # noqa: F401

__all__ = [
    "Church",
    "User",
    "ROLES",
    "TenantScoped",
    "TimestampMixin",
    "UTCDateTime",
    "utcnow",
]
