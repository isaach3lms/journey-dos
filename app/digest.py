"""The metrics digest: one email, on a schedule, about a period that closed.

Weekly on Monday morning about the week that just finished, monthly on the
1st about the month that just finished. Never about a period still running: a
report of a Tuesday that says "3 connect cards" invites somebody to compare
it with a full week and conclude the wrong thing.

**Counted, not tracked.** Every number comes from `app/metrics.py`, which
counts rows that were already being written. Nothing in this file records
anything.

**Numbers, never names.** The digest says eleven people asked for prayer. It
does not say who, and it never carries what they wrote. The prayer feature
was built so that those words stay on a screen behind a login, and a weekly
email that quoted them would undo that quietly and at scale. The link at the
end goes to the app, where the people behind each number are, for whoever is
allowed to look.

**Sending twice is impossible, not unlikely.** The worker tick runs often and
is allowed to run twice; retries happen. Each message carries a dedupe key of
church, period and address, and the outbox refuses the second one. That is a
property of the data rather than of the scheduler being well behaved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.content import METRICS
from app.extensions import db
from app.mail import NotQueued, queue
from app.metrics import Window, month_window, sections_for, week_window

# Its own category, not the member-facing "digest" one. The outbox log is
# read by category, and a staff report filed under the congregation's weekly
# digest is a report nobody can find. See app/categories.py.
CATEGORY = "metrics"


@dataclass(frozen=True)
class Sent:
    """What one call actually did, for the caller to report honestly."""

    window: Window
    queued: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def any_queued(self) -> bool:
        return bool(self.queued)


def dedupe_key(church, window: Window, address: str) -> str:
    """One key per church, period and recipient.

    The address is in the key on purpose. Without it the first recipient
    queues and every other one is deduped away as a repeat, which looks
    exactly like the digest working.
    """
    return f"digest:{window.kind}:{window.start.date().isoformat()}:{address}"


def body_for(church, window: Window) -> str:
    """The email, in the markup grammar the app already renders.

    Written as text with list markers rather than as HTML: `app/mail` turns
    every outgoing body into both halves, so this gets a formatted email and
    a readable plain-text one from the same source, and the formatting here
    matches everything else the church sends.
    """
    sections = sections_for(church, window)
    anything = any(section.total for section in sections)

    lines = [
        METRICS["digest_open"].format(church=church.name, label=window.label),
        "",
    ]

    def closing() -> list[str]:
        out = [METRICS["digest_close"].format(church=church.name)]
        # Built from the church's own domain rather than `url_for`, because
        # this runs from the worker with no request context and no
        # SERVER_NAME. `display_app_domain` is not used: it invents a
        # `slug.example.org` for a church still being set up, and a digest
        # with a link that does not resolve is worse than one with no link.
        domain = (church.app_domain or church.custom_domain or "").strip()
        if domain:
            out.append("")
            out.append(METRICS["digest_link"].format(
                url=f"https://{domain}/metrics/"
            ))
        return out

    if not anything:
        # Said plainly, and said as a fact rather than as a worry. A quiet
        # week at a church is usually a quiet week, and a report that
        # treated every nought as an alarm would stop being read.
        lines.append(METRICS["digest_quiet"].format(label=window.label))
        lines.append("")
        lines.extend(closing())
        return "\n".join(lines)

    versus = METRICS["vs_week"] if window.kind == "week" else METRICS["vs_month"]

    for section in sections:
        lines.append(f"# {section.label}")
        lines.append("")
        for metric in section.metrics:
            if metric.change:
                sign = "+" if metric.change > 0 else ""
                lines.append("- " + METRICS["digest_line_change"].format(
                    label=metric.label, count=metric.count,
                    change=f"{sign}{metric.change}", versus=versus,
                ))
            else:
                lines.append("- " + METRICS["digest_line"].format(
                    label=metric.label, count=metric.count,
                ))
        lines.append("")

    lines.extend(closing())
    return "\n".join(lines)


def send(church, window: Window, *, actor=None) -> Sent:
    """Queue the digest to everybody on the church's list.

    Does not commit. The caller owns the transaction, which matters for the
    worker: a tick that crashed halfway through would otherwise leave some
    recipients queued and the rest not, with nothing to say which.
    """
    recipients = church.digest_recipients
    if not recipients:
        return Sent(window=window, queued=(), skipped=())

    subject = METRICS["digest_subject"].format(
        church=church.name, label=window.label
    )
    body = body_for(church, window)

    queued, skipped = [], []
    for address in recipients:
        try:
            message = queue(
                church_id=church.id,
                category=CATEGORY,
                subject=subject,
                body_text=body,
                to_email=address,
                dedupe_key=dedupe_key(church, window, address),
                actor=actor,
            )
        except NotQueued:
            # One bad address must not cost the other recipients their
            # email, and it is not an error worth failing a scheduled job
            # over either.
            skipped.append(address)
            continue
        (queued if message is not None else skipped).append(address)

    return Sent(window=window, queued=tuple(queued), skipped=tuple(skipped))


def due(church, *, today: Optional[date] = None) -> list[Window]:
    """Which digests this church should have had by now.

    A list rather than one window, because the 1st of a month can be a
    Monday and both are then due. Returning both and letting the dedupe key
    decide is what makes the tick safe to run at any hour: nothing here asks
    "has it already gone", it just builds what is owed and the outbox
    refuses what it has already seen.
    """
    from app.timeutil import now_local

    today = today or now_local(church).date()
    windows = []
    if today.weekday() == 0:
        windows.append(week_window(church, ending=today))
    if today.day == 1:
        windows.append(month_window(church, ending=today))
    return windows


def run(*, today: Optional[date] = None, church_id: Optional[int] = None) -> list[Sent]:
    """Every church that is owed a digest today. Called by the worker tick.

    Commits once at the end rather than per church, so a crash leaves the
    whole run undone rather than half of it, and the next tick redoes it
    cleanly.
    """
    from app.models import Church

    query = db.select(Church).where(Church.is_active.is_(True))
    if church_id is not None:
        query = query.where(Church.id == church_id)

    out: list[Sent] = []
    for church in db.session.scalars(query).all():
        if not church.digest_recipients:
            continue
        for window in due(church, today=today):
            out.append(send(church, window))

    db.session.commit()
    return out
