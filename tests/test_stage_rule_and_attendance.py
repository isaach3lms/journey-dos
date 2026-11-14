"""Four changes to how a church reads its own people and its own Sundays.

**The rule that moves somebody.** This is the only thing in the application
that changes a person's stage without a human pressing a button on that
person's record, so most of this file is about the three ways it must never
fire: never backwards, never past Disciple, and never because a box was
unticked. A rule that demotes somebody once will not be trusted again, and
the stage column is load-bearing for the sequence engine and the stuck
engine both.

**The next step on the snapshot.** One line, and the test that matters is
that it agrees with the button at the foot of the page. Two places on one
screen saying different things about where somebody goes next is worse than
neither saying anything.

**Attendance.** The sums are the point. A church reads a change of nine as a
fact about last Sunday, and the ways this goes quietly wrong are all about
weeks nobody counted: treated as zero they manufacture a collapse, and
skipped carelessly they shift the comparison onto the wrong week.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import stage_rules
from app.attendance import AVERAGE_OVER, summary, trend, weeks_for
from app.content import PEOPLE, SERVICES
from app.models import (
    Church,
    Person,
    PersonEvent,
    Service,
    ServiceAssignment,
    ServiceTrack,
)
from app.models.base import utcnow
from app.models.service import ACCEPTED, DECLINED, INVITED
from app.stages import next_stage, stage_order
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def a_person(db, journey, stage="visitor", **kwargs):
    person = Person(
        church_id=journey.id, first_name="Marcus", last_name="Webb",
        stage=stage, approved_at=utcnow(), **kwargs,
    )
    db.session.add(person)
    db.session.commit()
    return person


def a_service(db, journey, *, days_ago=7, headcount=None, name="Sunday"):
    service = Service(
        church_id=journey.id, name=name,
        starts_at=utcnow() - timedelta(days=days_ago),
        headcount=headcount,
    )
    db.session.add(service)
    db.session.flush()
    track = db.session.scalar(
        db.select(ServiceTrack).where(ServiceTrack.service_id == service.id)
    )
    if track is None:
        track = ServiceTrack(church_id=journey.id, service_id=service.id,
                             name="Main", position=1)
        db.session.add(track)
        db.session.flush()
    db.session.commit()
    return service


def assign(db, journey, service, person, *, status=ACCEPTED, role=None):
    track = db.session.scalar(
        db.select(ServiceTrack).where(ServiceTrack.service_id == service.id)
    )
    row = ServiceAssignment(
        church_id=journey.id, service_id=service.id, track_id=track.id,
        person_id=person.id, status=status, position_name=role,
    )
    db.session.add(row)
    db.session.commit()
    return row


def save_flags(client, person_id, **fields):
    data = {key: "1" for key, value in fields.items() if value}
    return client.post(f"/people/{person_id}/flags/", data=data, headers=H,
                       follow_redirects=True)


# ---------------------------------------------------------------------------


class TestWhenBothBoxesAreTicked:
    def test_a_member_becomes_a_disciple(self, db, journey, client, sign_in):
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.stage == "disciple"

    def test_a_visitor_goes_straight_there(self, db, journey, client, sign_in):
        """Skipping Member and Volunteer, which is the decision this rule
        makes. Those stages are descriptions of where somebody is, not gates
        they have to pass through, and a Visitor who serves every week and
        gives every month is only a Visitor because nobody updated the
        record."""
        person = a_person(db, journey, stage="visitor")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.stage == "disciple"

    def test_the_clock_restarts(self, db, journey, client, sign_in):
        """The stuck engine measures time in the current stage. Without this
        a person moved by the rule reads as stuck the moment they arrive."""
        person = a_person(db, journey, stage="member")
        person.stage_since = utcnow() - timedelta(days=400)
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        db.session.refresh(person)
        assert (utcnow() - person.stage_since).days == 0

    def test_the_timeline_says_why(self, db, journey, client, sign_in):
        """A stage change nobody can account for six months later is the one
        that makes staff distrust the whole record."""
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == person.id)
        ).all()
        details = " ".join(e.detail or "" for e in events)
        assert "Moved automatically" in details
        assert "regular volunteer" in details

    def test_the_person_is_told_on_screen(self, db, journey, client, sign_in):
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        page = save_flags(client, person.id, is_regular_giver=True,
                          is_regular_volunteer=True).get_data(as_text=True)
        assert "moved from Member to Disciple" in page


class TestWhenItMustNotFire:
    def test_one_box_alone_does_nothing(self, db, journey, client, sign_in):
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.stage == "member"

    def test_a_leader_is_not_demoted(self, db, journey, client, sign_in):
        """A Leader who starts giving regularly has not stopped leading.

        This is the one that would do real damage: the sequence engine and
        the stuck engine both read stage, so a silent demotion would also
        re-enrol somebody in a sequence they finished a year ago.
        """
        person = a_person(db, journey, stage="leader")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.stage == "leader"

    def test_a_disciple_is_left_where_they_are(self, db, journey, client, sign_in):
        person = a_person(db, journey, stage="disciple")
        person.stage_since = utcnow() - timedelta(days=200)
        db.session.commit()
        before = person.stage_since

        sign_in("pastor@journeychurchsemo.com")
        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.stage == "disciple"
        # And the clock is not quietly reset, which would make somebody who
        # has been a Disciple for seven months look like a new arrival.
        assert person.stage_since == before

    def test_unticking_never_moves_anybody_back(self, db, journey, client,
                                                sign_in):
        """The flags say what is true now. The stage is a record of how far
        somebody has come, and a church that pauses giving for three months
        has not undone the last two years."""
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)
        save_flags(client, person.id)  # both boxes cleared

        db.session.refresh(person)
        assert person.stage == "disciple"
        assert person.is_regular_giver is False
        assert person.is_regular_volunteer is False

    def test_it_does_not_fire_twice(self, db, journey, client, sign_in):
        """Saving the same form again must not write a second stage change,
        which would show up as two moves to the same place on one timeline."""
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)
        save_flags(client, person.id, is_regular_giver=True,
                   is_regular_volunteer=True)

        moves = [
            event for event in db.session.scalars(
                db.select(PersonEvent).where(PersonEvent.person_id == person.id)
            ).all()
            if "Moved automatically" in (event.detail or "")
        ]
        assert len(moves) == 1

    def test_the_condition_can_be_asked_without_causing_anything(self, db,
                                                                 journey):
        person = a_person(db, journey, stage="member",
                          is_regular_giver=True, is_regular_volunteer=True)
        assert stage_rules.should_advance(person) is True
        assert person.stage == "member", "asking must not move anybody"


class TestTheCheckboxOnTheProfile:
    def test_it_is_on_the_snapshot(self, db, journey, client, sign_in):
        person = a_person(db, journey)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        assert 'name="is_regular_volunteer"' in page
        assert PEOPLE["flag_volunteer"] in page

    def test_the_rule_is_stated_before_it_fires(self, db, journey, client,
                                                sign_in):
        """This form changes a record it is not sitting on. A warning that
        only appears in the flash afterwards is read by somebody who has
        already done it."""
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        assert "moves this person to Disciple" in page

    def test_the_warning_goes_once_it_cannot_apply(self, db, journey, client,
                                                   sign_in):
        person = a_person(db, journey, stage="leader")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        assert "moves this person to Disciple" not in page

    def test_ticking_it_is_recorded(self, db, journey, client, sign_in):
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")

        save_flags(client, person.id, is_regular_volunteer=True)

        db.session.refresh(person)
        assert person.is_regular_volunteer is True
        events = db.session.scalars(
            db.select(PersonEvent).where(PersonEvent.person_id == person.id)
        ).all()
        assert any("regular volunteer" in (e.detail or "") for e in events)


class TestTheNextStepOnTheSnapshot:
    @staticmethod
    def cell(page: str) -> str:
        """Just the Next step cell.

        Searching the whole page for "Member" is how the first version of
        this test passed against a profile showing the stage somebody is
        already at: every stage name is also in the move dropdown, and
        "Member" is in the sign-in role list as well.
        """
        import re

        match = re.search(
            re.escape(PEOPLE["next_step_heading"]) + r"</dt>\s*<dd>(.*?)</dd>",
            page, re.S,
        )
        assert match, "the Next step cell is not on the page"
        return match.group(1)

    @pytest.mark.parametrize("stage,expected,not_expected", [
        ("visitor", "Member", "Visitor"),
        ("member", "Volunteer", "Member"),
        ("volunteer", "Disciple", "Volunteer"),
        ("disciple", "Leader", "Disciple"),
    ])
    def test_it_names_the_stage_after_this_one(self, db, journey, client,
                                               sign_in, stage, expected,
                                               not_expected):
        person = a_person(db, journey, stage=stage)
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        cell = self.cell(page)
        assert expected in cell
        # And not the stage they are already at, which is the one wrong
        # answer that looks right at a glance.
        assert not_expected not in cell

    def test_it_offers_the_step_that_gets_them_there(self, db, journey, client,
                                                     sign_in):
        """The recommended action under the stage name. A recommendation, not
        an instruction: staff assign whatever actually fits."""
        from app.stages import recommended_next_step

        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        assert recommended_next_step("member") in self.cell(page)

    def test_a_leader_is_told_there_is_nothing_after(self, db, journey, client,
                                                     sign_in):
        person = a_person(db, journey, stage="leader")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        assert PEOPLE["next_step_end"] in page

    def test_it_agrees_with_the_button_at_the_foot_of_the_page(self, db, journey,
                                                               client, sign_in):
        """Two places on one screen saying different things about where
        somebody goes next is worse than neither saying anything."""
        person = a_person(db, journey, stage="member")
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)

        target = next_stage(person.stage)
        assert PEOPLE["advance_to"].format(stage=target.label) in page
        assert f'value="{target.code}"' in page


class TestTheWeeksBehindBothScreens:
    def test_it_lists_services_that_have_happened(self, db, journey):
        a_service(db, journey, days_ago=7, headcount=100)
        a_service(db, journey, days_ago=14, headcount=90)

        weeks = weeks_for(journey.id)
        assert [w.headcount for w in weeks] == [90, 100], "oldest first"

    def test_a_service_still_to_come_is_not_on_it(self, db, journey):
        """A Sunday next week has a schedule and no head count. On a chart it
        draws a line down to zero at the right-hand edge that looks like a
        collapse."""
        a_service(db, journey, days_ago=7, headcount=100)
        a_service(db, journey, days_ago=-7, headcount=None, name="Next week")

        assert len(weeks_for(journey.id)) == 1

    def test_who_served_is_who_did_not_decline(self, db, journey):
        """The same rule the roster report uses for last served. Two reports
        that disagreed about whether somebody served on a given Sunday would
        cost a pastor their trust in both."""
        service = a_service(db, journey, headcount=100)
        accepted = a_person(db, journey)
        invited = Person(church_id=journey.id, first_name="Bo", last_name="Niles",
                         stage="member", approved_at=utcnow())
        declined = Person(church_id=journey.id, first_name="Dee", last_name="Fry",
                          stage="member", approved_at=utcnow())
        db.session.add_all([invited, declined])
        db.session.commit()

        assign(db, journey, service, accepted, status=ACCEPTED)
        assign(db, journey, service, invited, status=INVITED)
        assign(db, journey, service, declined, status=DECLINED)

        names = {s.name for s in weeks_for(journey.id)[0].servers}
        assert names == {"Marcus Webb", "Bo Niles"}

    def test_the_role_comes_through(self, db, journey):
        service = a_service(db, journey, headcount=100)
        person = a_person(db, journey)
        assign(db, journey, service, person, role="Sound")

        assert weeks_for(journey.id)[0].servers[0].role == "Sound"

    def test_another_churchs_sundays_are_not_on_it(self, db, journey):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        a_service(db, journey, headcount=100)
        a_service(db, riverbend, headcount=999, name="Riverbend")

        assert [w.headcount for w in weeks_for(journey.id)] == [100]

    def test_another_churchs_volunteers_are_not_on_it(self, db, journey):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        service = a_service(db, journey, headcount=100)
        theirs = Person(church_id=riverbend.id, first_name="Jo", last_name="Alder",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.commit()

        # Same service id space. The assignment query has to filter on the
        # church as well as on the service.
        track = db.session.scalar(
            db.select(ServiceTrack).where(ServiceTrack.service_id == service.id)
        )
        db.session.add(ServiceAssignment(
            church_id=riverbend.id, service_id=service.id, track_id=track.id,
            person_id=theirs.id, status=ACCEPTED,
        ))
        db.session.commit()

        assert weeks_for(journey.id)[0].servers == ()

    def test_the_limit_takes_the_most_recent(self, db, journey):
        """"The last 10" must mean the most recent ten, not the oldest ten
        this church ever held."""
        for weeks_back in range(1, 15):
            a_service(db, journey, days_ago=7 * weeks_back, headcount=weeks_back)

        got = weeks_for(journey.id, limit=10)
        assert len(got) == 10
        assert [w.headcount for w in got] == list(range(10, 0, -1))


class TestGrowthAndDecline:
    def test_the_change_is_against_the_week_before(self, db, journey):
        for days, count in ((21, 100), (14, 110), (7, 95)):
            a_service(db, journey, days_ago=days, headcount=count)

        points = trend(weeks_for(journey.id))
        assert [p.change for p in points] == [None, 10, -15]

    def test_the_first_week_has_nothing_to_compare_against(self, db, journey):
        a_service(db, journey, days_ago=7, headcount=100)
        assert trend(weeks_for(journey.id))[0].change is None

    def test_an_uncounted_week_is_skipped_not_treated_as_zero(self, db, journey):
        """A Sunday nobody counted is a Sunday nobody counted.

        Read as nought it prints a collapse and a recovery that never
        happened, and the week after it gets compared against zero.
        """
        for days, count in ((21, 100), (14, None), (7, 110)):
            a_service(db, journey, days_ago=days, headcount=count)

        points = trend(weeks_for(journey.id))
        assert points[1].change is None, "the uncounted week has no change"
        assert points[2].change == 10, "and the week after it skips back to 100"

    def test_a_week_of_nobody_does_not_produce_an_infinite_increase(self, db,
                                                                    journey):
        """Churches do hold services nobody attends, usually in a snow storm,
        and "inf%" would be the only thing anybody remembered about it."""
        for days, count in ((14, 0), (7, 80)):
            a_service(db, journey, days_ago=days, headcount=count)

        points = trend(weeks_for(journey.id))
        assert points[1].change == 80
        assert points[1].percent is None

    def test_the_average_needs_four_counted_weeks(self, db, journey):
        for weeks_back in range(1, 6):
            a_service(db, journey, days_ago=7 * weeks_back, headcount=100)

        points = trend(weeks_for(journey.id))
        assert [p.average for p in points[:3]] == [None, None, None]
        assert points[3].average == 100

    def test_growth_and_decline_are_named(self, db, journey):
        for days, count in ((14, 100), (7, 120)):
            a_service(db, journey, days_ago=days, headcount=count)
        points = trend(weeks_for(journey.id))

        assert points[1].is_growth and not points[1].is_decline


class TestTheHeadlineNumbers:
    def test_the_last_four_weeks_against_the_four_before(self, db, journey):
        """Against the previous four, not the first four on record.

        Twelve weeks rather than eight, deliberately. With exactly eight the
        previous four and the first four are the same slice, and a version
        comparing against the start of the year passes a test built that
        way while telling a church with a good January that a bad November
        is growth.
        """
        counts = [10, 10, 10, 10, 100, 100, 100, 100, 110, 110, 110, 110]
        for index, count in enumerate(counts):
            a_service(db, journey, days_ago=7 * (len(counts) - index),
                      headcount=count)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["recent_average"] == 110
        assert totals["earlier_average"] == 100, "not the 10s at the start"
        assert totals["change"] == 10
        assert round(totals["percent"], 1) == 10.0

    def test_last_sunday_is_the_most_recent_one(self, db, journey):
        """The tile says "Last Sunday". The oldest Sunday on record is the
        one wrong answer nobody would question, because it is a plausible
        number sitting under a plausible label."""
        for days, count in ((21, 70), (14, 80), (7, 118)):
            a_service(db, journey, days_ago=days, headcount=count)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["latest"] == 118

    def test_last_sunday_skips_a_week_nobody_counted(self, db, journey):
        """A church that forgot to count last Sunday should see the Sunday
        before it, not a blank where the headline number goes."""
        a_service(db, journey, days_ago=14, headcount=96)
        a_service(db, journey, days_ago=7, headcount=None)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["latest"] == 96

    def test_it_says_how_many_sundays_were_counted(self, db, journey):
        a_service(db, journey, days_ago=14, headcount=100)
        a_service(db, journey, days_ago=7, headcount=None)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["weeks"] == 2
        assert totals["counted"] == 1

    def test_nothing_counted_is_not_an_error(self, db, journey):
        """A church that has not entered a head count yet opens this page
        too, and a crash is a worse answer than an empty one."""
        a_service(db, journey, days_ago=7, headcount=None)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["average"] is None
        assert totals["counted"] == 0

    def test_the_best_sunday_is_the_biggest_one(self, db, journey):
        for days, count in ((21, 100), (14, 150), (7, 120)):
            a_service(db, journey, days_ago=days, headcount=count)

        totals = summary(trend(weeks_for(journey.id)))
        assert totals["best"].week.headcount == 150


class TestTheAttendanceScreen:
    def test_the_date_looks_like_the_link_it_is(self):
        """It opens that Sunday's plan.

        It carried a bare `class="link"`, and there is no general `.link`
        class in this stylesheet, so it rendered as plain black text in a
        table of plain black text. Found later by measuring the computed
        colour in a browser, which is the only way a silent one shows up.
        Pinned structurally because a stylesheet has no behaviour a request
        can assert.
        """
        css = open("app/static/css/app.css").read()
        start = css.index(".attend tbody a{")
        assert "color:var(--accent)" in css[start:start + 200]
        assert "text-decoration:underline" in css[start:start + 200]

    def test_a_leader_can_open_it(self, db, journey, client, sign_in):
        a_service(db, journey, days_ago=7, headcount=118)
        sign_in("leader@journeychurchsemo.com")

        page = client.get("/services/attendance/", headers=H).get_data(as_text=True)
        assert "118" in page

    def test_last_sunday_is_at_the_top(self, db, journey, client, sign_in):
        a_service(db, journey, days_ago=14, headcount=90, name="Older")
        a_service(db, journey, days_ago=7, headcount=118, name="Newer")

        sign_in("leader@journeychurchsemo.com")
        page = client.get("/services/attendance/", headers=H).get_data(as_text=True)
        assert page.index("Newer") < page.index("Older")

    def test_an_uncounted_week_says_so_rather_than_showing_nought(self, db,
                                                                  journey,
                                                                  client,
                                                                  sign_in):
        a_service(db, journey, days_ago=7, headcount=None)
        sign_in("leader@journeychurchsemo.com")

        page = client.get("/services/attendance/", headers=H).get_data(as_text=True)
        assert SERVICES["attendance_no_count"] in page

    def test_a_church_with_no_services_gets_told_why(self, db, journey, client,
                                                     sign_in):
        sign_in("leader@journeychurchsemo.com")
        page = client.get("/services/attendance/", headers=H).get_data(as_text=True)
        assert SERVICES["attendance_empty"] in page

    def test_the_dashboard_number_opens_it(self, db, journey, client, sign_in):
        a_service(db, journey, days_ago=7, headcount=118)
        sign_in("leader@journeychurchsemo.com")

        page = client.get("/", headers=H).get_data(as_text=True)
        assert "/services/attendance/" in page

    def test_the_window_cannot_be_widened_without_limit(self, db, journey,
                                                        client, sign_in):
        """A hand-edited URL must not ask for every Sunday since the church
        was planted on one page."""
        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/?weeks=100000", headers=H)
        assert response.status_code == 200

    def test_a_member_cannot_open_it(self, db, journey, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        assert client.get("/services/attendance/", headers=H).status_code == 403


class TestTheServicesReport:
    def test_a_leader_can_download_it(self, db, journey, client, sign_in):
        service = a_service(db, journey, days_ago=7, headcount=118)
        assign(db, journey, service, a_person(db, journey), role="Sound")

        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)

        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data[:5] == b"%PDF-"

    def test_it_is_not_left_in_a_shared_browsers_cache(self, db, journey, client,
                                                       sign_in):
        a_service(db, journey, days_ago=7, headcount=118)
        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)
        assert "no-store" in response.headers["Cache-Control"]

    def test_a_member_cannot_download_it(self, db, journey, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)
        assert response.status_code == 403

    def test_what_is_actually_in_it(self, db, journey, client, sign_in):
        from io import BytesIO

        from pypdf import PdfReader

        service = a_service(db, journey, days_ago=7, headcount=118)
        assign(db, journey, service, a_person(db, journey), role="Sound")

        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)

        text = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(response.data)).pages
        )
        assert "Marcus Webb" in text
        assert "Sound" in text
        assert "118 people" in text
        assert journey.name in text

    def test_an_uncounted_sunday_says_so_rather_than_printing_nought(self, db,
                                                                     journey,
                                                                     client,
                                                                     sign_in):
        from io import BytesIO

        from pypdf import PdfReader

        a_service(db, journey, days_ago=7, headcount=None)
        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)

        text = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(response.data)).pages
        )
        assert "no count taken" in text

    def test_a_sunday_nobody_was_scheduled_for_says_so(self, db, journey, client,
                                                       sign_in):
        from io import BytesIO

        from pypdf import PdfReader

        a_service(db, journey, days_ago=7, headcount=40)
        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)

        text = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(response.data)).pages
        )
        assert "Nobody was on the schedule" in text

    def test_it_explains_who_counts_as_a_volunteer(self, db, journey, client,
                                                   sign_in):
        """Every column on this sheet is a judgement about what counts, and a
        reader deciding anything from it deserves to know which ones."""
        from io import BytesIO

        from pypdf import PdfReader

        a_service(db, journey, days_ago=7, headcount=40)
        sign_in("leader@journeychurchsemo.com")
        response = client.get("/services/attendance/report.pdf", headers=H)

        text = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(response.data)).pages
        )
        assert "did not decline" in text


class TestItDoesNotAskOncePerSunday:
    def test_a_year_of_sundays_is_a_handful_of_queries(self, db, journey):
        """Fifty-two services, and a loop asking each one for its volunteers
        is a hundred queries behind one PDF."""
        from sqlalchemy import event

        people = []
        for index in range(6):
            person = Person(church_id=journey.id, first_name=f"P{index}",
                            last_name="X", stage="member", approved_at=utcnow())
            db.session.add(person)
            people.append(person)
        db.session.commit()

        for weeks_back in range(1, 53):
            service = a_service(db, journey, days_ago=7 * weeks_back,
                                headcount=100 + weeks_back)
            for person in people:
                assign(db, journey, service, person)

        engine = db.session.get_bind()

        def measure(limit):
            counted = []

            def count(conn, cursor, statement, params, context, many):
                counted.append(statement)

            event.listen(engine, "before_cursor_execute", count)
            try:
                weeks = weeks_for(journey.id, limit=limit)
            finally:
                event.remove(engine, "before_cursor_execute", count)
            return weeks, len(counted)

        # Warm up first. The very first call in a session also loads the
        # church row, which belongs to the fixture rather than to this
        # function and would otherwise show up as a difference between the
        # two measurements.
        weeks_for(journey.id, limit=1)

        few, few_queries = measure(4)
        many, many_queries = measure(52)

        assert len(few) == 4 and len(many) == 52
        assert sum(len(w.servers) for w in many) == 52 * 6

        # The property that matters is that the count does not grow with the
        # roster, not what the count happens to be: an unrelated lazy load
        # elsewhere in the request would make a fixed number brittle and
        # would say nothing about this function.
        assert few_queries == many_queries, (
            f"{few_queries} queries for 4 Sundays and {many_queries} for 52"
        )
        assert many_queries <= 3
