"""How many people did something, over a window.

The counting layer behind the metrics screen and the digest email. One
definition of each number, in one place, so the screen and the email can
never disagree about what a week was.

**Nothing new is recorded for this.** Every number here is counted from a
table that was already being written: connect cards from guest rows, prayer
from check-ins and pastoral requests, baptism and volunteer from next-step
signups, moves from the stage columns on the event row. A metrics feature
that starts by adding its own tracking is a feature that reports on itself
rather than on the church.

**Half-open windows, always.** Every count is `start <= x < end`. A Sunday
that is the last day of one week and the first of the next gets counted
twice under any other rule, and the two reports that overlap are the two
nobody can reconcile.

**Counts, never names, in the totals.** The numbers come back as integers and
each one carries the URL of the screen that lists the people behind it.
Putting the people in the digest would email a list of who asked for prayer
to whoever is on the recipient list, which is the thing the prayer feature
was built to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from app.extensions import db
from app.models import (
    ContactLog,
    GuestCard,
    NextStepSignup,
    Person,
    PersonEvent,
    PrayerCheckin,
    Service,
    SupportRequest,
)
from app.models.kids import Checkin
from app.models.person_event import KIND_STAGE_CHANGE
from app.stages import STAGES, stage_label

# Each month compared against the one before it, each week against the week
# before. One period back rather than a rolling average: a church reads "last
# month" as a thing that happened, not as a trend line.
WEEK = timedelta(days=7)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    count: int
    # The same count for the period before this one. None when there is no
    # previous period to compare against, which is different from zero.
    previous: Optional[int] = None
    # Where the people behind the number are. A number nobody can click into
    # is a number nobody believes, and the first question after "eleven
    # connect cards" is always "which eleven".
    endpoint: Optional[str] = None
    args: tuple = ()

    @property
    def change(self) -> Optional[int]:
        if self.previous is None:
            return None
        return self.count - self.previous

    @property
    def is_growth(self) -> bool:
        return self.change is not None and self.change > 0

    @property
    def is_decline(self) -> bool:
        return self.change is not None and self.change < 0


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    metrics: tuple[Metric, ...]

    @property
    def total(self) -> int:
        return sum(metric.count for metric in self.metrics)


@dataclass(frozen=True)
class Window:
    """A period, and the period before it."""

    start: datetime
    end: datetime
    label: str
    kind: str  # "week" or "month"

    @property
    def previous(self) -> "Window":
        if self.kind == "week":
            return Window(self.start - WEEK, self.start, self.label, self.kind)
        # A month back from the first of the month is the first of the month
        # before, whatever its length. Subtracting thirty days would compare
        # February against most of January.
        end = self.start
        year, month = self.start.year, self.start.month - 1
        if month == 0:
            year, month = year - 1, 12
        start = self.start.replace(year=year, month=month)
        return Window(start, end, self.label, self.kind)


def _count(model, church_id: int, window: Window, column, *where) -> int:
    from sqlalchemy import func

    query = (
        db.select(func.count(model.id))
        .where(
            model.church_id == church_id,
            column >= window.start,
            # Half open. See the module docstring: any other rule counts a
            # boundary day in both of two adjacent reports.
            column < window.end,
        )
    )
    for clause in where:
        query = query.where(clause)
    return db.session.scalar(query) or 0


# ---------------------------------------------------------------------------
# Building a window
#
# On the church's clock, always. A week that ran midnight to midnight UTC
# would start at 6pm on Saturday in Missouri, so a Saturday evening connect
# card would land in next week's report and last week's would be missing it.
# ---------------------------------------------------------------------------

def _midnight(church, day: date) -> datetime:
    """Midnight on the church's clock, as a UTC instant."""
    from app.timeutil import zone_for

    return datetime.combine(day, datetime.min.time(), tzinfo=zone_for(church))


def week_window(church, *, ending: Optional[date] = None) -> Window:
    """The seven days up to and including the day given, Monday to Sunday.

    Monday to Sunday rather than the church's Sunday-start verse week,
    because this report is about the working week that produced the numbers
    and it is sent on a Monday morning about the week that has just closed.
    """
    from app.timeutil import now_local

    today = ending or now_local(church).date()
    # Back to the Monday of the week that has finished.
    monday = today - timedelta(days=today.weekday()) - WEEK
    return Window(
        start=_midnight(church, monday),
        end=_midnight(church, monday + WEEK),
        label=monday.strftime("Week of %B %-d"),
        kind="week",
    )


def month_window(church, *, ending: Optional[date] = None) -> Window:
    """The calendar month that has just finished."""
    from app.timeutil import now_local

    today = ending or now_local(church).date()
    first_of_this = today.replace(day=1)
    year, month = first_of_this.year, first_of_this.month - 1
    if month == 0:
        year, month = year - 1, 12
    start = date(year, month, 1)
    return Window(
        start=_midnight(church, start),
        end=_midnight(church, first_of_this),
        label=start.strftime("%B %Y"),
        kind="month",
    )


def current_week(church) -> Window:
    """The week in progress, for the screen rather than the email.

    Somebody opening the metrics page on a Wednesday wants to know about
    this week. The email is always about a period that has closed.
    """
    from app.timeutil import now_local

    today = now_local(church).date()
    monday = today - timedelta(days=today.weekday())
    return Window(
        start=_midnight(church, monday),
        end=_midnight(church, monday + WEEK),
        label=monday.strftime("Week of %B %-d"),
        kind="week",
    )


def current_month(church) -> Window:
    from app.timeutil import now_local

    today = now_local(church).date()
    start = today.replace(day=1)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return Window(
        start=_midnight(church, start),
        end=_midnight(church, date(year, month, 1)),
        label=start.strftime("%B %Y"),
        kind="month",
    )


# ---------------------------------------------------------------------------
# What a person did
# ---------------------------------------------------------------------------

def connect_cards(church_id: int, window: Window) -> int:
    """Connect cards filled in.

    Every card, including ones staff later discarded as duplicates. The
    question is how many people filled one in, not how many survived triage,
    and a number that moves when somebody tidies up is a number that cannot
    be compared month to month.
    """
    return _count(GuestCard, church_id, window, GuestCard.created_at)


def signups(church_id: int, window: Window, offer: str) -> int:
    """Baptism, volunteer, or whatever else the church offers.

    Counted when it was asked for, not when staff dealt with it. A sign-up
    sitting unhandled still happened, and counting handled ones would turn
    this into a measure of staff response time wearing the label of a
    measure of interest.
    """
    return _count(
        NextStepSignup, church_id, window, NextStepSignup.created_at,
        NextStepSignup.offer == offer,
    )


def prayer_requests(church_id: int, window: Window) -> int:
    """Weekly check-ins plus pastoral requests of the prayer kind.

    Both, added together, because a member does not experience them as two
    features. Somebody who wrote in the weekly box and somebody who used the
    support form both asked their church to pray.
    """
    from app.models.support import KIND_PRAYER

    checkins = _count(
        PrayerCheckin, church_id, window, PrayerCheckin.created_at
    )
    asks = _count(
        SupportRequest, church_id, window, SupportRequest.created_at,
        SupportRequest.kind == KIND_PRAYER,
    )
    return checkins + asks


def new_people(church_id: int, window: Window) -> int:
    """People added, however they arrived: a card, a kiosk, staff, an import."""
    return _count(Person, church_id, window, Person.created_at)


def kids_checkins(church_id: int, window: Window) -> int:
    return _count(Checkin, church_id, window, Checkin.checked_in_at)


def contacts_logged(church_id: int, window: Window) -> int:
    """Contacts staff wrote down.

    The one number here that measures the church rather than the congregation,
    which is why it is on the sheet. A month where everything else moved and
    this did not is a month the system did the work on its own.
    """
    return _count(ContactLog, church_id, window, ContactLog.occurred_at)


def attendance_total(church_id: int, window: Window) -> int:
    """Head counts added up across the services in the window.

    A sum rather than an average, so it reads the same way as everything else
    on the sheet. Services with no count contribute nothing rather than zero.
    """
    from sqlalchemy import func

    return db.session.scalar(
        db.select(func.coalesce(func.sum(Service.headcount), 0)).where(
            Service.church_id == church_id,
            Service.starts_at >= window.start,
            Service.starts_at < window.end,
            Service.headcount.is_not(None),
        )
    ) or 0


# ---------------------------------------------------------------------------
# Moving along the rail
# ---------------------------------------------------------------------------

def stage_moves(church_id: int, window: Window) -> dict[str, int]:
    """How many arrived at each stage, keyed by the stage code.

    Counted on `to_stage`, the column, never on the sentence in `summary`.
    See the migration that added it for why.

    Moves backwards are counted too, under the stage they went to. A church
    that moves somebody from Volunteer back to Member has recorded something
    true, and hiding it would make the totals flatter than the rail.
    """
    from sqlalchemy import func

    rows = db.session.execute(
        db.select(PersonEvent.to_stage, func.count(PersonEvent.id))
        .where(
            PersonEvent.church_id == church_id,
            PersonEvent.kind == KIND_STAGE_CHANGE,
            PersonEvent.occurred_at >= window.start,
            PersonEvent.occurred_at < window.end,
            PersonEvent.to_stage.is_not(None),
        )
        .group_by(PersonEvent.to_stage)
    )
    return {code: total for code, total in rows}


def total_moves(church_id: int, window: Window) -> int:
    return sum(stage_moves(church_id, window).values())


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------

def _metric(key, label, now, before, endpoint=None, args=()) -> Metric:
    return Metric(key=key, label=label, count=now, previous=before,
                  endpoint=endpoint, args=args)


def sections_for(church, window: Window, *, compare: bool = True) -> list[Section]:
    """Every number on the sheet, grouped the way a pastor reads them.

    Three groups and an order that is an argument: what people did first,
    because that is the church happening; then movement along the rail,
    because that is the system working; then what staff did, because that is
    the question the first two raise.
    """
    from app.content import METRICS

    previous = window.previous if compare else None

    def pair(fn, *args):
        now = fn(church.id, window, *args)
        return now, (fn(church.id, previous, *args) if previous else None)

    cards_now, cards_before = pair(connect_cards)
    prayer_now, prayer_before = pair(prayer_requests)
    baptism_now, baptism_before = pair(signups, "baptism")
    volunteer_now, volunteer_before = pair(signups, "volunteer")
    people_now, people_before = pair(new_people)
    kids_now, kids_before = pair(kids_checkins)
    seats_now, seats_before = pair(attendance_total)
    contacts_now, contacts_before = pair(contacts_logged)

    asked = Section(
        key="asked",
        label=METRICS["section_asked"],
        metrics=(
            _metric("connect_cards", METRICS["connect_cards"], cards_now,
                    cards_before, "people.guests"),
            _metric("prayer", METRICS["prayer"], prayer_now, prayer_before,
                    "people.prayer_list"),
            _metric("baptism", METRICS["baptism"], baptism_now, baptism_before,
                    "people.signups"),
            _metric("volunteer", METRICS["volunteer"], volunteer_now,
                    volunteer_before, "people.signups"),
        ),
    )

    moves_now = stage_moves(church.id, window)
    moves_before = stage_moves(church.id, previous) if previous else {}
    moved = Section(
        key="moved",
        label=METRICS["section_moved"],
        metrics=tuple(
            _metric(
                f"to_{definition.code}",
                METRICS["moved_to"].format(stage=definition.label),
                moves_now.get(definition.code, 0),
                # `.get(code, 0)`, not `.get(code)`. A stage nobody moved to
                # last week compared as None, which the screen prints as
                # "nothing to compare against yet" on a church with a year
                # of history. Zero last week is a comparison, and a true one.
                (moves_before.get(definition.code, 0) if previous else None),
                "people.index",
                (("stage", definition.code),),
            )
            for definition in STAGES
            # Nobody moves to the first stage on the rail: that is where
            # people start. A row that is structurally always nought is a
            # row that teaches the reader to skim.
            if definition.code != STAGES[0].code
        ),
    )

    church_did = Section(
        key="church",
        label=METRICS["section_church"],
        metrics=(
            _metric("new_people", METRICS["new_people"], people_now,
                    people_before, "people.index"),
            _metric("attendance", METRICS["attendance"], seats_now,
                    seats_before, "services.attendance"),
            _metric("kids", METRICS["kids"], kids_now, kids_before),
            _metric("contacts", METRICS["contacts"], contacts_now,
                    contacts_before),
        ),
    )

    return [asked, moved, church_did]
