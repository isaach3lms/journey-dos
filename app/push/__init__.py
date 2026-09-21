"""Push notifications: a second transport beside email, not a second system."""

from app.push.transport import (
    MAX_BODY,
    MAX_TITLE,
    MemoryPushTransport,
    NullPushTransport,
    PushFailed,
    PushMessage,
    SubscriptionGone,
    WebPushTransport,
    build_push_transport,
)
from app.push.send import notify, send_to_person

__all__ = [
    "PushMessage",
    "PushFailed",
    "SubscriptionGone",
    "WebPushTransport",
    "NullPushTransport",
    "MemoryPushTransport",
    "build_push_transport",
    "notify",
    "send_to_person",
    "MAX_TITLE",
    "MAX_BODY",
]
