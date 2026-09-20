"""Services move between draft and published."""

from datetime import timedelta

import pytest

from app.models import Church, Person, Service, ServiceAssignment, ServiceItem, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def service(db):
    s = Service(church_id=journey(db).id, name="Sunday", starts_at=utcnow() + timedelta(days=2))
    db.session.add(s)
    db.session.flush()
    db.session.add(ServiceItem(church_id=s.church_id, service_id=s.id, position=1, kind="song", title="Goodness of God"))
    db.session.commit()
    return s


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


class TestStaff:
    def test_new_services_start_as_drafts(self, service):
        assert service.status == "draft" and not service.is_published

    def test_publish_then_unpublish(self, staff, db, service):
        r = staff.post(f"/services/{service.id}/publish/", headers=H, follow_redirects=True)
        assert b"is published" in r.data
        db.session.refresh(service)
        assert service.is_published
        r = staff.post(f"/services/{service.id}/publish/", headers=H, follow_redirects=True)
        assert b"back to a draft" in r.data
        db.session.refresh(service)
        assert service.status == "draft"

    def test_an_empty_plan_cannot_be_published(self, staff, db):
        s = Service(church_id=journey(db).id, name="Empty", starts_at=utcnow() + timedelta(days=1))
        db.session.add(s)
        db.session.commit()
        r = staff.post(f"/services/{s.id}/publish/", headers=H, follow_redirects=True)
        assert b"before publishing" in r.data
        db.session.refresh(s)
        assert s.status == "draft"

    def test_emailing_the_plan_publishes_it(self, staff, db, service):
        p = Person(church_id=service.church_id, first_name="V", last_name="L", email="v@example.com", approved_at=utcnow())
        db.session.add(p)
        db.session.flush()
        db.session.add(ServiceAssignment(church_id=p.church_id, service_id=service.id, person_id=p.id))
        db.session.commit()
        staff.post(f"/services/{service.id}/send/", headers=H)
        db.session.refresh(service)
        assert service.is_published

    def test_the_plan_page_shows_state_and_button(self, staff, db, service):
        page = staff.get(f"/services/{service.id}/", headers=H).data
        assert b"Draft. Only staff and leaders can see this plan." in page
        assert b">\n      Publish\n" in page or b"Publish" in page

    def test_the_list_shows_the_state(self, staff, db, service):
        assert b"Draft" in staff.get("/services/", headers=H).data

    def test_members_cannot_publish(self, client, sign_in, db, service):
        sign_in("member@journeychurchsemo.com")
        r = client.post(f"/services/{service.id}/publish/", headers=H)
        assert r.status_code in (302, 403, 404)
        db.session.refresh(service)
        assert service.status == "draft"

    def test_another_churchs_service_is_a_404(self, staff, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        s = Service(church_id=other.id, name="Theirs", starts_at=utcnow())
        db.session.add(s)
        db.session.commit()
        assert staff.post(f"/services/{s.id}/publish/", headers=H).status_code == 404


class TestVolunteers:
    @pytest.fixture
    def volunteer(self, client, sign_in, db, service):
        user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
        p = Person(church_id=user.church_id, first_name="A", last_name="R", email=user.email, approved_at=utcnow())
        db.session.add(p)
        db.session.flush()
        user.person_id = p.id
        db.session.add(ServiceAssignment(church_id=p.church_id, service_id=service.id, person_id=p.id))
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        return client

    def test_a_draft_plan_is_hidden_but_the_ask_is_not(self, volunteer, db, service):
        page = volunteer.get("/me/serve/", headers=H).data
        assert b"Goodness of God" not in page
        assert b"still being put together" in page
        assert b"Sunday" in page

    def test_a_published_plan_shows(self, volunteer, db, service):
        service.publish()
        db.session.commit()
        page = volunteer.get("/me/serve/", headers=H).data
        assert b"Goodness of God" in page
