"""Sundays, with the head count and who served on each.

One module behind two screens and a PDF, because both answer the same
question from different ends: the report asks "what happened on these
Sundays" and the history screen asks "is that number going up".

**Who counts as having volunteered.** Anybody on the schedule for that
service who did not decline it, which is the same rule `app/reports.py` uses
for "last served" on the roster report. Picking a different rule here would
mean the two reports could disagree about whether a person served on a given
Sunday, and the first time a pastor noticed that, they would stop trusting
both of them.

Accepted-only was the obvious alternative and is wrong for a service that has
already happened: most churches never chase the accept button, so a team of
eight would print as two.

**Only Sundays that have happened.** A service next week has a schedule and
no head count, and putting it on a chart of attendance draws a line down to
zero at the right-hand edge that looks like a collapse.

**Two queries, not two per Sunday.** A year is fifty-two services, and a loop
asking each one for its volunteers is a hundred queries behind one PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.extensions import db
from app.models import Person, Service, ServiceAssignment
from app.models.base import utcnow
from app.models.service import DECLINED

# How many Sundays the history screen shows at once. A year, near enough,
# which is the window a church compares against: this November against last
# November, not against August.
DEFAULT_WEEKS = 52

# The rolling average drawn beside the weekly numbers. Four weeks, because a
# single Sunday is weather and a holiday weekend, and a month of them is a
# trend somebody can act on.
AVERAGE_OVER = 4


@dataclass(frozen=True)
class Server:
    """One person who served, and what they were doing."""

    person_id: int
    name: str
    role: Optional[str]


@dataclass(frozen=True)
class Week:
    """One service that has happened."""

    service_id: int
    name: str
    starts_at: datetime
    headcount: Optional[int]
    servers: tuple[Server, ...]

    @property
    def served_count(self) -> int:
        return len(self.servers)


@dataclass(frozen=True)
class Point:
    """A week with the comparisons a church actually reads it against."""

    week: Week
    # Against the Sunday before. None when there is no Sunday before it, or
    # when either week has no head count recorded: a change computed against
    # a missing number is a made-up number.
    change: Optional[int]
    percent: Optional[float]
    # The four-week average ending at this week, once there are four.
    average: Optional[float]

    @property
    def is_growth(self) -> bool:
        return self.change is not None and self.change > 0

    @property
    def is_decline(self) -> bool:
        return self.change is not None and self.change < 0


def weeks_for(church_id: int, *, limit: int = DEFAULT_WEEKS,
              since=None, until=None, now=None) -> list[Week]:
    """Services that have already happened, oldest first.

    Oldest first because every reader of this list wants it that way: a
    report prints forwards and a trend is computed forwards. The screen
    reverses it once, at the end, rather than every query ordering itself
    for whichever caller it has.
    """
    now = now or utcnow()

    query = (
        db.select(Service)
        .where(Service.church_id == church_id, Service.starts_at <= now)
        .order_by(Service.starts_at.desc())
    )
    if since is not None:
        query = query.where(Service.starts_at >= since)
    if until is not None:
        query = query.where(Service.starts_at <= until)
    if limit:
        query = query.limit(limit)

    # Newest first for the limit, so "the last 52" means the most recent 52
    # rather than the oldest 52 this church ever held. Reversed below.
    services = list(db.session.scalars(query).all())
    services.reverse()
    if not services:
        return []

    by_service: dict[int, list[Server]] = {}
    rows = db.session.execute(
        db.select(
            ServiceAssignment.service_id,
            Person.id,
            Person.first_name,
            Person.last_name,
            ServiceAssignment.position_name,
        )
        .join(Person, Person.id == ServiceAssignment.person_id)
        .where(
            ServiceAssignment.church_id == church_id,
            ServiceAssignment.service_id.in_([s.id for s in services]),
            # See the module docstring: not declined, rather than accepted.
            ServiceAssignment.status != DECLINED,
        )
        .order_by(Person.last_name, Person.first_name)
    )
    for service_id, person_id, first, last, role in rows:
        name = " ".join(part for part in (first, last) if part).strip()
        by_service.setdefault(service_id, []).append(
            Server(person_id=person_id, name=name or "Unnamed", role=role)
        )

    return [
        Week(
            service_id=service.id,
            name=service.name,
            starts_at=service.starts_at,
            headcount=service.headcount,
            servers=tuple(by_service.get(service.id, ())),
        )
        for service in services
    ]


def trend(weeks: list[Week]) -> list[Point]:
    """Each week against the one before it, and against the last four.

    Both comparisons skip a week with no head count rather than treating it
    as zero. A Sunday nobody counted is a Sunday nobody counted, and a chart
    that reads it as nought prints a collapse and a recovery that never
    happened.
    """
    points: list[Point] = []
    counted: list[int] = []

    previous: Optional[int] = None
    for week in weeks:
        change = percent = None
        if week.headcount is not None and previous is not None:
            change = week.headcount - previous
            # Against a week of nought, any number is an infinite increase.
            # Churches do hold services nobody attends, usually in a snow
            # storm, and a report that printed "inf%" would be the only
            # thing anybody remembered about it.
            percent = (change / previous * 100) if previous else None

        average = None
        if week.headcount is not None:
            counted.append(week.headcount)
            if len(counted) >= AVERAGE_OVER:
                average = sum(counted[-AVERAGE_OVER:]) / AVERAGE_OVER
            previous = week.headcount

        points.append(Point(week=week, change=change, percent=percent,
                            average=average))
    return points


def summary(points: list[Point]) -> dict:
    """The handful of numbers a church reads first.

    Deliberately small. A page of statistics is a page nobody reads, and the
    questions actually being asked are "how many on average", "is it up or
    down on last month", and "what was the best Sunday".
    """
    counted = [p for p in points if p.week.headcount is not None]
    if not counted:
        return {
            "weeks": len(points), "counted": 0, "average": None,
            "latest": None, "best": None, "change": None, "percent": None,
            "recent_average": None, "earlier_average": None,
        }

    numbers = [p.week.headcount for p in counted]
    best = max(counted, key=lambda p: p.week.headcount)

    # The last four counted Sundays against the four before them. Not first
    # half against second half: a church wants to know about now, and a year
    # of growth followed by a bad month should show the bad month.
    recent = numbers[-AVERAGE_OVER:]
    earlier = numbers[-(AVERAGE_OVER * 2):-AVERAGE_OVER]
    recent_average = sum(recent) / len(recent)
    earlier_average = sum(earlier) / len(earlier) if earlier else None

    change = percent = None
    if earlier_average:
        change = recent_average - earlier_average
        percent = change / earlier_average * 100

    return {
        "weeks": len(points),
        "counted": len(counted),
        "average": sum(numbers) / len(numbers),
        "latest": counted[-1].week.headcount,
        "best": best,
        "change": change,
        "percent": percent,
        "recent_average": recent_average,
        "earlier_average": earlier_average,
    }
