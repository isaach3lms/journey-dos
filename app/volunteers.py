"""Who is serving, who is carrying too much, and which way the numbers move.

This is the counting layer behind the volunteer half of the roster report. It
answers four questions a staff meeting actually asks, and every one of them
turns on a definition that has to be written down or the number means nothing:

1. **How many volunteers do we have?** Not "how many people have ever served",
   which only ever goes up and is therefore useless. The answer here is people
   who served inside a window, and the window is the last three complete
   calendar months.

2. **Who serves at least monthly?** Somebody who served three times in one
   week in March and never again is not a monthly volunteer, so this counts
   *distinct months served*, not shifts. Three out of three months is the bar.

3. **Is a small group carrying it?** Two different failures hide here. A rota
   where five people cover most of the shifts is fragile: one family holiday
   and a Sunday has no sound desk. And the people in that five are the ones
   who quietly stop coming. So this reports both the concentration across the
   team and the individuals who trip a threshold, with the reason named.

4. **Which way is it going?** Thirteen months of it, so month over month and
   the same month last year are both read off the same table.

**What counts as having served.** An assignment that was not declined, on a
service that has already happened. Lifted exactly from
`app/reports._last_served_by_person` rather than invented here, because the
"Last served" column on the roster and the volunteer pages behind it are read
on the same sheet, and two definitions of serving on one page is how a staff
meeting spends twenty minutes arguing about which number is wrong.

Being invited counts. A church that does not use the accept and decline flow
leaves every assignment sitting at "invited" forever, and a report that
counted only acceptances would tell them they have no volunteers at all.
Declining does not count, because being asked is not serving.

**Everything runs on the church's clock.** A Sunday evening service in
Missouri is Monday in UTC, and a month boundary built on UTC midnight moves
roughly one service in twelve into the wrong month.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from app.extensions import db
from app.models import Person, Service, ServiceAssignment
from app.models.service import DECLINED

# How far back "now" looks. Three complete months is long enough that a
# monthly volunteer has three chances to appear and short enough that
# somebody who stopped in the spring is not still counted as active in the
# autumn.
WINDOW_MONTHS = 3

# Months of history on the trend table. Thirteen rather than twelve so the
# same month last year is on the same page as this month, which is the only
# way to read a year over year change without doing arithmetic in your head.
TREND_MONTHS = 13

# The busiest share of the team whose load gets reported. A fifth is the
# usual shape of the complaint: "the same few people do everything".
CONCENTRATION_SHARE = 0.2

# A rota where the busiest fifth cover more than this is reported as
# concentrated. Not a failure, a fact: some churches run small teams on
# purpose. It is printed with the number beside it so a leader can disagree.
CONCENTRATION_FLAG = 0.5

# Burnout thresholds. Each is a separate reason and a person can trip more
# than one; the report names which.
BUSY_SUNDAY_SHARE = 0.75   # served this share or more of the weekends available
MANY_POSITIONS = 3         # covering this many distinct roles or more
LONG_STREAK = 4            # this many consecutive weekends with a shift


@dataclass(frozen=True)
class Shift:
    """One person on one service, already converted to the church's clock."""

    person_id: int
    when: date
    position: Optional[str]


@dataclass
class VolunteerRow:
    person_id: int
    name: str
    shifts: int
    months: int                    # distinct calendar months served in the window
    positions: tuple[str, ...]
    last_served: Optional[date]
    streak: int                    # longest run of consecutive weekends
    is_flagged: bool               # the Regular volunteer tick on their profile
    weekend_share: float           # weekends served out of weekends with a service

    @property
    def is_monthly(self) -> bool:
        """Served in every month of the window.

        Distinct months, not shifts. Three shifts in one week is one month,
        and calling that person a monthly volunteer would be wrong in the
        direction that matters: they look covered when they are not.
        """
        return self.months >= WINDOW_MONTHS

    @property
    def reasons(self) -> tuple[str, ...]:
        """Why this person is flagged as carrying a lot. Named, not scored.

        A single risk score would be easier to sort and impossible to act on.
        A leader can do something about "covering four roles"; nobody can do
        anything about "7.4".
        """
        out = []
        if self.weekend_share >= BUSY_SUNDAY_SHARE:
            out.append(f"on {round(self.weekend_share * 100)}% of weekends")
        if len(self.positions) >= MANY_POSITIONS:
            out.append(f"covering {len(self.positions)} roles")
        if self.streak >= LONG_STREAK:
            out.append(f"{self.streak} weekends in a row")
        return tuple(out)

    @property
    def at_risk(self) -> bool:
        return bool(self.reasons)


@dataclass
class MonthPoint:
    label: str
    year: int
    month: int
    volunteers: int
    shifts: int

    @property
    def per_volunteer(self) -> float:
        return (self.shifts / self.volunteers) if self.volunteers else 0.0


@dataclass
class VolunteerReport:
    window_label: str
    weekends: int                       # weekends in the window that had a service
    rows: list[VolunteerRow] = field(default_factory=list)
    months: list[MonthPoint] = field(default_factory=list)

    @property
    def active(self) -> list[VolunteerRow]:
        return self.rows

    @property
    def monthly(self) -> list[VolunteerRow]:
        return [row for row in self.rows if row.is_monthly]

    @property
    def should_be_flagged(self) -> list[VolunteerRow]:
        """Serving monthly, with the Regular volunteer tick still off.

        The actionable list. Ticking it is what moves somebody to Disciple
        under `app/stage_rules.py`, so a missing tick is not cosmetic: it is
        a person the rail believes has not taken a step they took months ago.
        """
        return [row for row in self.monthly if not row.is_flagged]

    @property
    def stale_flags(self) -> list[VolunteerRow]:
        """Ticked as a regular volunteer, no shift in the window.

        The other direction, and the one nobody goes looking for. A tick set
        in 2024 still reads as true in 2026.
        """
        return [row for row in self.rows if row.is_flagged and not row.shifts]

    @property
    def at_risk(self) -> list[VolunteerRow]:
        return sorted(
            [row for row in self.rows if row.at_risk],
            key=lambda row: (-row.shifts, row.name),
        )

    @property
    def total_shifts(self) -> int:
        return sum(row.shifts for row in self.rows)

    @property
    def busiest(self) -> list[VolunteerRow]:
        """The busiest fifth of the team, at least one person once anybody
        has served."""
        ordered = sorted(self.rows, key=lambda row: (-row.shifts, row.name))
        ordered = [row for row in ordered if row.shifts]
        if not ordered:
            return []
        take = max(1, round(len(ordered) * CONCENTRATION_SHARE))
        return ordered[:take]

    @property
    def concentration(self) -> float:
        """Share of all shifts carried by the busiest fifth."""
        total = self.total_shifts
        if not total:
            return 0.0
        return sum(row.shifts for row in self.busiest) / total

    @property
    def is_concentrated(self) -> bool:
        return self.concentration >= CONCENTRATION_FLAG

    @property
    def this_month(self) -> Optional[MonthPoint]:
        return self.months[-1] if self.months else None

    @property
    def last_month(self) -> Optional[MonthPoint]:
        return self.months[-2] if len(self.months) >= 2 else None

    @property
    def year_ago(self) -> Optional[MonthPoint]:
        """The same month last year, if the church has that much history.

        Position, not a date search: the trend list is thirteen consecutive
        months ending with this one, so the first entry is this month a year
        ago by construction.
        """
        return self.months[0] if len(self.months) >= TREND_MONTHS else None


def _month_start(day: date, back: int = 0) -> date:
    """The first of the month, optionally stepping back whole months.

    Whole months rather than multiples of thirty days. Thirty days back from
    the 1st of March is the 30th of January, and a window built that way
    compares February against most of January every single year.
    """
    year, month = day.year, day.month - back
    while month <= 0:
        year, month = year - 1, month + 12
    return date(year, month, 1)


def _weekend_of(day: date) -> date:
    """The Sunday that owns this day, so a Saturday setup and a Sunday
    service are not counted as two separate weekends of work."""
    return day + timedelta(days=(6 - day.weekday()) % 7)


def shifts_for(church, *, since: date, until: date) -> list[Shift]:
    """Every non-declined assignment on a service that has already happened.

    Converted to the church's own date here, once, so nothing downstream has
    to think about time zones again.
    """
    from app.timeutil import to_local, zone_for

    zone = zone_for(church)
    start = datetime.combine(since, datetime.min.time(), tzinfo=zone)
    end = datetime.combine(until, datetime.min.time(), tzinfo=zone)
    now = datetime.now(tz=zone)

    rows = db.session.execute(
        db.select(
            ServiceAssignment.person_id,
            Service.starts_at,
            ServiceAssignment.position_name,
        )
        .join(Service, Service.id == ServiceAssignment.service_id)
        .where(
            ServiceAssignment.church_id == church.id,
            # Declining is not serving. Being invited is, because a church
            # that does not use the accept flow leaves everything at
            # "invited" and would otherwise read as having no volunteers.
            ServiceAssignment.status != DECLINED,
            Service.starts_at >= start,
            Service.starts_at < end,
            # Next Sunday has not happened. Counting it would let somebody
            # who has not served since March read as current.
            Service.starts_at <= now,
        )
    ).all()

    return [
        Shift(
            person_id=person_id,
            when=to_local(starts_at, church).date(),
            position=(position or None),
        )
        for person_id, starts_at, position in rows
    ]


def _weekends_with_a_service(church, *, since: date, until: date) -> int:
    """How many weekends in the window had any service at all.

    The denominator for "on 80% of weekends". Using the number of weeks in
    the window instead would punish a church that takes a Sunday off at
    Christmas by making every volunteer look less committed.
    """
    from app.timeutil import to_local, zone_for

    zone = zone_for(church)
    rows = db.session.scalars(
        db.select(Service.starts_at).where(
            Service.church_id == church.id,
            Service.starts_at >= datetime.combine(since, datetime.min.time(), tzinfo=zone),
            Service.starts_at < datetime.combine(until, datetime.min.time(), tzinfo=zone),
            Service.starts_at <= datetime.now(tz=zone),
        )
    ).all()
    return len({_weekend_of(to_local(value, church).date()) for value in rows})


def _longest_streak(weekends: set[date]) -> int:
    """The longest run of consecutive weekends served.

    Consecutive weekends rather than consecutive services, so somebody on two
    services every Sunday is not credited with twice the streak.
    """
    if not weekends:
        return 0
    best = run = 1
    ordered = sorted(weekends)
    for earlier, later in zip(ordered, ordered[1:]):
        run = run + 1 if (later - earlier).days == 7 else 1
        best = max(best, run)
    return best


def trend_for(church, *, months: int = TREND_MONTHS, today: Optional[date] = None):
    """Distinct volunteers and total shifts, by calendar month.

    Ends with the month in progress, which is marked on the page rather than
    silently compared against complete months beside it.
    """
    from app.timeutil import now_local

    today = today or now_local(church).date()
    points: list[MonthPoint] = []

    for back in range(months - 1, -1, -1):
        start = _month_start(today, back)
        end = _month_start(today, back - 1) if back else _month_start(today, -1)
        rows = shifts_for(church, since=start, until=end)
        points.append(
            MonthPoint(
                label=start.strftime("%b %Y"),
                year=start.year,
                month=start.month,
                volunteers=len({shift.person_id for shift in rows}),
                shifts=len(rows),
            )
        )
    return points


def report_for(church, *, today: Optional[date] = None) -> VolunteerReport:
    """Everything the volunteer pages print, in one pass.

    One function rather than a handful the template calls separately, because
    every number on those pages has to describe the same window. A page where
    the headline count and the burnout list disagree about which months they
    cover is worse than no page.
    """
    from app.timeutil import now_local

    today = today or now_local(church).date()
    since = _month_start(today, WINDOW_MONTHS)
    until = _month_start(today, 0)

    rows = shifts_for(church, since=since, until=until)
    weekends = _weekends_with_a_service(church, since=since, until=until)

    by_person: dict[int, list[Shift]] = {}
    for shift in rows:
        by_person.setdefault(shift.person_id, []).append(shift)

    # Everybody who served, plus everybody carrying the flag. The flagged
    # people who did not serve are the stale ticks, and they only appear if
    # the query goes looking for them.
    flagged = db.session.scalars(
        db.select(Person).where(
            Person.church_id == church.id,
            Person.is_regular_volunteer.is_(True),
            Person.is_archived.is_(False),
        )
    ).all()
    people = {person.id: person for person in flagged}
    missing = [pid for pid in by_person if pid not in people]
    if missing:
        for person in db.session.scalars(
            db.select(Person).where(
                Person.church_id == church.id, Person.id.in_(missing)
            )
        ).all():
            people[person.id] = person

    out: list[VolunteerRow] = []
    for person_id, person in people.items():
        mine = by_person.get(person_id, [])
        served_weekends = {_weekend_of(shift.when) for shift in mine}
        out.append(
            VolunteerRow(
                person_id=person_id,
                name=person.full_name,
                shifts=len(mine),
                months=len({(shift.when.year, shift.when.month) for shift in mine}),
                positions=tuple(sorted({s.position for s in mine if s.position})),
                last_served=max((shift.when for shift in mine), default=None),
                streak=_longest_streak(served_weekends),
                is_flagged=bool(person.is_regular_volunteer),
                weekend_share=(len(served_weekends) / weekends) if weekends else 0.0,
            )
        )

    out.sort(key=lambda row: (-row.shifts, row.name))
    label = (
        f"{since.strftime('%B %Y')} to "
        f"{(until - timedelta(days=1)).strftime('%B %Y')}"
    )
    return VolunteerReport(
        window_label=label,
        weekends=weekends,
        rows=out,
        months=trend_for(church, today=today),
    )
