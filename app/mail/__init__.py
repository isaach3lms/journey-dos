"""Email. Resend over HTTPS, queued through the outbox, never sent inline."""

from app.mail.outbox import NotQueued, opt_in, opt_out, queue, send_pending
from app.mail.transport import (
    ConsoleTransport,
    MemoryTransport,
    ResendTransport,
    SendFailed,
    build_transport,
)

__all__ = [
    "queue",
    "send_pending",
    "opt_in",
    "opt_out",
    "NotQueued",
    "SendFailed",
    "build_transport",
    "ResendTransport",
    "ConsoleTransport",
    "MemoryTransport",
]
