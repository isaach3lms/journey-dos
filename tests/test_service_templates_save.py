"""Saving a service's running order as a template for future services."""

from datetime import timedelta

import pytest

from app.models import (
    Church, Service, ServiceItem, ServiceNeed, ServiceTemplateItem, ServiceType,
    ServiceTypeNeed, Song, Team, TeamPosition, build_from_type,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def service(db):
    c = journey(db)
    song = Song(church_id=c.id, title="Goodness of God")
    team = Team(church_id=c.id, name="Band")
    db.session.add_all([song, team])
    db.session.flush()
    vocals = TeamPosition(church_id=c.id, team_id=team.id, name="Vocals")
    db.session.add(vocals)
    db.session.flush()
    s = Service(church_id=c.id, name="Sunday 10:30", starts_at=utcnow() + timedelta(days=1))
    db.session.add(s)
    db.session.flush()
    for i, (kind, title, minutes, song_id) in enumerate((
        ("header", "Worship", None, None),
        ("song", "Goodness of God", 5, song.id),
        ("element", "Welcome", 3, None),
        ("element", "Message", 35, None),
    ), start=1):
        db.session.add(ServiceItem(church_id=c.id, service_id=s.id, position=i, kind=kind,
                                   title=title, minutes=minutes, song_id=song_id, notes=f"note {i}"))
    db.session.add(ServiceNeed(church_id=c.id, service_id=s.id, position_id=vocals.id,
                               position_name="Vocals", wanted=2))
    db.session.commit()
    return s


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def template_titles(t):
    return [i.title for i in sorted(t.template_items, key=lambda i: i.position)]


class TestSaving:
    def test_save_as_a_new_template(self, staff, db, service):
        r = staff.post(f"/services/{service.id}/save-template/", headers=H, follow_redirects=True,
                       data={"target": "new", "name": "Sunday 10:30", "include_needs": "on"})
        assert b"saved as a template with 4 items" in r.data
        t = db.session.scalar(db.select(ServiceType).where(ServiceType.name == "Sunday 10:30"))
        assert template_titles(t) == ["Worship", "Goodness of God", "Welcome", "Message"]
        items = sorted(t.template_items, key=lambda i: i.position)
        assert items[0].kind == "header" and items[1].song_id is not None and items[3].minutes == 35
        assert items[2].notes == "note 3"
        assert [(n.position.name, n.wanted) for n in t.needs] == [("Vocals", 2)]
        assert t.default_minutes == 43

    def test_the_next_service_starts_from_it(self, staff, db, service):
        staff.post(f"/services/{service.id}/save-template/", headers=H,
                   data={"target": "new", "name": "Sunday", "include_needs": "on"})
        t = db.session.scalar(db.select(ServiceType).where(ServiceType.name == "Sunday"))
        nxt = build_from_type(journey(db).id, t, name="Sunday", starts_at=utcnow() + timedelta(days=8))
        db.session.commit()
        assert [i.title for i in sorted(nxt.items, key=lambda i: i.position)] == [
            "Worship", "Goodness of God", "Welcome", "Message"]

    def test_replace_an_existing_template(self, staff, db, service):
        c = journey(db)
        old = ServiceType(church_id=c.id, name="Sunday")
        db.session.add(old)
        db.session.flush()
        db.session.add(ServiceTemplateItem(church_id=c.id, service_type_id=old.id, position=1, title="Old item"))
        db.session.commit()
        r = staff.post(f"/services/{service.id}/save-template/", headers=H, follow_redirects=True,
                       data={"target": str(old.id)})
        assert b"now uses this running order" in r.data
        db.session.refresh(old)
        assert template_titles(old) == ["Worship", "Goodness of God", "Welcome", "Message"]
        # Roles box unticked: the template's existing roles are left alone.
        assert old.needs == []

    def test_later_edits_do_not_change_the_template(self, staff, db, service):
        staff.post(f"/services/{service.id}/save-template/", headers=H, data={"target": "new", "name": "T"})
        service.items[0].title = "Changed after"
        db.session.commit()
        t = db.session.scalar(db.select(ServiceType).where(ServiceType.name == "T"))
        assert "Changed after" not in template_titles(t)

    def test_a_duplicate_name_is_refused(self, staff, db, service):
        db.session.add(ServiceType(church_id=journey(db).id, name="Sunday"))
        db.session.commit()
        r = staff.post(f"/services/{service.id}/save-template/", headers=H, follow_redirects=True,
                       data={"target": "new", "name": "sunday"})
        assert b"already a template called Sunday" in r.data

    def test_an_empty_plan_is_refused(self, staff, db):
        s = Service(church_id=journey(db).id, name="Empty", starts_at=utcnow())
        db.session.add(s)
        db.session.commit()
        r = staff.post(f"/services/{s.id}/save-template/", headers=H, follow_redirects=True,
                       data={"target": "new", "name": "Empty"})
        assert b"Add items to the running order" in r.data
        assert db.session.scalar(db.select(ServiceType)) is None

    def test_a_name_is_required(self, staff, db, service):
        staff.post(f"/services/{service.id}/save-template/", headers=H, data={"target": "new", "name": " "})
        assert db.session.scalar(db.select(ServiceType)) is None

    def test_the_card_is_on_the_plan(self, staff, db, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"Save as a template" in page and b'id="template"' in page


class TestBoundaries:
    def test_members_cannot(self, client, sign_in, db, service):
        sign_in("member@journeychurchsemo.com")
        r = client.post(f"/services/{service.id}/save-template/", headers=H, data={"target": "new", "name": "X"})
        assert r.status_code in (302, 403, 404)
        assert db.session.scalar(db.select(ServiceType)) is None

    def test_another_churchs_template_cannot_be_overwritten(self, staff, db, service):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = ServiceType(church_id=other.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()
        r = staff.post(f"/services/{service.id}/save-template/", headers=H, data={"target": str(theirs.id)})
        assert r.status_code == 404
        db.session.refresh(theirs)
        assert theirs.template_items == []
