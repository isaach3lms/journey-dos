"""Everything the staff dashboard shows, computed in one place.

The layout follows the approved client demo: the Journey rail, four headline
tiles with seven weeks of history, the people who need a human, a health
score, and what ran on its own. Every number here comes from a real table.
Where a church has not recorded something yet (a headcount, a gift), the tile
says so instead of showing a zero that reads like a bad week.

Weeks are rolling seven day windows ending today, oldest first, so the last
bar is always "this week" and nothing depends on which day the page is opened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models.base import utcnow
from app.stages import STAGES, TRANSITIONAL_STAGES

WEEKS = 7
HEALTH_WINDOW_DAYS = 90
GUEST_CONTACT_HOURS = 48

DESTINATION_CODES = tuple(s.code for s in STAGES if not s.is_transitional)


@dataclass
class Tile:
    key: str
    value: str | None           # None means "nothing recorded yet"
    trend: tuple | None = None  # (direction, amount), worded by the template
    direction: str = "flat"     # up, down, flat: colours the trend line
    bars: list[int] = field(default_factory=list)
    link: str | None = None     # where the empty state points


@dataclass
class HealthLine:
    key: str
    percent: int
    numerator: int
    denominator: int


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def next_sunday(today: date) -> tuple[date, int]:
    """The coming Sunday and how many days away it is. Today counts."""
    days = (6 - today.weekday()) % 7
    return today + timedelta(days=days), days


def week_starts(today: date, weeks: int = WEEKS) -> list[date]:
    """Start dates of each rolling week, oldest first. The last one ends today."""
    end = today + timedelta(days=1)
    return [end - timedelta(days=7 * (weeks - i)) for i in range(weeks)]


def _bucket(values: list[tuple[date, int]], starts: list[date]) -> list[int]:
    totals = [0] * len(starts)
    for when, amount in values:
        for i, start in enumerate(starts):
            if start <= when < start + timedelta(days=7):
                totals[i] += amount
                break
    return totals


def _week_delta(bars: list[int]) -> tuple[int, str]:
    if len(bars) < 2:
        return 0, "flat"
    change = bars[-1] - bars[-2]
    return change, "up" if change > 0 else "down" if change < 0 else "flat"


# ---------------------------------------------------------------------------
# The Journey rail
# ---------------------------------------------------------------------------

def stuck_by_stage(church_id: int) -> dict[str, int]:
    from app.models import Person

    rows = db.session.execute(
        db.select(Person.stage, func.count(Person.id))
        .where(
            Person.church_id == church_id,
            Person.is_archived.is_(False),
            Person._stuck_clause(),
        )
        .group_by(Person.stage)
    ).all()
    return {stage: count for stage, count in rows}


def next_steps_done_since(church_id: int, days: int) -> int:
    from app.models.contact import STATUS_DONE, NextStep

    return db.session.scalar(
        db.select(func.count(NextStep.id)).where(
            NextStep.church_id == church_id,
            NextStep.status == STATUS_DONE,
            NextStep.completed_at >= utcnow() - timedelta(days=days),
        )
    ) or 0


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def attendance_tile(church, starts: list[date]) -> Tile:
    from app.models.service import Service
    from app.timeutil import to_local

    since = datetime.combine(starts[0], datetime.min.time())
    rows = db.session.scalars(
        db.select(Service).where(
            Service.church_id == church.id,
            Service.starts_at >= since - timedelta(days=1),
            Service.starts_at <= utcnow(),
            Service.headcount.is_not(None),
        )
    ).all()
    values = [(to_local(s.starts_at, church).date(), s.headcount) for s in rows]
    bars = _bucket(values, starts)

    if not any(bars):
        # Point the empty tile at the service that needs the number.
        last = db.session.scalar(
            db.select(Service.id)
            .where(Service.church_id == church.id, Service.starts_at <= utcnow())
            .order_by(Service.starts_at.desc())
            .limit(1)
        )
        return Tile("attendance", None, bars=bars, link=last)

    # The most recent week that has a number, so a Monday before anyone has
    # typed Sunday's count does not read as zero.
    latest_index = max(i for i, v in enumerate(bars) if v)
    latest = bars[latest_index]
    earlier = [v for v in bars[max(0, latest_index - 4):latest_index] if v]
    tile = Tile("attendance", f"{latest:,}", bars=bars)
    if earlier:
        baseline = sum(earlier) / len(earlier)
        pct = round((latest - baseline) / baseline * 100)
        tile.direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
        tile.trend = (tile.direction, abs(pct))
    return tile


def guests_tile(church_id: int, starts: list[date]) -> Tile:
    from app.models import Person

    rows = db.session.execute(
        db.select(Person.first_seen_on, func.count(Person.id))
        .where(
            Person.church_id == church_id,
            Person.first_seen_on >= starts[0],
        )
        .group_by(Person.first_seen_on)
    ).all()
    bars = _bucket([(d, c) for d, c in rows if d], starts)
    change, direction = _week_delta(bars)
    tile = Tile("guests", f"{bars[-1]:,}", bars=bars, direction=direction)
    tile.trend = (direction, abs(change))
    return tile


def next_steps_tile(church_id: int, starts: list[date]) -> Tile:
    from app.models.contact import STATUS_DONE, NextStep

    since = datetime.combine(starts[0], datetime.min.time())
    rows = db.session.scalars(
        db.select(NextStep.completed_at).where(
            NextStep.church_id == church_id,
            NextStep.status == STATUS_DONE,
            NextStep.completed_at >= since,
        )
    ).all()
    bars = _bucket([(when.date(), 1) for when in rows if when], starts)
    change, direction = _week_delta(bars)
    tile = Tile("next_steps", f"{bars[-1]:,}", bars=bars, direction=direction)
    tile.trend = (direction, abs(change))
    return tile


def giving_tile(church_id: int, today: date, starts: list[date]) -> Tile:
    from app.models.giving_mirror import ExternalGift

    has_any = db.session.scalar(
        db.select(func.count(ExternalGift.id)).where(ExternalGift.church_id == church_id)
    )
    if not has_any:
        return Tile("giving", None, bars=[0] * len(starts))

    def total(start: date, end: date) -> int:
        return int(db.session.scalar(
            db.select(func.coalesce(func.sum(ExternalGift.amount_cents), 0)).where(
                ExternalGift.church_id == church_id,
                ExternalGift.received_on >= start,
                ExternalGift.received_on <= end,
            )
        ) or 0)

    month_start = today.replace(day=1)
    mtd = total(month_start, today)

    # Same number of days into last month, so the 3rd is compared with the
    # 3rd and not with a whole month.
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    same_day = min(today.day, last_month_end.day)
    last_mtd = total(last_month_start, last_month_start.replace(day=same_day))

    rows = db.session.execute(
        db.select(ExternalGift.received_on, func.sum(ExternalGift.amount_cents))
        .where(ExternalGift.church_id == church_id, ExternalGift.received_on >= starts[0])
        .group_by(ExternalGift.received_on)
    ).all()
    bars = _bucket([(d, int(c or 0)) for d, c in rows], starts)

    tile = Tile("giving", f"${mtd / 100:,.0f}", bars=bars)
    if last_mtd:
        pct = round((mtd - last_mtd) / last_mtd * 100)
        tile.direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
        tile.trend = (tile.direction, abs(pct))
    return tile


# ---------------------------------------------------------------------------
# Church health
# ---------------------------------------------------------------------------

def _pct(numerator: int, denominator: int) -> int:
    return round(numerator / denominator * 100) if denominator else 0


def health_lines(church_id: int) -> list[HealthLine]:
    from app.models import ContactLog, Person
    from app.models.contact import STATUS_DONE, NextStep
    from app.models.group import Group, GroupMembership
    from app.models.service import Team, TeamMembership

    active = db.select(Person.id).where(
        Person.church_id == church_id, Person.is_archived.is_(False)
    )
    total = db.session.scalar(db.select(func.count()).select_from(active.subquery())) or 0
    since = utcnow() - timedelta(days=HEALTH_WINDOW_DAYS)

    # Moving: changed stage, or finished a next step, in the last 90 days.
    moved = set(db.session.scalars(active.where(Person.stage_since >= since)))
    moved |= set(db.session.scalars(
        db.select(NextStep.person_id).where(
            NextStep.church_id == church_id,
            NextStep.status == STATUS_DONE,
            NextStep.completed_at >= since,
        )
    ))
    moved &= set(db.session.scalars(active))

    # Members in a group: of the people at Member or beyond.
    members = set(db.session.scalars(active.where(Person.stage.in_(DESTINATION_CODES))))
    grouped = set(db.session.scalars(
        db.select(GroupMembership.person_id)
        .join(Group, Group.id == GroupMembership.group_id)
        .where(Group.church_id == church_id, Group.is_active.is_(True))
    ))

    # Adults serving: on an active team, of everyone who is not a child.
    adults = set(db.session.scalars(active.where(Person.is_child.is_(False))))
    serving = set(db.session.scalars(
        db.select(TeamMembership.person_id)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(Team.church_id == church_id, Team.is_active.is_(True))
    ))

    # Guests contacted within 48 hours of their first visit, over the window.
    newcomers = db.session.execute(
        db.select(Person.id, Person.first_seen_on).where(
            Person.church_id == church_id,
            Person.is_archived.is_(False),
            Person.first_seen_on >= since.date(),
        )
    ).all()
    first_contact = dict(db.session.execute(
        db.select(ContactLog.person_id, func.min(ContactLog.occurred_at))
        .where(ContactLog.church_id == church_id, ContactLog.occurred_at >= since - timedelta(days=1))
        .group_by(ContactLog.person_id)
    ).all())
    reached = 0
    for person_id, first_seen in newcomers:
        contacted = first_contact.get(person_id)
        if contacted is None or first_seen is None:
            continue
        deadline = datetime.combine(first_seen, datetime.max.time()) + timedelta(hours=GUEST_CONTACT_HOURS)
        if contacted.replace(tzinfo=None) <= deadline:
            reached += 1

    return [
        HealthLine("next_step", _pct(len(moved), total), len(moved), total),
        HealthLine("grouped", _pct(len(members & grouped), len(members)), len(members & grouped), len(members)),
        HealthLine("serving", _pct(len(adults & serving), len(adults)), len(adults & serving), len(adults)),
        HealthLine("guests_48h", _pct(reached, len(newcomers)), reached, len(newcomers)),
    ]


def health_score(lines: list[HealthLine]) -> int | None:
    counted = [line.percent for line in lines if line.denominator]
    return round(sum(counted) / len(counted)) if counted else None


def weakest(lines: list[HealthLine]) -> HealthLine | None:
    counted = [line for line in lines if line.denominator]
    return min(counted, key=lambda line: line.percent) if counted else None


# ---------------------------------------------------------------------------
# Running without staff time
# ---------------------------------------------------------------------------

def automation_rows(church_id: int, days: int = 7) -> list[dict]:
    """Each sequence with how many of its emails left this week.

    Counted from the outbox, where every sequence step is queued with a
    `sequence:<enrollment>:step:<n>` key, so the number is emails, not people.
    """
    from app.models import OutboxMessage, SequenceEnrollment
    from app.sequences import sequences_for

    keys = db.session.scalars(
        db.select(OutboxMessage.dedupe_key).where(
            OutboxMessage.church_id == church_id,
            OutboxMessage.dedupe_key.like("sequence:%"),
            OutboxMessage.status.in_(("sent", "queued")),
            OutboxMessage.queued_at >= utcnow() - timedelta(days=days),
        )
    ).all()
    enrollment_ids = []
    for key in keys:
        try:
            enrollment_ids.append(int(key.split(":")[1]))
        except (IndexError, ValueError):
            continue

    code_for = dict(db.session.execute(
        db.select(SequenceEnrollment.id, SequenceEnrollment.sequence_code).where(
            SequenceEnrollment.church_id == church_id,
            SequenceEnrollment.id.in_(set(enrollment_ids) or {-1}),
        )
    ).all())
    sent: dict[str, int] = {}
    for enrollment_id in enrollment_ids:
        code = code_for.get(enrollment_id)
        if code:
            sent[code] = sent.get(code, 0) + 1

    running = dict(db.session.execute(
        db.select(SequenceEnrollment.sequence_code, func.count(SequenceEnrollment.id))
        .where(SequenceEnrollment.church_id == church_id, SequenceEnrollment.status == "active")
        .group_by(SequenceEnrollment.sequence_code)
    ).all())

    from app.stages import STAGE_BY_CODE

    rows = []
    for sequence in sequences_for():
        stage = STAGE_BY_CODE.get(sequence.trigger_stage)
        rows.append({
            "name": sequence.name,
            "trigger": stage.label if stage else sequence.trigger_stage,
            "steps": len(sequence.steps),
            "days": sequence.length_days,
            "sent": sent.get(sequence.code, 0),
            "running": running.get(sequence.code, 0),
        })
    return rows


# ---------------------------------------------------------------------------
# Everything
# ---------------------------------------------------------------------------

def build(church) -> dict:
    from app.models import Person
    from app.stages import stages_for
    from app.timeutil import now_local

    today = now_local(church).date()
    starts = week_starts(today)
    sunday, days_out = next_sunday(today)

    counts = Person.stage_counts(church.id)
    stuck = stuck_by_stage(church.id)
    lines = health_lines(church.id)
    automation = automation_rows(church.id)

    return {
        "today": today,
        "sunday": sunday,
        "days_out": days_out,
        "stages": stages_for(church),
        "counts": counts,
        "stuck_by_stage": stuck,
        "transitional": {s.code for s in TRANSITIONAL_STAGES},
        "total": Person.total_for_church(church.id),
        "stuck_count": sum(stuck.values()),
        "steps_7d": next_steps_done_since(church.id, 7),
        "unowned_count": Person.unowned_count(church.id),
        "tiles": [
            attendance_tile(church, starts),
            guests_tile(church.id, starts),
            next_steps_tile(church.id, starts),
            giving_tile(church.id, today, starts),
        ],
        "health": lines,
        "health_score": health_score(lines),
        "weakest": weakest(lines),
        "automation_rows": automation,
        "automation_sent": sum(row["sent"] for row in automation),
    }
