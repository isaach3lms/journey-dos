"""Recording an audit entry.

One function, and a guard that exists because of a specific temptation.

When you write an audit entry the natural instinct is to record *what changed*.
For a brand colour that is right. For an API key, a password, a reset token, or
a pickup code it is exactly wrong: it would take a secret out of the one column
that protects it and put it in a table designed to be read, kept, and exported.

`scrub` is a blunt second line, not the first. The first is that no call site
passes a secret. The second is that if one ever does, the value is replaced
rather than stored, and a test walks every recorded action asserting it.
"""

from __future__ import annotations

import re

from flask import g, has_request_context
from flask_login import current_user

from app.extensions import db
from app.models.audit import AuditEvent

REDACTED = "[removed]"

# Anything that looks like a key, a token, or a code. Deliberately greedy: a
# false positive costs a slightly less readable log entry, a false negative
# costs a secret in a table people export.
_SECRET_PATTERNS = (
    re.compile(r"\b(sk|pk|rk)_[A-Za-z0-9_\-]{8,}", re.I),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    re.compile(r"(?i)\b(password|secret|api[_ ]?key|token|pin|pickup)\b\s*[:=]\s*\S+"),
)


def scrub(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


def record(
    action: str,
    summary: str,
    *,
    church_id: int | None = None,
    actor=None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    subject_label: str | None = None,
    detail: str | None = None,
) -> AuditEvent | None:
    """Append one entry. The caller commits.

    Returns None rather than raising when there is no church in context. An
    audit failure must never be the reason a sign-in or a check-out fails; the
    action being recorded matters more than the record of it.
    """
    if church_id is None:
        church = g.church if has_request_context() and hasattr(g, "church") else None
        church_id = getattr(church, "id", None)
    if church_id is None:
        return None

    if actor is None and has_request_context():
        actor = current_user if getattr(current_user, "is_authenticated", False) else None

    event = AuditEvent(
        church_id=church_id,
        action=action,
        actor_user_id=getattr(actor, "id", None),
        # Copied, so the entry still names who did it after an account is gone.
        actor_name=getattr(actor, "name", None),
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=scrub(subject_label),
        summary=scrub(summary)[:255],
        detail=scrub(detail),
    )
    db.session.add(event)
    return event
