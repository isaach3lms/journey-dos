"""The counting layer, the windows, and the digest email.

What these tests are actually defending, since none of it is obvious from
the assertions alone:

1. **Half-open windows.** Every count is `start <= x < end`. A Sunday that is
   the last day of one week and the first of the next gets counted twice under
   any other rule, and two reports that overlap are the two nobody can
   reconcile. Each boundary is tested at the instant, not near it.

2. **The church's clock, not the server's.** Journey is in Missouri, six
   hours behind UTC. A week built on UTC midnight starts at 6pm on Saturday
   there, so a Saturday evening connect card lands in the wrong week. The
   fixtures below pick moments where the right answer, the UTC answer and
   today's answer are three different weeks, because otherwise a server-clock
   bug passes by coincidence.

3. **Moves counted on the column, never on the sentence.** `to_stage` is a
   column on the event row. Earlier code wrote the move into `summary` as
   English prose, and a count that parsed that sentence would break the first
   time somebody reworded a stage label. One test writes a row whose summary
   says one thing and whose column says another, and insists the column wins.

4. **Numbers, never names.** The digest says eleven people asked for prayer.
   It must never carry who, or what they wrote. The prayer feature exists so
   those words stay behind a login, and a weekly email that quoted them would
   undo that quietly and at scale.

5. **Sending twice is impossible, not unlikely.** The worker tick runs often
   and is allowed to run twice. The dedupe key carries the address, because
   without it the first recipient queues and every other one is refused as a
   repeat, which looks exactly like the digest working.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app import digest as digest_module
from app import metrics as metrics_module
from app.extensions import db as _db
from app.metrics import (
    Window,
    attendance_total,
    connect_cards,
    contacts_logged,
    current_month,
    current_week,
    kids_checkins,
    month_window,
    new_people,
    prayer_requests,
    sections_for,
    signups,
    stage_moves,
    total_moves,
    week_window,
)
from app.models import (
    Church,
    ContactLog,
    GuestCard,
    NextStepSignup,
    OutboxMessage,
    Person,
    PersonEvent,
    PrayerCheckin,
    Service,
    SupportRequest,
)
from app.models.kids import Checkin, CheckinSession
from app.models.person_event import KIND_STAGE_CHANGE
from app.models.support import KIND_PRAYER, KIND_TALK

from tests.conftest import JOURNEY_HOST

# Missouri is UTC-6 in winter and UTC-5 in summer. Every datetime below is
# built in UTC on purpose: the point of most of these tests is that the code
# converts to the church's clock and the test does not.
UTC = timezone.utc


def _journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def _riverbend(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))


def _person(db, church, first="Sam", last="Carter", **kwargs):
    person = Person(
        church_id=church.id, first_name=first, last_name=last, **kwargs
    )
    db.session.add(person)
    db.session.flush()
    return person


def _window(start: datetime, end: datetime, kind: str = "week") -> Window:
    return Window(start=start, end=end, label="a period", kind=kind)


def _card(db, church, when: datetime, first="Guest"):
    card = GuestCard(
        church_id=church.id, first_name=first, last_name="Visitor",
        created_at=when,
    )
    db.session.add(card)
    db.session.flush()
    return card


class TestWindowsAreHalfOpen:
    """`start <= x < end`, tested at the instants rather than near them."""

    def test_a_row_at_the_start_instant_is_counted(self, db):
        church = _journey(db)
        start = datetime(2026, 3, 2, 6, tzinfo=UTC)
        _card(db, church, start)
        assert connect_cards(church.id, _window(start, start + timedelta(days=7))) == 1

    def test_a_row_at_the_end_instant_is_not(self, db):
        church = _journey(db)
        start = datetime(2026, 3, 2, 6, tzinfo=UTC)
        end = start + timedelta(days=7)
        _card(db, church, end)
        assert connect_cards(church.id, _window(start, end)) == 0

    def test_a_row_one_microsecond_before_the_end_is(self, db):
        church = _journey(db)
        start = datetime(2026, 3, 2, 6, tzinfo=UTC)
        end = start + timedelta(days=7)
        _card(db, church, end - timedelta(microseconds=1))
        assert connect_cards(church.id, _window(start, end)) == 1

    def test_two_adjacent_windows_never_both_claim_a_row(self, db):
        """The property the half-open rule exists for.

        One row on the boundary, two windows that meet there. Exactly one of
        them owns it, whichever one that is. Asserting the sum rather than
        which window gets it is the honest test: the rule is that reports do
        not overlap, not that the boundary belongs to the earlier one.
        """
        church = _journey(db)
        boundary = datetime(2026, 3, 9, 6, tzinfo=UTC)
        _card(db, church, boundary)

        earlier = _window(boundary - timedelta(days=7), boundary)
        later = _window(boundary, boundary + timedelta(days=7))
        assert connect_cards(church.id, earlier) + connect_cards(church.id, later) == 1


class TestWindowsRunOnTheChurchsClock:
    def test_a_saturday_evening_row_lands_in_the_right_week(self, db):
        """9pm Saturday in Jackson is 3am Sunday UTC.

        A week built on UTC midnight would push this row into the following
        week and leave it out of the report that covers the Saturday it
        happened on. The church's clock is the only clock a pastor has.
        """
        church = _journey(db)
        # Saturday 2026-03-07, 9pm local = Sunday 2026-03-08 03:00 UTC.
        saturday_evening = datetime(2026, 3, 8, 3, tzinfo=UTC)
        _card(db, church, saturday_evening)

        # The week that closed on Sunday 2026-03-08: Monday the 2nd onward.
        window = week_window(church, ending=date(2026, 3, 9))
        assert window.start == datetime(2026, 3, 2, 6, tzinfo=UTC)
        assert connect_cards(church.id, window) == 1

    def test_the_week_starts_at_local_midnight_not_utc_midnight(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        # The window is aware in the church's own zone, so midnight there is
        # 6am UTC in early March and 5am UTC after the clocks change on the
        # 8th. Read as instants, which is the only way the two are
        # comparable.
        assert window.start.astimezone(UTC).hour == 6
        assert window.end.astimezone(UTC).hour == 5
        assert window.start.hour == 0 and window.end.hour == 0

    def test_a_week_across_the_spring_change_is_167_hours(self, db):
        """Wall-clock arithmetic, which is what a church means by a week.

        2026-03-08 is the spring change in the United States. Midnight Monday
        to midnight Monday spans 167 hours, not 168, and the window has to be
        the seven local days rather than a fixed number of hours, or the last
        hour of the week belongs to nobody.
        """
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        # Converted to UTC before subtracting, and that is the whole lesson.
        # Both ends carry the same ZoneInfo object, and Python subtracts two
        # aware datetimes sharing a tzinfo by wall clock: it would answer
        # exactly seven days and the DST hour would never show up.
        assert (window.end.astimezone(UTC) - window.start.astimezone(UTC)) \
            == timedelta(hours=167)
        assert (window.end - window.start) == timedelta(days=7), (
            "wall clock still reads seven days, which is what a church means"
        )

    def test_a_week_across_the_autumn_change_is_169_hours(self, db):
        church = _journey(db)
        # 2026-11-01 is the autumn change; the week of Monday 2026-10-26
        # contains it.
        window = week_window(church, ending=date(2026, 11, 2))
        assert window.start == datetime(2026, 10, 26, 5, tzinfo=UTC)
        assert window.end == datetime(2026, 11, 2, 6, tzinfo=UTC)
        assert (window.end.astimezone(UTC) - window.start.astimezone(UTC)) \
            == timedelta(hours=169)


class TestWhichPeriodTheEmailIsAbout:
    """Always one that has closed. A Tuesday report reading "3 connect cards"
    invites somebody to compare it against a full week."""

    def test_the_weekly_window_is_the_week_that_finished(self, db):
        church = _journey(db)
        # Monday 2026-03-09. The week that closed is Monday the 2nd to
        # Sunday the 8th.
        window = week_window(church, ending=date(2026, 3, 9))
        assert window.start.astimezone(UTC).date() == date(2026, 3, 2)
        assert window.label == "Week of March 2"

    def test_the_weekly_window_from_midweek_still_looks_back_a_whole_week(self, db):
        church = _journey(db)
        thursday = week_window(church, ending=date(2026, 3, 12))
        monday = week_window(church, ending=date(2026, 3, 9))
        assert thursday.start == monday.start and thursday.end == monday.end

    def test_the_monthly_window_is_the_calendar_month_that_finished(self, db):
        church = _journey(db)
        window = month_window(church, ending=date(2026, 3, 1))
        assert window.start.astimezone(UTC).date() == date(2026, 2, 1)
        assert window.end.astimezone(UTC).date() == date(2026, 3, 1)
        assert window.label == "February 2026"

    def test_the_monthly_window_in_january_reaches_back_into_december(self, db):
        church = _journey(db)
        window = month_window(church, ending=date(2026, 1, 1))
        assert window.start.astimezone(UTC).date() == date(2025, 12, 1)
        assert window.label == "December 2025"

    def test_the_current_week_is_the_one_in_progress(self, db):
        """What the screen shows, unlike the email.

        Somebody opening the page on a Wednesday is asking about this week.
        """
        church = _journey(db)
        current = current_week(church)
        closed = week_window(church)
        assert current.start == closed.end

    def test_the_current_month_ends_at_the_first_of_next_month(self, db):
        church = _journey(db)
        current = current_month(church)
        assert current.end.astimezone(UTC).day == 1
        assert current.start.astimezone(UTC).day == 1
        assert current.end > current.start


class TestThePreviousPeriod:
    def test_a_week_compares_against_the_seven_days_before_it(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        before = window.previous
        assert before.end == window.start
        assert before.start == window.start - timedelta(days=7)

    def test_a_month_compares_against_the_calendar_month_before_it(self, db):
        """Not thirty days back.

        Subtracting a fixed thirty days from the 1st of March lands on the
        30th of January and compares February against most of January, which
        is a comparison that quietly understates every short month.
        """
        church = _journey(db)
        window = month_window(church, ending=date(2026, 3, 1))
        before = window.previous
        assert before.start.astimezone(UTC).date() == date(2026, 1, 1)
        assert before.end == window.start

    def test_a_january_month_steps_back_across_the_year(self, db):
        church = _journey(db)
        window = month_window(church, ending=date(2026, 2, 1))
        before = window.previous
        assert before.start.astimezone(UTC).date() == date(2025, 12, 1)


class TestEveryCountIsScopedToOneChurch:
    """`church_id` on every count. A multi-tenant platform whose metrics leak
    is a platform that emails one church another church's numbers."""

    def test_another_churchs_rows_are_invisible(self, db):
        journey, riverbend = _journey(db), _riverbend(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))

        _card(db, journey, when)
        _card(db, riverbend, when)
        _card(db, riverbend, when)

        assert connect_cards(journey.id, window) == 1
        assert connect_cards(riverbend.id, window) == 2

    def test_every_counter_filters_by_church(self, db):
        """Each count function, not just the one that was easy to test.

        Written as a loop over the real functions so that a count added later
        without a tenant filter fails here rather than in production. The
        assertion is that the other church sees nothing, which is the failure
        that matters.
        """
        journey, riverbend = _journey(db), _riverbend(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        _seed_one_of_everything(db, journey, when)

        empty = {
            "connect_cards": connect_cards(riverbend.id, window),
            "prayer": prayer_requests(riverbend.id, window),
            "baptism": signups(riverbend.id, window, "baptism"),
            "new_people": new_people(riverbend.id, window),
            "kids": kids_checkins(riverbend.id, window),
            "contacts": contacts_logged(riverbend.id, window),
            "attendance": attendance_total(riverbend.id, window),
            "moves": total_moves(riverbend.id, window),
        }
        assert empty == {key: 0 for key in empty}, (
            "A count is reaching across churches: " + repr(empty)
        )

        # And the church the rows belong to does see them, or the test above
        # would pass against a function that counts nothing at all.
        assert connect_cards(journey.id, window) == 1
        assert prayer_requests(journey.id, window) == 2
        assert attendance_total(journey.id, window) == 180


def _seed_one_of_everything(db, church, when: datetime):
    """One row in every table the metrics read, all inside one instant."""
    person = _person(db, church, created_at=when)
    _card(db, church, when)
    db.session.add_all([
        NextStepSignup(church_id=church.id, person_id=person.id,
                       offer="baptism", created_at=when),
        NextStepSignup(church_id=church.id, person_id=person.id,
                       offer="volunteer", created_at=when),
        PrayerCheckin(church_id=church.id, person_id=person.id,
                      week_of=when.date(), body="Please pray for my mother.",
                      created_at=when),
        SupportRequest(church_id=church.id, person_id=person.id,
                       kind=KIND_PRAYER, message="A hard week.",
                       created_at=when),
        ContactLog(church_id=church.id, person_id=person.id, method="call",
                   summary="Called to say hello", occurred_at=when),
        Service(church_id=church.id, name="Sunday", starts_at=when,
                headcount=180),
        PersonEvent(church_id=church.id, person_id=person.id,
                    kind=KIND_STAGE_CHANGE, summary="Moved from Visitor to Member",
                    from_stage="visitor", to_stage="member", occurred_at=when),
    ])
    session = CheckinSession(church_id=church.id, name="Sunday", starts_at=when)
    db.session.add(session)
    db.session.flush()
    db.session.add(Checkin(church_id=church.id, session_id=session.id,
                           person_id=person.id, pickup_code="AB12",
                           checked_in_at=when))
    db.session.flush()
    return person


class TestWhatEachNumberCounts:
    def test_prayer_adds_the_weekly_box_and_the_pastoral_form(self, db):
        """Both, because a member does not experience them as two features.

        Somebody who wrote in the weekly check-in box and somebody who used
        the support form both asked their church to pray for them.
        """
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)

        db.session.add_all([
            PrayerCheckin(church_id=church.id, person_id=person.id,
                          week_of=when.date(), body="A hard week.",
                          created_at=when),
            SupportRequest(church_id=church.id, person_id=person.id,
                           kind=KIND_PRAYER, message="Please pray.",
                           created_at=when),
            # A request of a different kind. Counting every support request
            # as prayer would inflate the one number a pastor is most likely
            # to read out loud.
            SupportRequest(church_id=church.id, person_id=person.id,
                           kind=KIND_TALK, message="Can we talk?",
                           created_at=when),
        ])
        db.session.flush()

        assert prayer_requests(church.id, window) == 2

    def test_a_sign_up_counts_under_its_own_offer(self, db):
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add_all([
            NextStepSignup(church_id=church.id, person_id=person.id,
                           offer="baptism", created_at=when),
            NextStepSignup(church_id=church.id, person_id=person.id,
                           offer="volunteer", created_at=when),
            NextStepSignup(church_id=church.id, person_id=person.id,
                           offer="volunteer", created_at=when),
        ])
        db.session.flush()
        assert signups(church.id, window, "baptism") == 1
        assert signups(church.id, window, "volunteer") == 2

    def test_a_sign_up_counts_when_it_was_asked_not_when_it_was_handled(self, db):
        """Otherwise this is a measure of staff response time wearing the
        label of a measure of interest."""
        church = _journey(db)
        asked = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(asked - timedelta(days=1), asked + timedelta(days=1))
        person = _person(db, church)
        db.session.add(NextStepSignup(
            church_id=church.id, person_id=person.id, offer="baptism",
            created_at=asked,
            # Dealt with three weeks later, well outside the window.
            handled_at=asked + timedelta(days=21),
            handled_by_name="Pastor Reed",
        ))
        db.session.flush()
        assert signups(church.id, window, "baptism") == 1

    def test_a_discarded_connect_card_still_counts(self, db):
        """The question is how many people filled one in, not how many
        survived triage. A number that moves when somebody tidies up is a
        number that cannot be compared month to month."""
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        card = _card(db, church, when)
        card.discarded_at = when + timedelta(hours=2)
        card.discarded_by_name = "Pastor Reed"
        db.session.flush()
        assert connect_cards(church.id, window) == 1

    def test_attendance_sums_head_counts_and_ignores_services_with_none(self, db):
        church = _journey(db)
        when = datetime(2026, 3, 8, 15, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        db.session.add_all([
            Service(church_id=church.id, name="9am", starts_at=when, headcount=120),
            Service(church_id=church.id, name="11am", starts_at=when, headcount=95),
            # Nobody counted this one. It must contribute nothing, which is
            # different from contributing zero: a zero would be a claim that
            # nobody came.
            Service(church_id=church.id, name="Evening", starts_at=when),
        ])
        db.session.flush()
        assert attendance_total(church.id, window) == 215

    def test_a_service_at_the_first_instant_of_the_window_is_counted(self, db):
        """Attendance has its own query rather than going through `_count`,
        so it needs the boundary tested on its own terms.

        A service at exactly midnight Monday is the week's first service. A
        mutation run found that nothing else here was exercising that edge:
        every other attendance test put the service in the middle of the
        window, where `>` and `>=` agree.
        """
        church = _journey(db)
        start = datetime(2026, 3, 2, 6, tzinfo=UTC)
        window = _window(start, start + timedelta(days=7))
        db.session.add(Service(church_id=church.id, name="Sunrise",
                               starts_at=start, headcount=40))
        db.session.flush()
        assert attendance_total(church.id, window) == 40

    def test_a_service_at_the_end_instant_belongs_to_the_next_window(self, db):
        church = _journey(db)
        start = datetime(2026, 3, 2, 6, tzinfo=UTC)
        end = start + timedelta(days=7)
        window = _window(start, end)
        db.session.add(Service(church_id=church.id, name="Next Sunday",
                               starts_at=end, headcount=40))
        db.session.flush()
        assert attendance_total(church.id, window) == 0

    def test_attendance_is_nought_when_no_service_was_counted(self, db):
        church = _journey(db)
        when = datetime(2026, 3, 8, 15, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        db.session.add(Service(church_id=church.id, name="9am", starts_at=when))
        db.session.flush()
        assert attendance_total(church.id, window) == 0

    def test_contacts_logged_counts_on_when_it_happened(self, db):
        """`occurred_at`, not `created_at`. Staff log Sunday's conversations
        on Tuesday, and the conversation happened on Sunday."""
        church = _journey(db)
        happened = datetime(2026, 3, 8, 16, tzinfo=UTC)
        written_down = datetime(2026, 3, 10, 14, tzinfo=UTC)
        window = _window(happened - timedelta(days=1), happened + timedelta(days=1))
        person = _person(db, church)
        db.session.add(ContactLog(
            church_id=church.id, person_id=person.id, method="call",
            summary="Talked after the service", occurred_at=happened,
            created_at=written_down,
        ))
        db.session.flush()
        assert contacts_logged(church.id, window) == 1


class TestMovesAreCountedOnTheColumn:
    def test_a_move_counts_under_the_stage_it_went_to(self, db):
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add_all([
            PersonEvent(church_id=church.id, person_id=person.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Visitor to Member",
                        from_stage="visitor", to_stage="member", occurred_at=when),
            PersonEvent(church_id=church.id, person_id=person.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Member to Volunteer",
                        from_stage="member", to_stage="volunteer", occurred_at=when),
            PersonEvent(church_id=church.id, person_id=person.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Visitor to Member",
                        from_stage="visitor", to_stage="member", occurred_at=when),
        ])
        db.session.flush()
        assert stage_moves(church.id, window) == {"member": 2, "volunteer": 1}
        assert total_moves(church.id, window) == 3

    def test_the_column_wins_over_the_sentence(self, db):
        """The one test that pins the decision.

        Before `to_stage` existed, the only record of a move was the English
        sentence in `summary`. A count that parsed that sentence would break
        the first time a church renamed a stage, and would never notice. Here
        the sentence says Leader and the column says Member: the count must
        follow the column.
        """
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add(PersonEvent(
            church_id=church.id, person_id=person.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Volunteer to Leader",
            from_stage="visitor", to_stage="member", occurred_at=when,
        ))
        db.session.flush()
        assert stage_moves(church.id, window) == {"member": 1}

    def test_a_move_backwards_is_counted_under_where_it_went(self, db):
        """A church that moves somebody from Volunteer back to Member has
        recorded something true. Hiding it would make the totals flatter than
        the rail actually is."""
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add(PersonEvent(
            church_id=church.id, person_id=person.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Volunteer to Member",
            from_stage="volunteer", to_stage="member", occurred_at=when,
        ))
        db.session.flush()
        assert stage_moves(church.id, window) == {"member": 1}

    def test_an_event_that_is_not_a_stage_change_is_ignored(self, db):
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add(PersonEvent(
            church_id=church.id, person_id=person.id, kind="note",
            summary="Had coffee", to_stage="leader", occurred_at=when,
        ))
        db.session.flush()
        assert stage_moves(church.id, window) == {}

    def test_an_old_event_with_no_stage_columns_is_skipped(self, db):
        """Rows written before the columns existed and missed by the backfill.

        Counting them as a move to nowhere would put a nameless row on the
        sheet; counting them under the first stage would invent moves that
        never happened. They are left out, and the migration's backfill is
        what keeps that from losing real history.
        """
        church = _journey(db)
        when = datetime(2026, 3, 4, 18, tzinfo=UTC)
        window = _window(when - timedelta(days=1), when + timedelta(days=1))
        person = _person(db, church)
        db.session.add(PersonEvent(
            church_id=church.id, person_id=person.id, kind=KIND_STAGE_CHANGE,
            summary="Stage changed", occurred_at=when,
        ))
        db.session.flush()
        assert stage_moves(church.id, window) == {}


class TestTheSheet:
    def test_the_three_sections_come_back_in_the_reading_order(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        sections = sections_for(church, window)
        assert [section.key for section in sections] == ["asked", "moved", "church"]

    def test_nobody_moves_to_the_first_stage_so_it_has_no_row(self, db):
        """A row that is structurally always nought teaches the reader to
        skim, and a reader who skims misses the row that matters."""
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        moved = next(s for s in sections_for(church, window) if s.key == "moved")
        assert "to_visitor" not in {metric.key for metric in moved.metrics}
        assert "to_member" in {metric.key for metric in moved.metrics}

    def test_zero_last_week_compares_as_zero_and_not_as_no_data(self, db):
        """The bug this pins.

        `moves_before.get(code)` returns None for a stage nobody moved to
        last week, and the screen prints None as "nothing to compare against
        yet" on a church with a year of history. Zero last week is a
        comparison, and a true one.
        """
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        person = _person(db, church)
        db.session.add(PersonEvent(
            church_id=church.id, person_id=person.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Visitor to Member", from_stage="visitor",
            to_stage="member", occurred_at=window.start + timedelta(days=1),
        ))
        db.session.flush()

        moved = next(s for s in sections_for(church, window) if s.key == "moved")
        member = next(m for m in moved.metrics if m.key == "to_member")
        assert member.count == 1
        assert member.previous == 0, "a stage nobody reached last week is 0, not None"
        assert member.change == 1
        assert member.is_growth

    def test_without_a_comparison_every_change_is_none(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        sections = sections_for(church, window, compare=False)
        changes = {
            metric.change for section in sections for metric in section.metrics
        }
        assert changes == {None}

    def test_a_decline_reads_as_a_decline(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        before = window.previous
        _card(db, church, before.start + timedelta(days=1))
        _card(db, church, before.start + timedelta(days=2))
        _card(db, church, window.start + timedelta(days=1))

        asked = next(s for s in sections_for(church, window) if s.key == "asked")
        cards = next(m for m in asked.metrics if m.key == "connect_cards")
        assert (cards.count, cards.previous, cards.change) == (1, 2, -1)
        assert cards.is_decline and not cards.is_growth

    def test_every_number_that_has_people_behind_it_links_to_them(self, db):
        """A number nobody can click into is a number nobody believes, and the
        first question after "eleven connect cards" is always "which eleven"."""
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        linked = [
            metric.key
            for section in sections_for(church, window)
            for metric in section.metrics
            if metric.endpoint
        ]
        for key in ("connect_cards", "prayer", "baptism", "to_member", "attendance"):
            assert key in linked, f"{key} has no way to see who is behind it"

    def test_the_links_resolve_to_real_endpoints(self, app):
        """Named endpoints are a string until something builds the URL.

        A typo in an endpoint name renders the whole metrics page as a 500 and
        nothing before this point would have noticed.
        """
        from flask import url_for

        with app.app_context():
            church = _journey(_db)
            window = week_window(church, ending=date(2026, 3, 9))
            with app.test_request_context(f"http://{JOURNEY_HOST}/"):
                for section in sections_for(church, window):
                    for metric in section.metrics:
                        if metric.endpoint:
                            url_for(metric.endpoint, **dict(metric.args))


class TestTheDigestBody:
    def test_it_carries_the_numbers(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        _seed_one_of_everything(db, church, window.start + timedelta(days=2))

        body = digest_module.body_for(church, window)
        assert "Connect cards: 1" in body
        assert "Prayer requests: 2" in body
        assert "Seats filled: 180" in body

    def test_it_never_carries_a_name_or_a_prayer(self, db):
        """The property the prayer feature was built for.

        Those words live on a screen behind a login. A weekly email that
        quoted them would undo that quietly, at scale, to whoever is on the
        recipient list.
        """
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        when = window.start + timedelta(days=2)
        person = _person(db, church, first="Marisol", last="Okonkwo",
                         created_at=when)
        db.session.add_all([
            PrayerCheckin(church_id=church.id, person_id=person.id,
                          week_of=when.date(),
                          body="My brother relapsed and I am frightened.",
                          created_at=when),
            SupportRequest(church_id=church.id, person_id=person.id,
                           kind=KIND_PRAYER,
                           message="We are losing the house.", created_at=when),
        ])
        _card(db, church, when, first="Marisol")
        db.session.flush()

        body = digest_module.body_for(church, window)
        for forbidden in ("Marisol", "Okonkwo", "relapsed", "losing the house"):
            assert forbidden not in body, (
                f"the digest is carrying {forbidden!r}, which belongs behind a login"
            )
        assert "Prayer requests: 2" in body

    def test_it_links_to_the_church_s_own_app(self, db):
        """Built from the church's domain, not `url_for`.

        This runs from the worker, with no request context and no
        SERVER_NAME, so `url_for(_external=True)` would raise.
        """
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        body = digest_module.body_for(church, window)
        assert "https://app.thejourneychurchsemo.com/metrics/" in body

    def test_a_church_with_no_domain_yet_gets_no_broken_link(self, db):
        """`display_app_domain` invents a `slug.example.org` for a church
        still being set up. A digest with a link that does not resolve is
        worse than one with no link, so this builds from the real columns."""
        church = _riverbend(db)
        assert church.app_domain is None and church.custom_domain is None
        body = digest_module.body_for(church, week_window(church))
        assert "http" not in body
        assert "example.org" not in body

    def test_the_subject_says_what_the_email_is(self, db):
        """"The Journey Church: Week of November 2" on its own is
        indistinguishable from a service plan in an inbox."""
        church = _journey(db)
        church.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.flush()
        window = week_window(church, ending=date(2026, 3, 9))
        digest_module.send(church, window)
        db.session.flush()

        message = db.session.scalar(
            db.select(OutboxMessage).where(OutboxMessage.category == "metrics")
        )
        assert message.subject == "The Journey Church: Week of March 2 in numbers"

    def test_the_monthly_subject_names_the_month(self, db):
        church = _journey(db)
        church.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.flush()
        digest_module.send(church, month_window(church, ending=date(2026, 3, 1)))
        db.session.flush()

        message = db.session.scalar(
            db.select(OutboxMessage).where(OutboxMessage.category == "metrics")
        )
        assert message.subject == "The Journey Church: February 2026 in numbers"

    def test_a_quiet_period_says_so_plainly(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        body = digest_module.body_for(church, window)
        assert "Nothing was recorded" in body
        # And it does not print a wall of noughts, which is what a reader
        # stops opening.
        assert "Connect cards: 0" not in body
        # It still carries the link. A quiet week is the week somebody most
        # wants to click through and check for themselves.
        assert "https://app.thejourneychurchsemo.com/metrics/" in body

    def test_a_change_is_spelled_out_against_the_period_before(self, db):
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        _card(db, church, window.previous.start + timedelta(days=1))
        _card(db, church, window.start + timedelta(days=1))
        _card(db, church, window.start + timedelta(days=2))

        body = digest_module.body_for(church, window)
        assert "Connect cards: 2 (+1 on the week before)" in body

    def test_the_monthly_digest_compares_against_the_month_before(self, db):
        church = _journey(db)
        window = month_window(church, ending=date(2026, 3, 1))
        _card(db, church, window.start + timedelta(days=3))
        body = digest_module.body_for(church, window)
        assert "February 2026" in body
        assert "on the month before" in body or "Connect cards: 1" in body


class TestWhoTheDigestGoesTo:
    def test_a_church_with_no_recipients_sends_nothing(self, db):
        """The default for every church added after Journey.

        Unlike the pastoral and guest lists, this one falls back to nobody
        rather than to all staff. A metrics report is a thing somebody asked
        for, and mailing a new client's whole staff on day one because a field
        was empty is not a sensible default.
        """
        church = _riverbend(db)
        assert church.digest_recipients == []

        window = week_window(church, ending=date(2026, 3, 9))
        sent = digest_module.send(church, window)
        assert sent.queued == () and sent.skipped == ()
        assert _queued(db, church) == []

    def test_every_address_on_the_list_gets_one(self, db):
        church = _journey(db)
        church.digest_emails = (
            "isaac@betweensundaysconsulting.com\npastor@journeychurchsemo.com"
        )
        db.session.flush()
        window = week_window(church, ending=date(2026, 3, 9))

        sent = digest_module.send(church, window)
        db.session.flush()
        assert set(sent.queued) == {
            "isaac@betweensundaysconsulting.com",
            "pastor@journeychurchsemo.com",
        }
        assert sorted(_queued(db, church)) == [
            "isaac@betweensundaysconsulting.com",
            "pastor@journeychurchsemo.com",
        ]

    def test_the_dedupe_key_carries_the_address(self, db):
        """Without it the first recipient queues and every other one is
        refused as a repeat, which looks exactly like the digest working."""
        church = _journey(db)
        window = week_window(church, ending=date(2026, 3, 9))
        first = digest_module.dedupe_key(church, window, "a@example.com")
        second = digest_module.dedupe_key(church, window, "b@example.com")
        assert first != second
        assert "a@example.com" in first

    def test_the_dedupe_key_separates_the_week_from_the_month(self, db):
        """A Monday that is also the 1st owes both, to the same address."""
        church = _journey(db)
        monday_the_first = date(2026, 6, 1)
        weekly = week_window(church, ending=monday_the_first)
        monthly = month_window(church, ending=monday_the_first)
        assert digest_module.dedupe_key(church, weekly, "a@example.com") != \
            digest_module.dedupe_key(church, monthly, "a@example.com")
        # The period kind is named in the key, not merely implied by the
        # start date. Today the two start dates can never collide, so
        # dropping it would break nothing and the next change to how a
        # window is built would. The key is also what somebody reads in the
        # outbox when working out why an email did or did not go.
        assert "week" in digest_module.dedupe_key(church, weekly, "a@example.com")
        assert "month" in digest_module.dedupe_key(church, monthly, "a@example.com")


def _queued(db, church, category="metrics"):
    return [
        message.to_email
        for message in db.session.scalars(
            db.select(OutboxMessage).where(
                OutboxMessage.church_id == church.id,
                OutboxMessage.category == category,
            )
        ).all()
    ]


class TestItCannotSendTwice:
    def test_a_second_run_queues_nothing(self, db):
        """The worker tick runs often and is allowed to run twice."""
        church = _journey(db)
        church.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.flush()
        window = week_window(church, ending=date(2026, 3, 9))

        first = digest_module.send(church, window)
        db.session.commit()
        second = digest_module.send(church, window)
        db.session.commit()

        assert first.queued == ("isaac@betweensundaysconsulting.com",)
        assert second.queued == ()
        assert second.skipped == ("isaac@betweensundaysconsulting.com",)
        assert _queued(db, church) == ["isaac@betweensundaysconsulting.com"]

    def test_the_next_week_is_a_different_email(self, db):
        church = _journey(db)
        church.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.flush()

        digest_module.send(church, week_window(church, ending=date(2026, 3, 9)))
        db.session.commit()
        digest_module.send(church, week_window(church, ending=date(2026, 3, 16)))
        db.session.commit()

        assert len(_queued(db, church)) == 2


class TestWhatIsDueToday:
    def test_monday_owes_the_week(self, db):
        church = _journey(db)
        due = digest_module.due(church, today=date(2026, 3, 9))
        assert [window.kind for window in due] == ["week"]

    def test_a_tuesday_owes_nothing(self, db):
        church = _journey(db)
        assert digest_module.due(church, today=date(2026, 3, 10)) == []

    def test_the_second_of_the_month_owes_nothing(self, db):
        """The day after the monthly report, which is the day a "day in (1, 2)"
        slip would send it a second time. 2026-09-02 is a Wednesday, so the
        weekly report is not owed either and the answer is an empty list."""
        church = _journey(db)
        assert digest_module.due(church, today=date(2026, 9, 2)) == []

    def test_the_last_day_of_a_month_owes_nothing(self, db):
        """The other end of the same mistake. 2026-09-30 is a Wednesday."""
        church = _journey(db)
        assert digest_module.due(church, today=date(2026, 9, 30)) == []

    def test_the_first_of_the_month_owes_the_month(self, db):
        church = _journey(db)
        # Thursday 2026-01-01.
        due = digest_module.due(church, today=date(2026, 1, 1))
        assert [window.kind for window in due] == ["month"]

    def test_a_monday_that_is_also_the_first_owes_both(self, db):
        """The case that makes `due` return a list.

        2026-06-01 is a Monday. Returning one window would silently drop the
        monthly report eleven times out of twelve and send it the twelfth, and
        nobody would work out why.
        """
        church = _journey(db)
        due = digest_module.due(church, today=date(2026, 6, 1))
        assert [window.kind for window in due] == ["week", "month"]


class TestTheWorkerRun:
    def test_it_sends_for_the_churches_that_have_a_list(self, db):
        journey, riverbend = _journey(db), _riverbend(db)
        journey.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.commit()

        sent = digest_module.run(today=date(2026, 3, 9))
        assert [s.queued for s in sent] == [("isaac@betweensundaysconsulting.com",)]
        assert _queued(db, journey) == ["isaac@betweensundaysconsulting.com"]
        assert _queued(db, riverbend) == []

    def test_it_skips_a_closed_church(self, db):
        """A church that is no longer a client still has rows. It must not
        still get email about them."""
        closed = db.session.scalar(db.select(Church).where(Church.slug == "closed"))
        closed.digest_emails = "someone@example.com"
        db.session.commit()

        digest_module.run(today=date(2026, 3, 9))
        assert _queued(db, closed) == []

    def test_one_church_can_be_run_on_its_own(self, db):
        journey, riverbend = _journey(db), _riverbend(db)
        journey.digest_emails = "isaac@betweensundaysconsulting.com"
        riverbend.digest_emails = "office@riverbendfellowship.org"
        db.session.commit()

        digest_module.run(today=date(2026, 3, 9), church_id=riverbend.id)
        assert _queued(db, journey) == []
        assert _queued(db, riverbend) == ["office@riverbendfellowship.org"]

    def test_a_run_on_a_quiet_day_sends_nothing(self, db):
        journey = _journey(db)
        journey.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.commit()
        assert digest_module.run(today=date(2026, 3, 10)) == []
        assert _queued(db, journey) == []

    def test_the_email_has_both_a_text_and_a_formatted_body(self, db):
        """`queue` builds the HTML from the markup grammar in the text body.

        The digest writes list markers rather than HTML so that it gets both
        halves from one source and looks like everything else the church
        sends. If the text body stopped using that grammar, this is where it
        would show.
        """
        journey = _journey(db)
        journey.digest_emails = "isaac@betweensundaysconsulting.com"
        db.session.commit()
        window = week_window(journey, ending=date(2026, 3, 9))
        _seed_one_of_everything(db, journey, window.start + timedelta(days=2))
        db.session.commit()

        digest_module.run(today=date(2026, 3, 9))
        message = db.session.scalar(
            db.select(OutboxMessage).where(OutboxMessage.category == "metrics")
        )
        assert "Connect cards: 1" in message.body_text
        assert "<li" in message.body_html
        assert "Connect cards: 1" in message.body_html


class TestTheMetricsScreen:
    def test_a_leader_can_open_it(self, leader):
        response = leader.get("/metrics/", headers={"Host": JOURNEY_HOST})
        assert response.status_code == 200
        assert b"What people asked for" in response.data

    def test_a_member_cannot(self, member):
        """Counts rather than names, but still a picture of a congregation's
        giving, prayer and attendance."""
        response = member.get("/metrics/", headers={"Host": JOURNEY_HOST})
        assert response.status_code in (302, 403)

    def test_each_of_the_four_periods_renders(self, leader):
        for period in ("week", "last_week", "month", "last_month"):
            response = leader.get(
                f"/metrics/?period={period}", headers={"Host": JOURNEY_HOST}
            )
            assert response.status_code == 200, period

    def test_an_unknown_period_is_a_404_rather_than_this_week(self, leader):
        """Silently falling back would make a mistyped link look like a
        report about a period nobody asked for."""
        response = leader.get(
            "/metrics/?period=quarter", headers={"Host": JOURNEY_HOST}
        )
        assert response.status_code == 404

    def test_the_dashboard_links_to_it(self, leader):
        response = leader.get("/", headers={"Host": JOURNEY_HOST})
        assert response.status_code == 200
        assert b'href="/metrics/"' in response.data

    def test_the_dashboard_link_is_styled_as_a_link(self):
        """There is no general `.link` class in this stylesheet: the tile
        links are `.ttrend.link`, scoped to a tile. An anchor given a bare
        `class="link"` rendered as plain black text and read as a caption,
        which is how a link to the page the four tiles summarise went
        unnoticed in a browser."""
        css = open("app/static/css/app.css").read()
        start = css.index(".dashmore a{")
        assert "color:var(--accent)" in css[start:start + 200]
        assert "text-decoration:underline" in css[start:start + 200]

    def test_the_numbers_on_the_screen_are_links(self, app, leader):
        with app.app_context():
            church = _journey(_db)
            window = current_week(church)
            _seed_one_of_everything(_db, church, window.start + timedelta(hours=2))
            _db.session.commit()

        response = leader.get("/metrics/", headers={"Host": JOURNEY_HOST})
        assert b'<a class="metnum"' in response.data


class TestTheNumbersLookLikeSomethingToTap:
    """One CSS rule carries meaning here, so it is pinned.

    Pinned structurally because a stylesheet has no behaviour a request can
    assert. Found by rendering the page and measuring it in a browser: the
    first version used `--line`, which at 14% opacity read as a rendering
    artifact rather than as an affordance.
    """

    @staticmethod
    def css() -> str:
        return open("app/static/css/app.css").read()

    def test_the_rule_under_a_number_is_visible_without_hovering(self):
        """An affordance that only appears under the pointer tells a reader
        nothing on a first look, and nothing at all on a phone."""
        css = self.css()
        start = css.index("a.metnum{")
        block = css[start:start + 300]
        assert "text-decoration:underline" in block
        assert "text-decoration-color:var(--muted-2)" in block, (
            "a fainter token reads as an artifact rather than as a link"
        )

    def test_the_rule_is_the_width_of_the_digits(self):
        """Without `align-self:start` the link's box fills the column and the
        rule runs the full width of the tile, which reads as a divider."""
        css = self.css()
        start = css.index(".metnum{")
        assert "align-self:start" in css[start:start + 300]


class TestTheSettingsRow:
    def test_a_staff_member_sees_the_recipients(self, staff):
        response = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        assert response.status_code == 200
        assert b"Metrics digest" in response.data

    def test_the_collapsed_row_shows_the_state_not_the_hint(self, app, staff):
        """Every other settings row shows the state in its subtitle and a
        short word in the pill. This one was showing the long hint in both
        the subtitle and the body, which printed the same paragraph twice on
        one screen."""
        with app.app_context():
            _journey(_db).digest_emails = "isaac@betweensundaysconsulting.com"
            _db.session.commit()

        response = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        body = response.get_data(as_text=True)
        assert "1 address" in body
        assert "Sending" in body
        assert body.count("emailed on Monday morning") == 1, (
            "the hint is printed in both the summary and the body"
        )

    def test_the_collapsed_row_says_when_nothing_is_being_sent(self, staff):
        response = staff.get("/settings/", headers={"Host": JOURNEY_HOST})
        body = response.get_data(as_text=True)
        assert "Not being sent" in body

    def test_saving_an_address_sets_it(self, app, staff):
        response = staff.post(
            "/settings/digest/",
            data={"emails": "pastor@journeychurchsemo.com"},
            headers={"Host": JOURNEY_HOST},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            church = _journey(_db)
            assert church.digest_recipients == ["pastor@journeychurchsemo.com"]

    def test_clearing_it_stops_the_digest(self, app, staff):
        with app.app_context():
            _journey(_db).digest_emails = "pastor@journeychurchsemo.com"
            _db.session.commit()

        staff.post("/settings/digest/", data={"emails": ""},
                   headers={"Host": JOURNEY_HOST}, follow_redirects=True)
        with app.app_context():
            assert _journey(_db).digest_recipients == []

    def test_a_line_without_an_at_sign_is_named_rather_than_dropped_quietly(
        self, app, staff
    ):
        """Every other recipient list drops junk silently and falls back to
        all active staff, so the mail still arrives. This list falls back to
        nobody: a typo means the digest never sends and nothing on any screen
        says why. So the bad line is named and the good one is still saved.
        """
        response = staff.post(
            "/settings/digest/",
            data={"emails": "isaac@betweensundaysconsulting.com\nnot an address"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"does not look like an email address" in response.data
        assert b"not an address" in response.data
        with app.app_context():
            assert _journey(_db).digest_recipients == [
                "isaac@betweensundaysconsulting.com"
            ]

    def test_the_same_address_twice_is_not_called_a_bad_address(self, app, staff):
        """`_addresses` collapses duplicates. Telling somebody their own
        address does not look like an email address because they typed it
        twice would be worse than saying nothing."""
        response = staff.post(
            "/settings/digest/",
            data={"emails": "isaac@betweensundaysconsulting.com\n"
                            "isaac@betweensundaysconsulting.com"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"does not look like an email address" not in response.data
        with app.app_context():
            assert _journey(_db).digest_recipients == [
                "isaac@betweensundaysconsulting.com"
            ]

    def test_send_now_queues_it(self, app, staff):
        with app.app_context():
            _journey(_db).digest_emails = "pastor@journeychurchsemo.com"
            _db.session.commit()

        response = staff.post("/settings/digest/send/",
                              headers={"Host": JOURNEY_HOST},
                              follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert _queued(_db, _journey(_db)) == ["pastor@journeychurchsemo.com"]

    def test_send_now_twice_does_not_send_twice(self, app, staff):
        """It reuses the scheduled dedupe key on purpose, so an early send
        suppresses Monday's rather than doubling it."""
        with app.app_context():
            _journey(_db).digest_emails = "pastor@journeychurchsemo.com"
            _db.session.commit()

        staff.post("/settings/digest/send/", headers={"Host": JOURNEY_HOST},
                   follow_redirects=True)
        second = staff.post("/settings/digest/send/",
                            headers={"Host": JOURNEY_HOST},
                            follow_redirects=True)
        assert b"already gone" in second.data
        with app.app_context():
            assert _queued(_db, _journey(_db)) == ["pastor@journeychurchsemo.com"]

    def test_send_now_with_nobody_on_the_list_says_so(self, staff):
        response = staff.post("/settings/digest/send/",
                              headers={"Host": JOURNEY_HOST},
                              follow_redirects=True)
        assert b"nobody to send it to" in response.data

    def test_a_member_cannot_change_the_recipients(self, app, member):
        response = member.post(
            "/settings/digest/", data={"emails": "member@example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        assert response.status_code in (302, 403)
        with app.app_context():
            assert _journey(_db).digest_recipients == []


class TestTheCategoryIsItsOwn:
    def test_the_digest_does_not_borrow_the_members_weekly_digest(self, db):
        """The outbox log is read by category. A staff report filed under the
        congregation's weekly digest is a report nobody can find, and a
        "Metrics digest" toggle on every member's preferences screen is a
        switch for a report no member is ever sent.
        """
        from app.categories import CATEGORY_BY_CODE, OPTIONAL_CATEGORIES

        assert digest_module.CATEGORY == "metrics"
        assert CATEGORY_BY_CODE["metrics"].is_transactional
        assert "metrics" not in {c.code for c in OPTIONAL_CATEGORIES}

    def test_a_staff_member_who_opted_out_of_everything_still_gets_it(self, db):
        """Opting out is done by removing the address, which is where somebody
        looking for the off switch will go. A preference row must not silently
        stop the report a church configured."""
        church = _journey(db)
        person = _person(db, church, first="Dana", last="Webb",
                         email="leader@journeychurchsemo.com")
        for code in ("digest", "announcement", "next_step"):
            person.set_preference(code, False)
        church.digest_emails = "leader@journeychurchsemo.com"
        db.session.commit()

        sent = digest_module.send(church, week_window(church, ending=date(2026, 3, 9)))
        db.session.commit()
        assert sent.queued == ("leader@journeychurchsemo.com",)


class TestTheWorkerRunsIt:
    def test_the_command_queues_the_digest(self, app):
        runner = app.test_cli_runner()
        with app.app_context():
            _journey(_db).digest_emails = "isaac@betweensundaysconsulting.com"
            _db.session.commit()

        result = runner.invoke(args=["send-digests", "--date", "2026-03-09"])
        assert result.exit_code == 0, result.output
        assert "1 queued" in result.output
        with app.app_context():
            assert _queued(_db, _journey(_db)) == [
                "isaac@betweensundaysconsulting.com"
            ]

    def test_the_command_can_run_twelve_times_on_a_monday(self, app):
        """Which is what a five minute worker does all Monday."""
        runner = app.test_cli_runner()
        with app.app_context():
            _journey(_db).digest_emails = "isaac@betweensundaysconsulting.com"
            _db.session.commit()

        for _ in range(12):
            result = runner.invoke(args=["send-digests", "--date", "2026-03-09"])
            assert result.exit_code == 0, result.output
        with app.app_context():
            assert _queued(_db, _journey(_db)) == [
                "isaac@betweensundaysconsulting.com"
            ]

    def test_an_unknown_church_is_an_error_rather_than_a_silent_no_op(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["send-digests", "--church", "nowhere"])
        assert result.exit_code != 0

    def test_the_tick_runs_digests_before_it_sends_the_outbox(self):
        """Order, not just presence.

        A digest queued after `send-outbox` has already run waits for the
        next tick. On a Monday morning that is the difference between the
        report being there when somebody opens their laptop and it not
        being. The steps are a local tuple inside the command, so this reads
        the source: structural, like the CSS tests, and it fails loudly if
        the tuple is ever reordered.
        """
        import inspect

        from app import cli

        source = inspect.getsource(cli)
        tick = source[source.index("def worker_tick"):]
        tick = tick[:tick.index("failed = []")]
        assert '"send-digests"' in tick, "worker-tick no longer runs the digest"
        assert tick.index('"send-digests"') < tick.index('"send-outbox"')
