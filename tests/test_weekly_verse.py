"""Verse of the week: set by staff, shown on every member's Home tab."""

from datetime import date, timedelta

import pytest

from app.models import BibleVerse, Church, Person, User, WeeklyVerse, week_of
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def today(db):
    from app.timeutil import now_local

    return now_local(journey(db)).date()


def add(db, starts_on, reference="Hebrews 10:24", text="And let us consider how to stir up one another."):
    v = WeeklyVerse(church_id=journey(db).id, starts_on=starts_on, reference=reference, text=text)
    db.session.add(v)
    db.session.commit()
    return v


@pytest.fixture
def linked_member(client, sign_in, db):
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    p = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
               email=user.email, approved_at=utcnow())
    db.session.add(p)
    db.session.flush()
    user.person_id = p.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client


class TestWeeks:
    def test_weeks_start_on_sunday(self):
        assert week_of(date(2026, 9, 19)) == date(2026, 9, 13)   # Saturday
        assert week_of(date(2026, 9, 20)) == date(2026, 9, 20)   # Sunday
        assert week_of(date(2026, 9, 21)) == date(2026, 9, 20)   # Monday

    def test_current_is_the_newest_started_week(self, db):
        t = today(db)
        add(db, week_of(t) - timedelta(days=7), reference="Old 1:1")
        add(db, week_of(t), reference="Now 1:1")
        add(db, week_of(t) + timedelta(days=7), reference="Later 1:1")
        assert WeeklyVerse.current(journey(db).id, t).reference == "Now 1:1"

    def test_a_missed_week_keeps_last_weeks(self, db):
        t = today(db)
        add(db, week_of(t) - timedelta(days=14), reference="Two weeks ago 1:1")
        assert WeeklyVerse.current(journey(db).id, t).reference == "Two weeks ago 1:1"


class TestMemberHome:
    def test_shows_this_weeks_verse(self, linked_member, db):
        add(db, week_of(today(db)))
        page = linked_member.get("/me/", headers=H).data
        assert b"This week&#39;s verse" in page or b"This week's verse" in page
        assert b"stir up one another" in page and b"Hebrews 10:24" in page

    def test_nothing_shown_until_one_is_set(self, linked_member, db):
        page = linked_member.get("/me/", headers=H).data
        assert b"week&#39;s verse" not in page and b"week's verse" not in page

    def test_scheduled_verses_do_not_show_early(self, linked_member, db):
        add(db, week_of(today(db)) + timedelta(days=7), text="Not yet")
        assert b"Not yet" not in linked_member.get("/me/", headers=H).data

    def test_other_churches_verses_do_not_leak(self, linked_member, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(WeeklyVerse(church_id=other.id, starts_on=week_of(today(db)),
                                   reference="John 3:16", text="Riverbend verse"))
        db.session.commit()
        assert b"Riverbend verse" not in linked_member.get("/me/", headers=H).data


class TestStaffEditing:
    @pytest.fixture
    def staff(self, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        return client

    def test_set_this_weeks_verse(self, staff, db):
        r = staff.post("/resources/verse/", headers=H, follow_redirects=True, data={
            "reference": "Hebrews 10:24", "text": "And let us consider...", "translation": "ESV",
            "starts_on": today(db).isoformat(),
        })
        assert b"on every member&#39;s Home tab now" in r.data or b"on every member's Home tab now" in r.data
        v = WeeklyVerse.current(journey(db).id, today(db))
        assert (v.reference, v.translation, v.starts_on) == ("Hebrews 10:24", "ESV", week_of(today(db)))

    def test_any_day_snaps_to_its_sunday(self, staff, db):
        wednesday = week_of(today(db)) + timedelta(days=10)
        staff.post("/resources/verse/", headers=H, data={
            "reference": "Psalm 23:1", "text": "The Lord is my shepherd", "starts_on": wednesday.isoformat()})
        v = db.session.scalar(db.select(WeeklyVerse))
        assert v.starts_on == week_of(wednesday) and v.starts_on.weekday() == 6

    def test_scheduling_says_when(self, staff, db):
        next_week = week_of(today(db)) + timedelta(days=7)
        r = staff.post("/resources/verse/", headers=H, follow_redirects=True, data={
            "reference": "Psalm 23:1", "text": "x", "starts_on": next_week.isoformat()})
        assert b"is scheduled" in r.data
        assert b"Scheduled" in staff.get("/resources/", headers=H).data

    def test_same_week_replaces_instead_of_duplicating(self, staff, db):
        for text in ("first", "second"):
            staff.post("/resources/verse/", headers=H, data={
                "reference": "Psalm 23:1", "text": text, "starts_on": today(db).isoformat()})
        rows = db.session.scalars(db.select(WeeklyVerse)).all()
        assert [r.text for r in rows] == ["second"]

    def test_blank_text_fills_from_the_world_english_bible(self, staff, db):
        db.session.add(BibleVerse(book="Hebrews", chapter=10, verse=24,
                                  text="Let's consider how to provoke one another to love and good works,"))
        db.session.commit()
        staff.post("/resources/verse/", headers=H, data={"reference": "Hebrews 10:24", "text": ""})
        v = db.session.scalar(db.select(WeeklyVerse))
        assert v.text.startswith("Let's consider") and v.translation == "WEB"

    def test_blank_text_and_unknown_reference_is_refused(self, staff, db):
        r = staff.post("/resources/verse/", headers=H, follow_redirects=True,
                       data={"reference": "Hezekiah 4:1", "text": ""})
        assert b"could not find that reference" in r.data
        assert db.session.scalar(db.select(WeeklyVerse)) is None

    def test_edit_and_delete(self, staff, db):
        v = add(db, week_of(today(db)))
        staff.post(f"/resources/verse/{v.id}/", headers=H, data={
            "reference": "Hebrews 10:25", "text": "Edited", "starts_on": v.starts_on.isoformat()})
        db.session.refresh(v)
        assert (v.reference, v.text) == ("Hebrews 10:25", "Edited")
        staff.post(f"/resources/verse/{v.id}/delete/", headers=H)
        assert db.session.scalar(db.select(WeeklyVerse)) is None

    def test_moving_onto_a_taken_week_is_refused(self, staff, db):
        a = add(db, week_of(today(db)), reference="A 1:1")
        b = add(db, week_of(today(db)) + timedelta(days=7), reference="B 1:1")
        r = staff.post(f"/resources/verse/{b.id}/", headers=H, follow_redirects=True, data={
            "reference": "B 1:1", "text": "x", "starts_on": a.starts_on.isoformat()})
        assert b"already has a verse" in r.data

    def test_the_staff_card_shows(self, staff, db):
        add(db, week_of(today(db)))
        page = staff.get("/resources/", headers=H).data
        assert b"Verse of the week" in page and b"Showing now" in page

    def test_members_cannot_set_it(self, linked_member, db):
        r = linked_member.post("/resources/verse/", headers=H, data={"reference": "John 3:16", "text": "x"})
        assert r.status_code in (302, 403, 404)
        assert db.session.scalar(db.select(WeeklyVerse)) is None

    def test_another_churchs_verse_is_a_404(self, staff, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        v = WeeklyVerse(church_id=other.id, starts_on=week_of(today(db)), reference="J 1:1", text="x")
        db.session.add(v)
        db.session.commit()
        assert staff.post(f"/resources/verse/{v.id}/delete/", headers=H).status_code == 404
