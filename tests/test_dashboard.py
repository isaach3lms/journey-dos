"""The staff dashboard, laid out like the approved demo, on real numbers."""

from datetime import date, datetime, timedelta

import pytest

from app import dashboard
from app.models import Church, ContactLog, OutboxMessage, Person, SequenceEnrollment
from app.models.base import utcnow
from app.models.contact import NextStep
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
                     b"people flagged as stuck too long", b"next steps taken in the last 7 days"):
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
        old = utcnow() - timedelta(days=200)
        person(db, "guest", stage_since=old, last_contact_at=old)
        person(db, "guest", stage_since=old, last_contact_at=old)
        person(db, "attender", stage_since=old, last_contact_at=old)
        person(db, "member", stage_since=old, last_contact_at=old)  # destinations never stick
        db.session.commit()
        assert dashboard.stuck_by_stage(journey(db).id) == {"guest": 2, "attender": 1}

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
        assert rows["Guest follow up"]["sent"] == 0

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
