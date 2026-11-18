"""The volunteer half of the roster report.

Every number on those pages is a definition, and a definition nobody wrote
down is a number two people read two ways. What these tests hold still:

- **Served** means scheduled for a service that has already happened and not
  declined. Lifted from the roster's own "Last served" column rather than
  invented, because both appear on one sheet and two definitions of serving
  on one page is how a staff meeting spends twenty minutes arguing about
  which number is wrong.

- **Being invited counts.** A church that never uses the accept and decline
  flow leaves every assignment at "invited" forever. Counting only
  acceptances would tell them they have no volunteers at all, which is the
  failure that makes a report worse than nothing.

- **At least monthly means distinct months, not shifts.** Three shifts in one
  week in March is one month. Counting it as three is wrong in the direction
  that matters: the rota looks covered when it is not.

- **Whole calendar months, on the church's clock.** Thirty days back from the
  1st of March is the 30th of January, and a Sunday evening service in
  Missouri is Monday in UTC.

- **Nothing from another church**, on a sheet that gets printed and left on a
  table.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import Church, Person, Service, ServiceAssignment, ServiceTrack
from app.models.service import ACCEPTED, DECLINED, INVITED
from app.volunteers import (
    BUSY_SUNDAY_SHARE,
    CONCENTRATION_FLAG,
    LONG_STREAK,
    MANY_POSITIONS,
    TREND_MONTHS,
    WINDOW_MONTHS,
    _longest_streak,
    _month_start,
    _weekend_of,
    report_for,
    shifts_for,
    trend_for,
)
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

# A fixed "today" so nothing here drifts with the wall clock. A Wednesday in
# November, which makes the window August, September and October 2026.
TODAY = date(2026, 11, 18)
UTC = timezone.utc


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def riverbend(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))


def person(db, church, first="Amy", last="Davis", **kw):
    row = Person(
        church_id=church.id, first_name=first, last_name=last,
        stage=kw.pop("stage", "volunteer"), **kw,
    )
    db.session.add(row)
    db.session.flush()
    return row


def served(db, church, who, when, *, status=ACCEPTED, role="Sound", at_hour=16):
    """One assignment on one service, at a fixed hour on the given date."""
    starts = datetime.combine(when, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=at_hour
    )
    service = db.session.scalars(
        db.select(Service).where(
            Service.church_id == church.id, Service.starts_at == starts
        )
    ).first()
    if service is None:
        service = Service(church_id=church.id, name="Sunday", starts_at=starts)
        db.session.add(service)
        db.session.flush()
    track = db.session.scalars(
        db.select(ServiceTrack).where(ServiceTrack.service_id == service.id)
    ).first()
    if track is None:
        track = ServiceTrack(church_id=church.id, service_id=service.id,
                             name="Main", position=1)
        db.session.add(track)
        db.session.flush()
    db.session.add(ServiceAssignment(
        church_id=church.id, service_id=service.id, track_id=track.id,
        person_id=who.id, position_name=role, status=status,
    ))
    db.session.flush()
    return service


# The three complete months before TODAY, and a Sunday in each.
AUG, SEP, OCT = date(2026, 8, 9), date(2026, 9, 13), date(2026, 10, 11)


class TestTheWindow:
    def test_it_is_the_three_complete_months_before_today(self, db, journey):
        report = report_for(journey, today=TODAY)
        assert report.window_label == "August 2026 to October 2026"

    def test_november_is_not_in_it(self, db, journey):
        """The month in progress is excluded on purpose.

        A volunteer who has not served yet this month is not lapsed, and a
        window that included a half-finished November would say they were.
        """
        who = person(db, journey)
        served(db, journey, who, date(2026, 11, 1))
        db.session.commit()
        assert report_for(journey, today=TODAY).active == []

    def test_whole_months_rather_than_ninety_days(self, db, journey):
        """Ninety days back from 1 November lands on 3 August, which would
        silently drop the first weekend of the window every time."""
        assert _month_start(date(2026, 11, 18), 3) == date(2026, 8, 1)
        assert _month_start(date(2026, 1, 15), 3) == date(2025, 10, 1)
        assert _month_start(date(2026, 3, 1), 1) == date(2026, 2, 1)

    def test_a_service_on_the_first_instant_of_the_window_counts(self, db, journey):
        who = person(db, journey)
        served(db, journey, who, date(2026, 8, 1), at_hour=6)
        db.session.commit()
        assert len(report_for(journey, today=TODAY).active) == 1

    def test_the_window_runs_on_the_church_s_clock(self, db, journey):
        """A Sunday evening service in Missouri is Monday in UTC.

        At the end of a month that moves the service into the next one. The
        service below is 31 July at 8pm local, which is 1 August 02:00 UTC: a
        window built on UTC would count it, and the church would see a July
        service listed under August.
        """
        who = person(db, journey)
        served(db, journey, who, date(2026, 8, 1), at_hour=2)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert report.active == [], (
            "a 31 July service on the church's clock was counted as August"
        )


class TestWhatCountsAsServing:
    def test_an_accepted_assignment_counts(self, db, journey):
        who = person(db, journey)
        served(db, journey, who, SEP, status=ACCEPTED)
        db.session.commit()
        assert len(report_for(journey, today=TODAY).active) == 1

    def test_an_invitation_nobody_answered_counts(self, db, journey):
        """The decision that keeps the report honest for most churches.

        A church that never uses the accept and decline flow leaves every
        assignment at "invited". Counting only acceptances would tell them
        they have no volunteers at all.
        """
        who = person(db, journey)
        served(db, journey, who, SEP, status=INVITED)
        db.session.commit()
        assert len(report_for(journey, today=TODAY).active) == 1

    def test_a_declined_assignment_does_not(self, db, journey):
        """Being asked is not serving. Counting it would show somebody as
        active on the strength of an invitation they turned down."""
        who = person(db, journey)
        served(db, journey, who, SEP, status=DECLINED)
        db.session.commit()
        assert report_for(journey, today=TODAY).active == []

    def test_a_service_still_to_come_does_not(self, db, journey):
        """Somebody rostered for next Sunday has not served since March."""
        who = person(db, journey)
        served(db, journey, who, TODAY + timedelta(days=4))
        db.session.commit()
        assert report_for(journey, today=TODAY).active == []

    def test_a_service_later_this_month_is_not_in_the_trend_either(
        self, db, journey
    ):
        """Where the future guard actually bites.

        The three month window ends on the 1st, so a date next week is
        already outside it and the test above passes with or without the
        guard. A mutation run found that. The month in progress on the trend
        table runs to the end of the month, so without the guard next
        Sunday's rota inflates this month's numbers and every month over
        month comparison made against them.
        """
        who = person(db, journey)
        served(db, journey, who, date(2026, 11, 22))   # a Sunday after TODAY
        served(db, journey, who, date(2026, 11, 8))    # and one already past
        db.session.commit()

        november = trend_for(journey, today=TODAY)[-1]
        assert november.label == "Nov 2026"
        assert november.shifts == 1, "a service still to come was counted"
        assert november.volunteers == 1

    def test_two_roles_on_one_service_are_two_shifts_and_one_weekend(
        self, db, journey
    ):
        """Shifts measure work, weekends measure commitment.

        Somebody on sound and cameras the same morning did two jobs, which
        the shift count should say. But they gave up one Sunday, not two, so
        the weekend share and the streak must not double.
        """
        who = person(db, journey)
        served(db, journey, who, SEP, role="Sound")
        served(db, journey, who, SEP, role="Cameras")
        db.session.commit()
        report = report_for(journey, today=TODAY)
        row = report.active[0]
        assert row.shifts == 2
        assert row.positions == ("Cameras", "Sound")
        assert row.streak == 1
        # And the share is weekends, not shifts. One weekend with a service,
        # one weekend given up: 100%, not 200%. A mutation run found that
        # nothing here was asserting it, and a share built on shifts would
        # push every two-role volunteer over the burnout threshold on their
        # first Sunday.
        assert report.weekends == 1
        assert row.weekend_share == 1.0


class TestServingAtLeastMonthly:
    def test_a_month_in_each_of_the_three_counts(self, db, journey):
        who = person(db, journey)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert [row.name for row in report.monthly] == ["Amy Davis"]
        assert report.active[0].months == 3

    def test_three_shifts_in_one_month_is_not_monthly(self, db, journey):
        """The decision this whole section turns on.

        Counting shifts instead of distinct months would call this person a
        monthly volunteer on the strength of one busy fortnight, and the rota
        would look covered when it is not.
        """
        who = person(db, journey)
        for day in (6, 13, 20):
            served(db, journey, who, date(2026, 9, day))
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert report.active[0].shifts == 3
        assert report.active[0].months == 1
        assert report.monthly == []

    def test_two_months_out_of_three_is_not_monthly(self, db, journey):
        who = person(db, journey)
        served(db, journey, who, AUG)
        served(db, journey, who, OCT)
        db.session.commit()
        assert report_for(journey, today=TODAY).monthly == []


class TestTheRegularVolunteerTick:
    def test_a_monthly_volunteer_with_the_box_off_is_listed(self, db, journey):
        """The actionable list, and it has teeth: ticking the box alongside
        Regular giver is what moves somebody to Disciple on the rail, so a
        missing tick is a step the system believes they never took."""
        who = person(db, journey, is_regular_volunteer=False)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert [row.name for row in report.should_be_flagged] == ["Amy Davis"]

    def test_a_monthly_volunteer_already_ticked_is_not(self, db, journey):
        who = person(db, journey, is_regular_volunteer=True)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()
        assert report_for(journey, today=TODAY).should_be_flagged == []

    def test_somebody_serving_once_is_not_suggested(self, db, journey):
        """One shift is not a regular volunteer, and a list that suggested it
        would be ignored within a month."""
        who = person(db, journey, is_regular_volunteer=False)
        served(db, journey, who, SEP)
        db.session.commit()
        assert report_for(journey, today=TODAY).should_be_flagged == []

    def test_a_tick_with_no_shifts_is_listed_as_stale(self, db, journey):
        """The direction nobody goes looking for. A tick set in 2024 still
        reads as true in 2026."""
        who = person(db, journey, first="Ford", last="Lamb",
                     is_regular_volunteer=True)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert [row.name for row in report.stale_flags] == ["Ford Lamb"]
        assert report.stale_flags[0].shifts == 0
        assert report.stale_flags[0].last_served is None

    def test_a_ticked_volunteer_who_served_is_not_stale(self, db, journey):
        who = person(db, journey, is_regular_volunteer=True)
        served(db, journey, who, SEP)
        db.session.commit()
        assert report_for(journey, today=TODAY).stale_flags == []

    def test_an_archived_person_is_not_chased(self, db, journey):
        """Archiving somebody is how a church says they have left. A report
        that kept listing them would be a list nobody could ever clear."""
        who = person(db, journey, is_regular_volunteer=True)
        who.is_archived = True
        db.session.commit()
        assert report_for(journey, today=TODAY).stale_flags == []


class TestWorkload:
    def _busy_team(self, db, journey):
        """Two people on every weekend, six on one each."""
        heavy = [person(db, journey, first=f"Heavy{i}", last="One") for i in range(2)]
        light = [person(db, journey, first=f"Light{i}", last="Two") for i in range(6)]
        weekends = [date(2026, 8, 2) + timedelta(weeks=n) for n in range(12)]
        for index, when in enumerate(weekends):
            for who in heavy:
                served(db, journey, who, when)
            served(db, journey, light[index % len(light)], when)
        db.session.commit()
        return heavy, light

    def test_a_rota_resting_on_a_few_people_is_flagged(self, db, journey):
        self._busy_team(db, journey)
        report = report_for(journey, today=TODAY)
        assert report.is_concentrated
        assert report.concentration >= CONCENTRATION_FLAG

    def test_an_evenly_spread_rota_is_not(self, db, journey):
        """The flag has to be able to say no, or it says nothing."""
        team = [person(db, journey, first=f"Even{i}", last="Share")
                for i in range(10)]
        weekends = [date(2026, 8, 2) + timedelta(weeks=n) for n in range(10)]
        for index, when in enumerate(weekends):
            for offset in range(3):
                served(db, journey, team[(index + offset) % len(team)], when)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert not report.is_concentrated, (
            f"an even rota read as concentrated at {report.concentration:.0%}"
        )

    def test_somebody_on_every_weekend_is_flagged_with_the_reason(self, db, journey):
        heavy, _ = self._busy_team(db, journey)
        report = report_for(journey, today=TODAY)
        flagged = {row.name: row for row in report.at_risk}
        assert "Heavy0 One" in flagged
        assert any("weekends" in reason for reason in flagged["Heavy0 One"].reasons)

    def test_the_reasons_are_named_rather_than_scored(self, db, journey):
        """A leader can act on "covering four roles". Nobody can act on
        "7.4"."""
        who = person(db, journey)
        for index, role in enumerate(("Sound", "Kids", "Welcome", "Coffee")):
            served(db, journey, who, AUG + timedelta(weeks=index), role=role)
        db.session.commit()
        row = report_for(journey, today=TODAY).active[0]
        assert row.at_risk
        assert any(f"covering {len(row.positions)} roles" == reason
                   for reason in row.reasons)

    def test_covering_fewer_roles_than_the_threshold_is_not_flagged(
        self, db, journey
    ):
        who = person(db, journey)
        for index in range(MANY_POSITIONS - 1):
            served(db, journey, who, AUG + timedelta(weeks=index),
                   role=f"Role{index}")
        db.session.commit()
        row = report_for(journey, today=TODAY).active[0]
        assert len(row.positions) == MANY_POSITIONS - 1
        assert not any("roles" in reason for reason in row.reasons)

    def test_a_long_run_of_weekends_is_flagged(self, db, journey):
        who = person(db, journey)
        for index in range(LONG_STREAK):
            served(db, journey, who, date(2026, 8, 2) + timedelta(weeks=index))
        db.session.commit()
        row = report_for(journey, today=TODAY).active[0]
        assert row.streak == LONG_STREAK
        assert any("in a row" in reason for reason in row.reasons)

    def test_a_gap_breaks_the_run(self, db, journey):
        who = person(db, journey)
        for week in (0, 1, 3, 4):
            served(db, journey, who, date(2026, 8, 2) + timedelta(weeks=week))
        db.session.commit()
        assert report_for(journey, today=TODAY).active[0].streak == 2

    def test_the_weekend_share_is_out_of_weekends_that_had_a_service(
        self, db, journey
    ):
        """Not out of the weeks in the window.

        A church that takes a Sunday off at Christmas would otherwise make
        every one of its volunteers look less committed than they are.
        """
        regular = person(db, journey, first="Every", last="Week")
        weekends = [date(2026, 8, 2), date(2026, 9, 6), date(2026, 10, 4)]
        for when in weekends:
            served(db, journey, regular, when)
        db.session.commit()
        report = report_for(journey, today=TODAY)
        assert report.weekends == 3
        assert report.active[0].weekend_share == 1.0
        assert report.active[0].weekend_share >= BUSY_SUNDAY_SHARE

    def test_two_services_on_one_sunday_are_one_weekend(self, db, journey):
        """The denominator counts weekends, not services.

        A church with a 9am and an 11am has two services every Sunday.
        Counting services would halve every volunteer's apparent commitment
        and switch the burnout flags off across the whole rota.
        """
        who = person(db, journey, first="Both", last="Services")
        for when in (date(2026, 8, 2), date(2026, 9, 6), date(2026, 10, 4)):
            served(db, journey, who, when, at_hour=15)
            served(db, journey, who, when, at_hour=17)
        db.session.commit()

        report = report_for(journey, today=TODAY)
        assert report.weekends == 3, "two services on one Sunday counted twice"
        assert report.active[0].shifts == 6
        assert report.active[0].weekend_share == 1.0

    def test_nobody_is_flagged_on_an_empty_rota(self, db, journey):
        report = report_for(journey, today=TODAY)
        assert report.at_risk == []
        assert report.concentration == 0.0
        assert not report.is_concentrated


class TestTheStreakHelper:
    def test_consecutive_weeks_run(self):
        assert _longest_streak({
            date(2026, 3, 1), date(2026, 3, 8), date(2026, 3, 15)
        }) == 3

    def test_a_gap_resets_it(self):
        assert _longest_streak({
            date(2026, 3, 1), date(2026, 3, 8), date(2026, 3, 29)
        }) == 2

    def test_nothing_is_nought(self):
        assert _longest_streak(set()) == 0

    def test_one_weekend_is_one(self):
        assert _longest_streak({date(2026, 3, 1)}) == 1

    def test_a_saturday_and_a_sunday_are_one_weekend(self):
        """A Saturday setup and a Sunday morning are one weekend of
        somebody's life, not two."""
        assert _weekend_of(date(2026, 3, 7)) == _weekend_of(date(2026, 3, 8))


class TestTheTrend:
    def test_it_ends_with_the_month_in_progress(self, db, journey):
        points = trend_for(journey, today=TODAY)
        assert len(points) == TREND_MONTHS
        assert points[-1].label == "Nov 2026"

    def test_it_reaches_back_a_year_and_a_month(self, db, journey):
        """Thirteen months, so this month and the same month last year are on
        the same page and nobody has to do arithmetic in their head."""
        points = trend_for(journey, today=TODAY)
        assert points[0].label == "Nov 2025"

    def test_each_month_counts_distinct_volunteers_and_total_shifts(
        self, db, journey
    ):
        one = person(db, journey, first="One", last="Person")
        two = person(db, journey, first="Two", last="People")
        served(db, journey, one, SEP, role="Sound")
        served(db, journey, one, SEP, role="Kids")
        served(db, journey, two, SEP)
        db.session.commit()
        point = next(p for p in trend_for(journey, today=TODAY)
                     if p.label == "Sep 2026")
        assert point.volunteers == 2
        assert point.shifts == 3
        assert round(point.per_volunteer, 1) == 1.5

    def test_the_year_ago_point_is_the_first_entry(self, db, journey):
        report = report_for(journey, today=TODAY)
        assert report.year_ago is report.months[0]
        assert report.this_month is report.months[-1]
        assert report.last_month is report.months[-2]

    def test_a_month_with_nobody_is_nought_not_missing(self, db, journey):
        """A gap in the table would read as missing data. A quiet month is a
        fact and belongs on the page as one."""
        points = trend_for(journey, today=TODAY)
        assert all(point.volunteers == 0 for point in points)
        assert len(points) == TREND_MONTHS

    def test_per_volunteer_on_an_empty_month_does_not_divide_by_nought(
        self, db, journey
    ):
        assert trend_for(journey, today=TODAY)[0].per_volunteer == 0.0


class TestOneChurchOnly:
    def test_another_church_s_rota_is_invisible(self, db, journey, riverbend):
        mine = person(db, journey, first="Mine", last="Here")
        theirs = person(db, riverbend, first="Theirs", last="Elsewhere")
        served(db, journey, mine, SEP)
        for when in (AUG, SEP, OCT):
            served(db, riverbend, theirs, when)
        db.session.commit()

        report = report_for(journey, today=TODAY)
        assert [row.name for row in report.active] == ["Mine Here"]
        assert report.monthly == []

        theirs_report = report_for(riverbend, today=TODAY)
        assert [row.name for row in theirs_report.active] == ["Theirs Elsewhere"]

    def test_the_weekend_count_does_not_cross_churches(self, db, riverbend, journey):
        """It is the denominator for every workload percentage. Another
        church's services in it would quietly deflate all of them."""
        theirs = person(db, riverbend)
        for week in range(8):
            served(db, riverbend, theirs, date(2026, 8, 2) + timedelta(weeks=week))
        db.session.commit()
        assert report_for(journey, today=TODAY).weekends == 0

    def test_shifts_for_is_scoped(self, db, journey, riverbend):
        """The filter that matters, asserted where it lives.

        `report_for` happens to drop another church's people anyway, because
        it looks their names up with a church-scoped query. That is defence
        in depth, not the defence: this is the one that stops another
        congregation's rota reaching a sheet left on a table.
        """
        theirs = person(db, riverbend)
        served(db, riverbend, theirs, SEP)
        db.session.commit()
        assert shifts_for(journey, since=date(2026, 8, 1),
                          until=date(2026, 11, 1)) == []
        assert len(shifts_for(riverbend, since=date(2026, 8, 1),
                              until=date(2026, 11, 1))) == 1


class TestThePdf:
    def test_the_report_route_still_works_without_volunteers(self, db, journey):
        """`volunteers` is optional, so the roster can still be rendered on
        its own by anything that does not want the extra pages."""
        from app.reports import sections_for
        from app.roster_pdf import render_roster

        pdf = render_roster(
            sections=sections_for(journey), church=journey,
            fmt=lambda value: "today",
        )
        assert pdf.startswith(b"%PDF")

    def test_the_volunteer_pages_are_added(self, db, journey):
        from app.reports import sections_for
        from app.roster_pdf import render_roster

        who = person(db, journey)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()

        plain = render_roster(sections=sections_for(journey), church=journey,
                              fmt=lambda value: "today")
        with_volunteers = render_roster(
            sections=sections_for(journey), church=journey,
            fmt=lambda value: "today",
            volunteers=report_for(journey, today=TODAY),
        )
        assert len(with_volunteers) > len(plain)

    def test_the_page_count_grows(self, db, journey):
        import io

        import pypdf

        from app.reports import sections_for
        from app.roster_pdf import render_roster

        who = person(db, journey)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()

        def pages(pdf):
            return len(pypdf.PdfReader(io.BytesIO(pdf)).pages)

        plain = pages(render_roster(sections=sections_for(journey), church=journey,
                                    fmt=lambda value: "today"))
        both = pages(render_roster(
            sections=sections_for(journey), church=journey,
            fmt=lambda value: "today",
            volunteers=report_for(journey, today=TODAY),
        ))
        assert both > plain

    def test_the_volunteer_pages_say_volunteers_in_the_heading(self, db, journey):
        """A page headed "roster" over a table of burnout flags is the kind
        of thing nobody notices until it is printed."""
        import io

        import pypdf

        from app.reports import sections_for
        from app.roster_pdf import render_roster

        who = person(db, journey)
        served(db, journey, who, SEP)
        db.session.commit()

        pdf = render_roster(
            sections=sections_for(journey), church=journey,
            fmt=lambda value: "today",
            volunteers=report_for(journey, today=TODAY),
        )
        reader = pypdf.PdfReader(io.BytesIO(pdf))
        text = "\n".join(page.extract_text() for page in reader.pages)
        # The heading itself, not merely the word somewhere on the page.
        # "volunteers" appears in the body of every one of these pages
        # ("the busiest 3 of 16 volunteers"), so a mutation run found that
        # asserting the bare word passed against a page still headed
        # "roster".
        assert f"{journey.name} volunteers" in text
        assert "Serving at least monthly" in text
        assert "Should be ticked as a regular volunteer" in text
        assert "Workload" in text
        assert "Month by month" in text

    def test_the_definitions_are_printed(self, db, journey):
        """Every number on those pages is a judgement about what counts, and
        a reader deserves to know which."""
        import io

        import pypdf

        from app.reports import sections_for
        from app.roster_pdf import render_roster

        pdf = render_roster(
            sections=sections_for(journey), church=journey,
            fmt=lambda value: "today",
            volunteers=report_for(journey, today=TODAY),
        )
        text = "\n".join(
            page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf)).pages
        )
        assert "Being invited counts; declining does not" in text
        assert "distinct months, not shifts" in text

    def test_a_role_count_survives_a_long_list(self):
        """On the one page that is about workload, "Cameras, Coffee, Kids,
        Sound, Welcome, Wo…" loses the only number a reader needs."""
        from app.roster_pdf import _roles

        many = tuple(f"Role{i}" for i in range(MANY_POSITIONS))
        assert _roles(many).startswith(f"{MANY_POSITIONS}: ")
        few = tuple(f"Role{i}" for i in range(MANY_POSITIONS - 1))
        assert not few or not _roles(few)[0].isdigit()
        assert _roles(()) == "Not recorded"

    def test_a_missing_comparison_is_blank_rather_than_nought(self):
        """A church in its first year would otherwise see a column of zeroes
        and conclude it was flat."""
        from app.roster_pdf import _delta

        assert _delta(5, None) == ""
        assert _delta(5, 5) == "no change"
        assert _delta(7, 5) == "+2"
        assert _delta(3, 5) == "-2"


class TestTheRoute:
    def test_a_leader_gets_one_document(self, db, journey, client, sign_in):
        """One button, one file. A roster and a volunteer report that arrive
        as two downloads are two documents somebody has to staple together
        before a meeting."""
        import io

        import pypdf

        who = person(db, journey)
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        response = client.get("/people/report.pdf", headers=H)
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"

        text = "\n".join(
            page.extract_text()
            for page in pypdf.PdfReader(io.BytesIO(response.data)).pages
        )
        assert "roster" in text
        assert "volunteers" in text

    def test_a_member_still_cannot(self, db, journey, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        response = client.get("/people/report.pdf", headers=H)
        assert response.status_code in (302, 403)

    def test_the_volunteer_half_ignores_the_roster_filter(
        self, db, journey, client, sign_in
    ):
        """"Who is carrying too much" is a question about the serving team.

        A copy of it filtered down to the Visitor stage would be a page of
        noise that still looked authoritative.
        """
        import io

        import pypdf

        who = person(db, journey, first="Vera", last="Teran", stage="volunteer")
        for when in (AUG, SEP, OCT):
            served(db, journey, who, when)
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        response = client.get("/people/report.pdf?stage=visitor", headers=H)
        assert response.status_code == 200
        text = "\n".join(
            page.extract_text()
            for page in pypdf.PdfReader(io.BytesIO(response.data)).pages
        )
        assert "Vera Teran" in text, (
            "the volunteer pages were filtered down with the roster"
        )
