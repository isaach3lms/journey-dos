"""Is email actually leaving?

Answers the question staff ask when somebody says "I never got the link",
without a shell, a log, or a provider dashboard. Reads the outbox only. It
shows subjects and addresses, never message bodies: a body can hold a
password link, and this screen is not where that should be readable.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import STATUS_QUEUED, OutboxMessage
from app.models.base import utcnow
from app.models.outbox import STATUS_FAILED, STATUS_SENT

# The worker runs every five minutes. Anything waiting twice that long means
# it is not running, or not finishing.
STALE_AFTER = timedelta(minutes=10)

# Plain-language readings of what the provider says, most specific first.
# Matched against the lowercased error text.
_EXPLANATIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("error code: 10",), "blocked_client"),
    (("domain", "not verified"), "domain_unverified"),
    (("verify a domain",), "domain_unverified"),
    (("only send testing emails",), "test_mode"),
    (("api key is invalid",), "bad_key"),
    (("invalid api key",), "bad_key"),
    (("http 401",), "bad_key"),
    (("restricted_api_key",), "restricted_key"),
    (("restricted",), "restricted_key"),
    (("invalid `from`",), "bad_from"),
    (("from field",), "bad_from"),
    (("rate limit",), "rate_limited"),
    (("http 429",), "rate_limited"),
    (("network error",), "network"),
    (("timed out",), "network"),
)


def explain(error: str | None) -> str | None:
    """A key into SETTINGS email_why_*, or None when we cannot tell."""
    if not error:
        return None
    text = error.lower()
    for needles, key in _EXPLANATIONS:
        if all(n in text for n in needles):
            return key
    return None


def transport_summary(config=None) -> dict:
    config = config or current_app.config
    name = (config.get("MAIL_TRANSPORT") or "console").lower()
    return {
        "transport": name,
        "real": name == "resend",
        "from_address": config.get("MAIL_FROM", ""),
        # Whether it is set, never what it is.
        "key_present": bool(config.get("RESEND_API_KEY")),
    }


def email_health(church_id: int, now=None) -> dict:
    now = now or utcnow()
    counts = dict(
        db.session.execute(
            db.select(OutboxMessage.status, func.count(OutboxMessage.id))
            .where(OutboxMessage.church_id == church_id)
            .group_by(OutboxMessage.status)
        ).all()
    )

    oldest_queued = db.session.scalar(
        db.select(func.min(OutboxMessage.queued_at)).where(
            OutboxMessage.church_id == church_id,
            OutboxMessage.status == STATUS_QUEUED,
        )
    )
    last_sent = db.session.scalar(
        db.select(func.max(OutboxMessage.sent_at)).where(
            OutboxMessage.church_id == church_id,
            OutboxMessage.status == STATUS_SENT,
        )
    )

    problems = db.session.scalars(
        db.select(OutboxMessage)
        .where(
            OutboxMessage.church_id == church_id,
            OutboxMessage.status.in_((STATUS_QUEUED, STATUS_FAILED)),
            OutboxMessage.last_error.is_not(None),
        )
        .order_by(OutboxMessage.updated_at.desc(), OutboxMessage.id.desc())
        .limit(5)
    ).all()

    waiting_minutes = None
    stale = False
    if oldest_queued is not None:
        age = now - oldest_queued
        waiting_minutes = max(0, int(age.total_seconds() // 60))
        stale = age > STALE_AFTER

    latest_reason = explain(problems[0].last_error) if problems else None

    return {
        "sent": counts.get(STATUS_SENT, 0),
        "queued": counts.get(STATUS_QUEUED, 0),
        "failed": counts.get(STATUS_FAILED, 0),
        "last_sent": last_sent,
        "waiting_minutes": waiting_minutes,
        "stale": stale,
        "problems": [
            {
                "to_email": m.to_email,
                "subject": m.subject,
                "status": m.status,
                "attempts": m.attempts,
                "error": (m.last_error or "")[:300],
                "why": explain(m.last_error),
                "when": m.updated_at or m.queued_at,
            }
            for m in problems
        ],
        "latest_reason": latest_reason,
        **transport_summary(),
    }
