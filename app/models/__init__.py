"""Model package. Import every model here so Alembic autogenerate sees them."""

from app.models.base import (  # noqa: F401
    TenantScoped,
    TimestampMixin,
    UTCDateTime,
    utcnow,
)
from app.models.church import Church  # noqa: F401
from app.models.contact import (  # noqa: F401
    CONTACT_METHODS,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_OPEN,
    ContactLog,
    NextStep,
)
from app.models.outbox import (  # noqa: F401
    MAX_ATTEMPTS,
    OUTBOX_STATUSES,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENT,
    STATUS_SUPPRESSED,
    NotificationPreference,
    OutboxMessage,
)
from app.models.person import Household, Person  # noqa: F401
from app.models.person_event import (  # noqa: F401
    EVENT_KINDS,
    KIND_CONTACT,
    KIND_CREATED,
    KIND_EMAIL,
    KIND_IMPORTED,
    KIND_NEXT_STEP,
    KIND_NOTE,
    KIND_STAGE_CHANGE,
    PersonEvent,
)
from app.models.user import ROLES, User  # noqa: F401

__all__ = [
    "Church",
    "Household",
    "Person",
    "PersonEvent",
    "ContactLog",
    "NextStep",
    "OutboxMessage",
    "NotificationPreference",
    "STATUS_QUEUED",
    "STATUS_SENT",
    "STATUS_FAILED",
    "STATUS_SUPPRESSED",
    "STATUS_CANCELLED",
    "MAX_ATTEMPTS",
    "CONTACT_METHODS",
    "STATUS_OPEN",
    "STATUS_DONE",
    "STATUS_DROPPED",
    "EVENT_KINDS",
    "KIND_CREATED",
    "KIND_IMPORTED",
    "KIND_NOTE",
    "KIND_STAGE_CHANGE",
    "KIND_CONTACT",
    "KIND_EMAIL",
    "KIND_NEXT_STEP",
    "User",
    "ROLES",
    "TenantScoped",
    "TimestampMixin",
    "UTCDateTime",
    "utcnow",
]
