"""The roster report: who is here, and when did anything last happen to them.

A pastor's question is rarely "what is this person's stage". It is "who have
we lost track of", and the honest answer needs four dates side by side: when
a child of theirs was last in kids, when they last served, when somebody on
staff last wrote anything down, and which family they belong to so the row
reads as a household rather than a name.

Those four live in four places, which is why this file exists. On a screen
you can click into a profile; on a printed sheet taken into a staff meeting
you cannot, so the sheet has to carry them.

**Four queries, not four per person.** A loop that asks each person for their
last contact is 127 queries on Journey today and 2,000 on a church four times
the size, over a request that returns a PDF. Each of the three dates is one
grouped aggregate, joined on at the end.

**Why the dates are what they are.** Each one is a decision, and a different
reasonable person would make a different one, so each is written down next to
the query that implements it rather than left for somebody to infer from a
column heading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.extensions import db
from app.models import (
    Checkin,
    ContactLog,
    Household,
    Person,
    Service,
    ServiceAssignment,
)
from app.models.base import utcnow
from app.models.service import DECLINED
from app.stages import STAGE_BY_CODE, stages_for


@dataclass(frozen=True)
class Row:
    """One person, as the report prints them."""

    person_id: int
    name: str
    stage: str
    is_child: bool
    household: Optional[str]
    last_kid_checkin: Optional[datetime]
    last_served: Optional[datetime]
    last_contact: Optional[datetime]

    @property
    def in_household(self) -> bool:
        return bool(self.household)


@dataclass(frozen=True)
class Section:
    """A heading and the people under it."""

    key: str
    label: str
    rows: tuple[Row, ...]

    @property
    def count(self) -> int:
        return len(self.rows)


def _last_contact_by_person(church_id: int):
    """When somebody on staff last wrote anything down about them.

    Every kind of contact counts. A text message logged is a church that
    knows where somebody is, which is the question the column answers, and
    splitting it by method would turn one column into five.

    **What the church filter is for.** Not correctness: this is joined on
    `person_id` against a person list already scoped to one church, so
    another church's rows cannot reach a row on this sheet even without
    it, and a test cannot tell the difference. It is here because the
    aggregate runs before the join, and without it every church on the
    platform is grouped on every request. One church today, so it costs
    nothing to measure and everything to retrofit.
    """
    return (
        db.select(
            ContactLog.person_id.label("person_id"),
            db.func.max(ContactLog.occurred_at).label("at"),
        )
        .where(ContactLog.church_id == church_id)
        .group_by(ContactLog.person_id)
        .subquery()
    )


def _last_served_by_person(church_id: int):
    """The last service they were on the schedule for and did not decline.

    Two decisions here.

    Declined assignments are left out. Being asked is not serving, and a
    report that counted it would show somebody as active on the strength of
    an invitation they turned down, which is the opposite of true.

    Services in the future are left out. Somebody rostered for next Sunday
    has not served since March, and printing next Sunday's date in a column
    headed "last served" is how a person nobody has seen in eight months
    reads as current.
    """
    return (
        db.select(
            ServiceAssignment.person_id.label("person_id"),
            db.func.max(Service.starts_at).label("at"),
        )
        .join(Service, Service.id == ServiceAssignment.service_id)
        .where(
            ServiceAssignment.church_id == church_id,
            ServiceAssignment.status != DECLINED,
            Service.starts_at <= utcnow(),
        )
        .group_by(ServiceAssignment.person_id)
        .subquery()
    )


def _last_checkin_by_household(church_id: int):
    """When a child of this household was last checked into kids.

    Keyed on the household, not the person, because that is the question:
    an adult has no check-ins of their own and the column is about whether
    this family is still bringing their children on a Sunday.

    `household_id`, not the name stored on the check-in row. The name is a
    copy kept so an old check-in still reads correctly after a family is
    renamed, and matching on it would attach one family's history to
    another family that happens to share a surname. Matching on the id is
    also what makes the church filter below untestable, and matching on
    the name is what would make it load-bearing: see
    `_last_contact_by_person` for the reason it stays either way.
    """
    return (
        db.select(
            Checkin.household_id.label("household_id"),
            db.func.max(Checkin.checked_in_at).label("at"),
        )
        .where(
            Checkin.church_id == church_id,
            Checkin.household_id.is_not(None),
        )
        .group_by(Checkin.household_id)
        .subquery()
    )


def rows_for(church_id: int, *, stage=None, children=None, group=None) -> list[Row]:
    """The report's rows, filtered the same way the roster screen filters.

    The filter arguments are `Person.search`'s, deliberately: a report that
    disagreed with the list somebody is looking at would be worse than no
    report. The one difference is ordering, which is by stage then name
    here so the printed sheet groups.
    """
    contacts = _last_contact_by_person(church_id)
    served = _last_served_by_person(church_id)
    checkins = _last_checkin_by_household(church_id)

    query = (
        db.select(
            Person.id,
            Person.first_name,
            Person.last_name,
            Person.stage,
            Person.is_child,
            Household.name,
            checkins.c.at,
            served.c.at,
            contacts.c.at,
        )
        .select_from(Person)
        .outerjoin(Household, Household.id == Person.household_id)
        .outerjoin(checkins, checkins.c.household_id == Person.household_id)
        .outerjoin(served, served.c.person_id == Person.id)
        .outerjoin(contacts, contacts.c.person_id == Person.id)
        .where(
            Person.church_id == church_id,
            Person.is_archived.is_(False),
        )
        .order_by(Person.last_name, Person.first_name)
    )

    if stage:
        query = query.where(Person.stage == stage)
    if children is not None:
        query = query.where(Person.is_child.is_(children))
    if group:
        # `Person.in_group` is the SQL form of the `age_group` property,
        # which is Python and cannot be put in a WHERE clause. Using the
        # same helper the roster screen uses is what keeps the report and
        # the list agreeing about who is a kid.
        query = query.where(Person.in_group(group))

    out = []
    for (pid, first, last, person_stage, is_child,
         household, kid_at, served_at, contact_at) in db.session.execute(query):
        name = " ".join(part for part in (first, last) if part).strip()
        out.append(Row(
            person_id=pid,
            name=name or "Unnamed",
            stage=person_stage,
            is_child=bool(is_child),
            household=household,
            last_kid_checkin=kid_at,
            last_served=served_at,
            last_contact=contact_at,
        ))
    return out


def sections_for(church, *, stage=None, children=None, group=None,
                 group_label=None) -> list[Section]:
    """The rows, split into the headings the sheet prints under.

    Filtered to one thing, the sheet is one section: splitting a single
    stage by stage produces one heading with everything under it and a
    reader wondering what the heading is for.

    Unfiltered, it is one section per stage in the church's own order, and
    anybody whose stage is no longer in that list lands in a final section
    rather than disappearing. A person is never dropped from this report
    because of a stage nobody uses any more.
    """
    rows = rows_for(church.id, stage=stage, children=children, group=group)

    if stage or group:
        label = group_label or (
            STAGE_BY_CODE[stage].label if stage in STAGE_BY_CODE else "Everyone"
        )
        return [Section(key=stage or group, label=label, rows=tuple(rows))]

    by_stage: dict[str, list[Row]] = {}
    for row in rows:
        by_stage.setdefault(row.stage, []).append(row)

    sections = []
    for definition in stages_for(church):
        if definition.code in by_stage:
            sections.append(Section(
                key=definition.code,
                label=definition.label,
                rows=tuple(by_stage.pop(definition.code)),
            ))

    # Whatever is left belongs to a stage this church no longer lists.
    for code, leftover in sorted(by_stage.items()):
        sections.append(Section(key=code, label=code.title(), rows=tuple(leftover)))

    return sections
