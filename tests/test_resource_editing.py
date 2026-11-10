"""Resources stay editable after publishing, and the library matches the demo.

Before: once a plan was created, its title, type, and description could never
change, and a day could only be deleted and re-added, which also wiped every
member's tick on it. Now everything edits in place, live or draft.
"""

import pytest

from app.models import Church, Person, Resource, ResourceSession, SessionCompletion
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def plan(db):
    c = journey(db)
    r = Resource(church_id=c.id, title="Known", summary="Five days", kind="reading_plan", cover=2)
    db.session.add(r)
    db.session.flush()
    for i, t in enumerate(("Searched", "Nowhere to hide", "Formed"), start=1):
        db.session.add(ResourceSession(church_id=c.id, resource_id=r.id, position=i, title=t,
                                       passage_ref=f"Psalm 139:{i}", minutes=6))
    db.session.flush()
    r.publish()
    db.session.commit()
    return r


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def titles(plan):
    return [s.title for s in sorted(plan.sessions, key=lambda s: s.position)]


class TestEditingALivePlan:
    def test_details_change_while_published(self, staff, db, plan):
        r = staff.post(f"/resources/{plan.id}/details/", headers=H, follow_redirects=True, data={
            "title": "Known: a 5 day reading plan", "kind": "study",
            "summary": "Companion plan for the Known series.", "cover": "4",
        })
        assert b"Members see the change now" in r.data
        db.session.refresh(plan)
        assert plan.is_published
        assert (plan.title, plan.kind, plan.cover) == ("Known: a 5 day reading plan", "study", 4)
        assert plan.summary == "Companion plan for the Known series."

    def test_a_session_edits_in_place_and_keeps_progress(self, staff, db, plan):
        day2 = plan.sessions[1]
        person = Person(church_id=plan.church_id, first_name="A", last_name="R", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        SessionCompletion.mark(plan.church_id, person.id, day2)
        db.session.commit()

        staff.post(f"/resources/{plan.id}/sessions/{day2.id}/", headers=H, data={
            "title": "Day 2: Nowhere to hide", "passage_ref": "Psalm 139:7-12",
            "minutes": "7", "body": "New words", "question": "Where?",
        })
        db.session.refresh(day2)
        assert (day2.title, day2.passage_ref, day2.minutes, day2.body) == (
            "Day 2: Nowhere to hide", "Psalm 139:7-12", 7, "New words")
        assert SessionCompletion.completed_session_ids(plan.church_id, person.id, plan.id) == {day2.id}

    def test_a_blank_session_title_is_refused(self, staff, db, plan):
        day1 = plan.sessions[0]
        staff.post(f"/resources/{plan.id}/sessions/{day1.id}/", headers=H, data={"title": "  "})
        db.session.refresh(day1)
        assert day1.title == "Searched"

    def test_silly_minutes_are_dropped(self, staff, db, plan):
        day1 = plan.sessions[0]
        staff.post(f"/resources/{plan.id}/sessions/{day1.id}/", headers=H,
                   data={"title": "Searched", "minutes": "9999"})
        db.session.refresh(day1)
        assert day1.minutes is None

    def test_add_a_session_to_a_live_plan(self, staff, db, plan):
        r = staff.post(f"/resources/{plan.id}/sessions/", headers=H, follow_redirects=True,
                       data={"title": "Day 4", "minutes": "5"})
        assert b"Members see the new session now" in r.data
        db.session.refresh(plan)
        assert titles(plan)[-1] == "Day 4"
        assert plan.sessions[-1].minutes == 5

    def test_members_see_edits_immediately(self, client, sign_in, db, plan):
        from app.models import User

        user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
        person = Person(church_id=plan.church_id, first_name="A", last_name="R",
                        email=user.email, approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        plan.title = "Renamed while live"
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        assert b"Renamed while live" in client.get("/me/read/", headers=H).data


class TestOrder:
    def test_move_down_and_up(self, staff, db, plan):
        first = plan.sessions[0]
        staff.post(f"/resources/{plan.id}/sessions/{first.id}/move/", headers=H, data={"direction": "down"})
        db.session.expire_all()
        assert titles(plan) == ["Nowhere to hide", "Searched", "Formed"]
        staff.post(f"/resources/{plan.id}/sessions/{first.id}/move/", headers=H, data={"direction": "up"})
        db.session.expire_all()
        assert titles(plan) == ["Searched", "Nowhere to hide", "Formed"]

    def test_moving_past_the_end_is_harmless(self, staff, db, plan):
        last = plan.sessions[-1]
        staff.post(f"/resources/{plan.id}/sessions/{last.id}/move/", headers=H, data={"direction": "down"})
        db.session.expire_all()
        assert titles(plan) == ["Searched", "Nowhere to hide", "Formed"]

    def test_delete_renumbers(self, staff, db, plan):
        middle = plan.sessions[1]
        staff.post(f"/resources/{plan.id}/sessions/{middle.id}/delete/", headers=H)
        db.session.expire_all()
        assert [s.position for s in sorted(plan.sessions, key=lambda s: s.position)] == [1, 2]

    def test_the_last_session_of_a_live_plan_cannot_go(self, staff, db):
        c = journey(db)
        r = Resource(church_id=c.id, title="Solo", kind="course")
        db.session.add(r)
        db.session.flush()
        only = ResourceSession(church_id=c.id, resource_id=r.id, position=1, title="Only")
        db.session.add(only)
        db.session.flush()
        r.publish()
        db.session.commit()
        resp = staff.post(f"/resources/{r.id}/sessions/{only.id}/delete/", headers=H, follow_redirects=True)
        assert b"only session in a live plan" in resp.data
        db.session.expire_all()
        assert r.session_count == 1


class TestArchiveAndRestore:
    def test_restore_brings_it_back_as_a_draft(self, staff, db, plan):
        staff.post(f"/resources/{plan.id}/archive/", headers=H)
        page = staff.get("/resources/", headers=H)
        assert b"Archived" in page.data and b"Restore" in page.data
        staff.post(f"/resources/{plan.id}/restore/", headers=H)
        db.session.refresh(plan)
        assert plan.status == "draft"


class TestLibraryLayout:
    def test_cards_match_the_demo(self, staff, db, plan):
        page = staff.get("/resources/", headers=H).data
        for text in (b"Published to your people", b"Add a resource", b"Search", b"Edit plan",
                     b"Click card to open", b"3 sessions", b"Reading plan", b"cover-2"):
            assert text in page, text

    def test_every_card_has_its_plan_overview(self, staff, db, plan):
        page = staff.get("/resources/", headers=H).data
        assert f'id="plan-{plan.id}"'.encode() in page
        assert b"Psalm 139:1" in page and b"6 min" in page
        assert b"Open in member app" in page

    def test_editor_shows_live_banner(self, staff, db, plan):
        page = staff.get(f"/resources/{plan.id}/", headers=H).data
        assert b"Members see every change the moment you save" in page
        assert b"Move up" in page and b"Save changes" in page

    def test_create_with_a_cover(self, staff, db):
        staff.post("/resources/", headers=H, data={"title": "Baptism", "kind": "course", "cover": "5"})
        r = db.session.scalar(db.select(Resource).where(Resource.title == "Baptism"))
        assert r.cover == 5 and r.status == "draft"

    def test_bad_cover_falls_back(self, staff, db):
        staff.post("/resources/", headers=H, data={"title": "X", "kind": "course", "cover": "99"})
        assert db.session.scalar(db.select(Resource).where(Resource.title == "X")).cover == 0


class TestBoundaries:
    def test_members_cannot_edit(self, client, sign_in, db, plan):
        sign_in("member@journeychurchsemo.com")
        r = client.post(f"/resources/{plan.id}/details/", headers=H, data={"title": "Hacked", "kind": "study"})
        assert r.status_code in (302, 403, 404)
        db.session.refresh(plan)
        assert plan.title == "Known"

    def test_another_churchs_session_is_a_404(self, staff, db, plan):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Resource(church_id=other.id, title="Theirs", kind="course")
        db.session.add(theirs)
        db.session.flush()
        s = ResourceSession(church_id=other.id, resource_id=theirs.id, position=1, title="T")
        db.session.add(s)
        db.session.commit()
        assert staff.post(f"/resources/{theirs.id}/sessions/{s.id}/", headers=H,
                          data={"title": "x"}).status_code == 404
        assert staff.post(f"/resources/{plan.id}/sessions/{s.id}/", headers=H,
                          data={"title": "x"}).status_code == 404
