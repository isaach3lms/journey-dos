"""The staff dashboard, laid out like the approved demo, on real numbers."""

from datetime import date, datetime, timedelta

import pytest

from app import dashboard
from app.models import Church, ContactLog, OutboxMessage, Person, SequenceEnrollment
from app.models.base import utcnow
from app.sequences import SEQUENCES
from app.models.contact import NextStep
from app.models.person_event import KIND_STAGE_CHANGE, PersonEvent
from app.models.signup import NextStepSignup
from app.models.giving_mirror import ExternalGift
from app.models.group import Group, GroupMembership
from app.models.service import Service, Team, TeamMembership
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def person(db, stage="member", **kw):
    p = Person(church_id=journey(db).id, first_name="A", last_name=f"P{stage}", stage=stage,
               approved_at=utcnow(), **kw)
    db.session.add(p)
    db.session.flush()
    return p


class TestDates:
    def test_next_sunday(self):
        assert dashboard.next_sunday(date(2026, 9, 19)) == (date(2026, 9, 20), 1)
        assert dashboard.next_sunday(date(2026, 9, 20)) == (date(2026, 9, 20), 0)
        assert dashboard.next_sunday(date(2026, 9, 14)) == (date(2026, 9, 20), 6)

    def test_weeks_end_today(self):
        starts = dashboard.week_starts(date(2026, 9, 19))
        assert len(starts) == 7
        assert starts[-1] == date(2026, 9, 13)
        assert dashboard._bucket([(date(2026, 9, 19), 5), (date(2026, 9, 12), 2)], starts)[-2:] == [2, 5]


class TestPage:
    def test_staff_see_every_section(self, client, sign_in, db):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data
        for text in (b"Welcome To Your Dashboard", b"The Journey", b"Open People",
                     b"Attendance last Sunday", b"First time guests", b"Next steps taken",
                     b"Giving month to date", b"Needs a person", b"Church health",
                     b"Running without staff time", b"Request support", b"Search",
                     b"people flagged as stuck too long",
                     # The footer line says staff follow-ups, because that is
                     # what it counts. It read "next steps taken" while the
                     # tile above it read "Next steps taken" and counted
                     # something else entirely.
                     b"staff follow-ups completed in the last 7 days"):
            assert text in page, text

    def test_subheading_counts_down_to_sunday(self, client, sign_in, db):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data
        assert b"Sunday, " in page and (b"days out" in page or b"tomorrow" in page or b"is today" in page)

    def test_members_are_sent_to_their_app(self, client, sign_in, db):
        sign_in("member@journeychurchsemo.com")
        assert client.get("/", headers=H).status_code == 302

    def test_empty_tiles_say_what_to_do(self, client, sign_in, db):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).data
        assert b"Add last Sunday&#39;s headcount" in page or b"Add last Sunday's headcount" in page
        assert b"Connect giving to see this" in page

    def test_support_goes_to_between_sundays(self, client, sign_in, db):
        sign_in("pastor@journeychurchsemo.com")
        assert b"mailto:isaac@betweensundaysconsulting.com" in client.get("/", headers=H).data


class TestNumbers:
    def test_stuck_counted_per_stage(self, db):
        """Counted per stage, and only transitional stages can appear.

        This used to assert {"visitor": 2, "attender": 1}, which read as a
        test about two stages and was really a test about one rule: a stage
        with an expectation on it can hold stuck people and a destination
        cannot. Attender was retired, so the rule is asserted directly rather
        than through whichever stages happen to exist.
        """
        from app.stages import STAGES, TRANSITIONAL_STAGES

        old = utcnow() - timedelta(days=200)
        for stage in STAGES:
            person(db, stage.code, stage_since=old, last_contact_at=old)
        person(db, TRANSITIONAL_STAGES[0].code, stage_since=old, last_contact_at=old)
        db.session.commit()

        counts = dashboard.stuck_by_stage(journey(db).id)

        # Every transitional stage, and nothing else. A destination stage
        # appearing here is the bug that flagged 39 of Journey's 54 people.
        transitional = {s.code for s in TRANSITIONAL_STAGES}
        assert set(counts) == transitional
        # Two were seeded on the first transitional stage, one on each other.
        assert counts[TRANSITIONAL_STAGES[0].code] == 2
        for stage in TRANSITIONAL_STAGES[1:]:
            assert counts[stage.code] == 1

    def test_attendance_uses_headcounts_and_compares_to_four_weeks(self, db):
        c = journey(db)
        now = utcnow()
        for weeks_ago, count in ((0, 420), (1, 400), (2, 400), (3, 400), (4, 400)):
            db.session.add(Service(church_id=c.id, name="Sunday",
                                   starts_at=now - timedelta(days=7 * weeks_ago, hours=1), headcount=count))
        db.session.commit()
        starts = dashboard.week_starts(now.date())
        tile = dashboard.attendance_tile(c, starts)
        assert tile.value == "420"
        assert tile.trend == ("up", 5)

    def test_attendance_empty_points_at_last_service(self, db):
        c = journey(db)
        s = Service(church_id=c.id, name="Sunday", starts_at=utcnow() - timedelta(days=1))
        db.session.add(s)
        db.session.commit()
        tile = dashboard.attendance_tile(c, dashboard.week_starts(utcnow().date()))
        assert tile.value is None and tile.link == s.id

    def test_first_time_guests_this_week(self, db):
        today = utcnow().date()
        person(db, "visitor", first_seen_on=today)
        person(db, "visitor", first_seen_on=today - timedelta(days=2))
        person(db, "visitor", first_seen_on=today - timedelta(days=9))
        db.session.commit()
        tile = dashboard.guests_tile(journey(db).id, dashboard.week_starts(today))
        assert tile.value == "2" and tile.trend == ("up", 1)

    def test_giving_compares_the_same_day_last_month(self, db):
        c = journey(db)
        today = date(2026, 9, 10)
        for when, cents in ((date(2026, 9, 2), 110_00), (date(2026, 8, 3), 100_00), (date(2026, 8, 20), 999_00)):
            db.session.add(ExternalGift(church_id=c.id, provider="tithely", provider_txn_id=str(when),
                                        amount_cents=cents, received_on=when))
        db.session.commit()
        tile = dashboard.giving_tile(c.id, today, dashboard.week_starts(today))
        assert tile.value == "$110"
        assert tile.trend == ("up", 10)

    def test_health_lines(self, db):
        c = journey(db)
        now = utcnow()
        member_a = person(db, "member", stage_since=now - timedelta(days=400))
        member_b = person(db, "member", stage_since=now - timedelta(days=5))
        guest = person(db, "visitor", stage_since=now - timedelta(days=3), first_seen_on=(now - timedelta(days=3)).date())
        g = Group(church_id=c.id, name="G")
        t = Team(church_id=c.id, name="T")
        db.session.add_all([g, t])
        db.session.flush()
        db.session.add(GroupMembership(church_id=c.id, group_id=g.id, person_id=member_a.id))
        db.session.add(TeamMembership(church_id=c.id, team_id=t.id, person_id=member_b.id))
        db.session.add(ContactLog(church_id=c.id, person_id=guest.id, method="call", summary="hi",
                                  occurred_at=now - timedelta(days=2)))
        db.session.commit()
        lines = {line.key: line for line in dashboard.health_lines(c.id)}
        assert (lines["grouped"].numerator, lines["grouped"].denominator) == (1, 2)
        assert (lines["serving"].numerator, lines["serving"].denominator) == (1, 3)
        assert lines["guests_48h"].percent == 100
        assert (lines["next_step"].numerator, lines["next_step"].denominator) == (2, 3)
        assert dashboard.weakest(list(lines.values())).key == "serving"

    def test_automation_counts_emails_per_sequence(self, db):
        c = journey(db)
        p = person(db, "visitor")
        e = SequenceEnrollment(church_id=c.id, person_id=p.id, sequence_code="first_visit_welcome", status="active")
        db.session.add(e)
        db.session.flush()
        for step in range(2):
            db.session.add(OutboxMessage(church_id=c.id, to_email="a@example.com", category="welcome",
                                         subject="s", body_text="b", status="sent", queued_at=utcnow(),
                                         dedupe_key=f"sequence:{e.id}:step:{step}"))
        db.session.commit()
        rows = {r["name"]: r for r in dashboard.automation_rows(c.id)}
        assert rows["First visit welcome"]["sent"] == 2
        # "Attender follow up" was the second row here until it was retired
        # with the stage it fired on. A retired sequence must not appear at
        # all: a row on this screen reads as something the church has
        # running, and one that can never fire is worse than a missing row.
        assert "Attender follow up" not in rows
        assert set(rows) == {s.name for s in SEQUENCES}

    def test_other_churches_do_not_leak(self, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(Person(church_id=other.id, first_name="X", last_name="Y", stage="visitor",
                              first_seen_on=utcnow().date()))
        db.session.commit()
        tile = dashboard.guests_tile(journey(db).id, dashboard.week_starts(utcnow().date()))
        assert tile.value == "0"


class TestHeadcount:
    def test_staff_record_it_on_the_service(self, client, sign_in, db):
        s = Service(church_id=journey(db).id, name="Sunday", starts_at=utcnow() - timedelta(days=1))
        db.session.add(s)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        assert b'id="headcount"' in client.get(f"/services/{s.id}/", headers=H).data
        client.post(f"/services/{s.id}/headcount/", headers=H, data={"headcount": "412"})
        db.session.refresh(s)
        assert s.headcount == 412
        assert b"412" in client.get("/", headers=H).data

    def test_nonsense_is_refused(self, client, sign_in, db):
        s = Service(church_id=journey(db).id, name="Sunday", starts_at=utcnow())
        db.session.add(s)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/services/{s.id}/headcount/", headers=H, data={"headcount": "-3"})
        db.session.refresh(s)
        assert s.headcount is None


class TestTheNextStepsTile:
    """What the congregation did, not what the staff ticked off.

    The tile used to count `NextStep` rows a staff member marked done. That
    is staff throughput wearing a label that says the congregation is moving:
    an afternoon spent closing out old tasks would read as a strong week on
    the rail. It now counts moves along the rail and the cards people fill
    in, which is what the words above it have always claimed.
    """

    @staticmethod
    def tile(db, today=None):
        church = journey(db)
        starts = dashboard.week_starts(today or utcnow().date())
        return dashboard.next_steps_tile(church, starts), starts

    def test_a_move_along_the_rail_counts(self, db):
        who = person(db)
        db.session.add(PersonEvent(
            church_id=journey(db).id, person_id=who.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Visitor to Member", from_stage="visitor",
            to_stage="member", occurred_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "1"

    def test_a_baptism_card_counts(self, db):
        who = person(db)
        db.session.add(NextStepSignup(
            church_id=journey(db).id, person_id=who.id, offer="baptism",
            created_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "1"

    def test_a_volunteer_card_counts(self, db):
        who = person(db)
        db.session.add(NextStepSignup(
            church_id=journey(db).id, person_id=who.id, offer="volunteer",
            created_at=utcnow() - timedelta(days=2),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "1"

    def test_an_offer_added_later_counts_without_anyone_remembering(self, db):
        """Baptism and volunteer are the offers that exist today. Naming them
        in the query would mean the next offer a church adds is a next step
        the dashboard does not count, and nobody would notice for a month."""
        who = person(db)
        db.session.add(NextStepSignup(
            church_id=journey(db).id, person_id=who.id, offer="membership_class",
            created_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "1"

    def test_both_in_one_week_count_as_two(self, db):
        """Asking to be baptised and then moving from Member to Disciple are
        two steps. Nothing in the system turns one into the other: the only
        automatic move reads two flags on a profile and knows nothing about
        these cards, so there is no path by which one action counts twice."""
        who = person(db)
        db.session.add_all([
            NextStepSignup(church_id=journey(db).id, person_id=who.id,
                           offer="baptism", created_at=utcnow() - timedelta(days=3)),
            PersonEvent(church_id=journey(db).id, person_id=who.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Member to Disciple",
                        from_stage="member", to_stage="disciple",
                        occurred_at=utcnow() - timedelta(days=1)),
        ])
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "2"

    def test_a_staff_task_marked_done_no_longer_counts(self, db):
        """The change, pinned. A staff member closing out three tasks is not
        three people taking a next step."""
        who = person(db)
        from app.models.contact import STATUS_DONE

        for index in range(3):
            db.session.add(NextStep(
                church_id=journey(db).id, person_id=who.id,
                title=f"Call them {index}", status=STATUS_DONE,
                completed_at=utcnow() - timedelta(days=1),
            ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "0"

    def test_staff_follow_ups_still_have_their_own_number(self, db):
        """Nothing was lost, it was relabelled. The line under the rail still
        counts them, and now says so."""
        who = person(db)
        from app.models.contact import STATUS_DONE

        db.session.add(NextStep(
            church_id=journey(db).id, person_id=who.id, title="Call them",
            status=STATUS_DONE, completed_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        assert dashboard.next_steps_done_since(journey(db).id, 7) == 1

    def test_an_event_that_is_not_a_move_is_ignored(self, db):
        """The note carries stage columns on purpose.

        Without them this passes against a query that has lost its `kind`
        filter entirely, because the `to_stage is not null` filter catches a
        plain note anyway. A mutation run found exactly that: the first
        version of this test proved nothing about the filter it was named
        for.
        """
        who = person(db)
        db.session.add(PersonEvent(
            church_id=journey(db).id, person_id=who.id, kind="note",
            summary="Had coffee and talked about serving",
            from_stage="member", to_stage="volunteer",
            occurred_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "0"

    def test_a_card_is_bucketed_on_the_church_s_clock(self, db):
        """10pm Sunday in Jackson is 4am Monday in UTC.

        On a bucket boundary that is the difference between the week that
        just ended and the week in progress, and a pastor comparing the last
        two bars would be reading one week's work filed under the next. The
        moment below is chosen so the local answer and the server's answer
        are two different bars, which is the only way a server-clock bug
        shows up rather than passing by coincidence.
        """
        who = person(db)
        db.session.add(NextStepSignup(
            church_id=journey(db).id, person_id=who.id, offer="baptism",
            # 2026-11-09 04:00 UTC is 2026-11-08 22:00 in Missouri.
            created_at=datetime(2026, 11, 9, 4),
        ))
        db.session.commit()

        church = journey(db)
        starts = dashboard.week_starts(date(2026, 11, 15))
        # Bar 5 covers Nov 2 to Nov 8; bar 6 covers Nov 9 to Nov 15.
        assert (starts[5], starts[6]) == (date(2026, 11, 2), date(2026, 11, 9))

        tile = dashboard.next_steps_tile(church, starts)
        assert tile.bars[5] == 1, "the Sunday card landed in the following week"
        assert tile.bars[6] == 0

    def test_a_pre_backfill_row_with_no_stage_columns_is_ignored(self, db):
        """Counting it would be a move to nowhere on the one screen a pastor
        reads first."""
        who = person(db)
        db.session.add(PersonEvent(
            church_id=journey(db).id, person_id=who.id, kind=KIND_STAGE_CHANGE,
            summary="Stage changed", occurred_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "0"

    def test_another_church_is_invisible(self, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="B", last_name="Q",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.flush()
        db.session.add_all([
            NextStepSignup(church_id=other.id, person_id=theirs.id, offer="baptism",
                           created_at=utcnow() - timedelta(days=1)),
            PersonEvent(church_id=other.id, person_id=theirs.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Visitor to Member",
                        from_stage="visitor", to_stage="member",
                        occurred_at=utcnow() - timedelta(days=1)),
        ])
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "0"

    def test_last_week_lands_in_the_bar_before_this_one(self, db):
        who = person(db)
        db.session.add_all([
            PersonEvent(church_id=journey(db).id, person_id=who.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Visitor to Member",
                        from_stage="visitor", to_stage="member",
                        occurred_at=utcnow() - timedelta(days=9)),
            PersonEvent(church_id=journey(db).id, person_id=who.id,
                        kind=KIND_STAGE_CHANGE, summary="Moved from Member to Volunteer",
                        from_stage="member", to_stage="volunteer",
                        occurred_at=utcnow() - timedelta(days=1)),
        ])
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.bars[-2:] == [1, 1]
        assert tile.value == "1"
        assert tile.direction == "flat"

    def test_a_better_week_than_the_last_one_reads_as_up(self, db):
        who = person(db)
        db.session.add(PersonEvent(
            church_id=journey(db).id, person_id=who.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Visitor to Member", from_stage="visitor",
            to_stage="member", occurred_at=utcnow() - timedelta(days=9),
        ))
        for index in range(3):
            db.session.add(NextStepSignup(
                church_id=journey(db).id, person_id=who.id, offer="baptism",
                created_at=utcnow() - timedelta(days=1, hours=index),
            ))
        db.session.commit()
        tile, _ = self.tile(db)
        assert tile.value == "3"
        assert tile.direction == "up"
        assert tile.trend == ("up", 2)

    def test_the_number_opens_the_page_that_lists_them(self, client, sign_in, db):
        who = person(db)
        db.session.add(NextStepSignup(
            church_id=journey(db).id, person_id=who.id, offer="baptism",
            created_at=utcnow() - timedelta(days=1),
        ))
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).get_data(as_text=True)
        assert '<a class="tvalue link" href="/metrics/">' in page

    def test_the_tile_still_says_whether_the_number_is_good(self, client, sign_in, db):
        """The first version of this change put the value in its own template
        branch and lost the trend line with it, which left a tile reading
        "14" and nothing to say whether 14 was a good week."""
        who = person(db)
        db.session.add(PersonEvent(
            church_id=journey(db).id, person_id=who.id, kind=KIND_STAGE_CHANGE,
            summary="Moved from Visitor to Member", from_stage="visitor",
            to_stage="member", occurred_at=utcnow() - timedelta(days=9),
        ))
        for index in range(3):
            db.session.add(NextStepSignup(
                church_id=journey(db).id, person_id=who.id, offer="baptism",
                created_at=utcnow() - timedelta(days=1, hours=index),
            ))
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/", headers=H).get_data(as_text=True)
        assert "Up 2 from last week" in page

    def test_a_linked_tile_number_looks_like_a_link(self):
        """The third place a bare `class="link"` styled nothing. There is no
        general `.link` class in this stylesheet, so the tile numbers that
        open a page rendered identically to the ones that do not, and nobody
        could tell which headline number was worth clicking. Found by
        measuring computed colours in a browser."""
        css = open("app/static/css/app.css").read()
        start = css.index("a.tvalue.link{")
        block = css[start:start + 300]
        assert "text-decoration:underline" in block
        assert "text-decoration-color:var(--muted-2)" in block
