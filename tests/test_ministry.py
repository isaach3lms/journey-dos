from datetime import timedelta

import pytest

from app import create_app
from app.extensions import db, utcnow
from app.ministry import (
    Announcement,
    CheckIn,
    Child,
    Group,
    GroupMembership,
    Household,
    Service,
    ServiceAssignment,
    ServiceElement,
    Team,
    TeamMembership,
    announcements_for,
    audience_query,
)
from app.models import Person, Stage
from app.seed import seed


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("KIOSK_PIN", "4242")
    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, KIOSK_PIN="4242")
    with application.app_context():
        db.create_all()
        seed()
        yield application
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def church_id(app):
    return Stage.query.first().church_id


def person(church_id, email="staffer@example.com", role="admin", stage_index=0):
    stages = Stage.query.order_by(Stage.position).all()
    record = Person(
        church_id=church_id,
        first_name="Casey",
        last_name="Reed",
        email=email,
        role=role,
        source="seed",
    )
    db.session.add(record)
    db.session.flush()
    record.move_to_stage(stages[stage_index])
    record.set_password("passw0rd123")
    db.session.commit()
    return record


def sign_in(client, email="staffer@example.com"):
    return client.post(
        "/account/login", data={"email": email, "password": "passw0rd123"}, follow_redirects=True
    )


def a_service(church_id, hours_from_now=2):
    service = Service(
        church_id=church_id,
        name="Sunday Gathering",
        starts_at=utcnow() + timedelta(hours=hours_from_now),
    )
    db.session.add(service)
    db.session.commit()
    return service


def a_child(church_id, phone="573-555-0142", allergies=None):
    household = Household(church_id=church_id, name="The Webb family", phone=phone)
    db.session.add(household)
    db.session.flush()
    child = Child(
        church_id=church_id,
        household_id=household.id,
        first_name="Ada",
        last_name="Webb",
        room="Kids",
        allergies=allergies,
    )
    db.session.add(child)
    db.session.commit()
    return household, child


# --- services ---------------------------------------------------------------


def test_creating_a_service_seeds_a_run_sheet(app, client, church_id):
    person(church_id)
    sign_in(client)
    client.post(
        "/staff/services",
        data={"name": "Launch Sunday", "date": "2026-10-12", "time": "10:00"},
        follow_redirects=True,
    )
    service = Service.query.filter_by(name="Launch Sunday").one()
    assert len(service.elements) == 7
    assert service.total_minutes == 70


def test_run_sheet_times_recompute_from_the_start(app, church_id):
    service = a_service(church_id)
    service.starts_at = service.starts_at.replace(hour=15, minute=0)  # 10:00 am Central
    for position, minutes in enumerate([5, 20, 10]):
        db.session.add(
            ServiceElement(
                church_id=church_id,
                service_id=service.id,
                position=position,
                title=f"Item {position}",
                minutes=minutes,
            )
        )
    db.session.commit()
    rows = service.running_times("America/Chicago")
    assert [clock for _, clock in rows] == ["10:00 am", "10:05 am", "10:25 am"]
    assert service.total_minutes == 35


def test_scheduling_someone_moves_them_to_serving(app, client, church_id):
    staffer = person(church_id)
    volunteer = person(church_id, email="vol@example.com", role="member", stage_index=1)
    service = a_service(church_id)
    sign_in(client)
    client.post(
        f"/staff/services/{service.id}",
        data={"action": "assign", "person_id": volunteer.id, "role": "Greeter"},
        follow_redirects=True,
    )
    db.session.refresh(volunteer)
    assert volunteer.stage.name == "Serving"
    assert ServiceAssignment.query.count() == 1
    assert staffer.stage.name != "Serving"


def test_volunteer_can_confirm_their_own_assignment(app, client, church_id):
    volunteer = person(church_id, email="vol@example.com", role="member")
    service = a_service(church_id)
    assignment = ServiceAssignment(
        church_id=church_id, service_id=service.id, person_id=volunteer.id, role="Greeter"
    )
    db.session.add(assignment)
    db.session.commit()

    sign_in(client, "vol@example.com")
    client.post(
        "/app/serving",
        data={"assignment_id": assignment.id, "answer": "confirmed"},
        follow_redirects=True,
    )
    db.session.refresh(assignment)
    assert assignment.status == "confirmed"
    assert assignment.responded_at is not None


# --- kids check in ----------------------------------------------------------


def test_kiosk_requires_the_pin(client):
    assert client.get("/kiosk/", follow_redirects=False).status_code == 302
    client.post("/kiosk/unlock", data={"pin": "0000"})
    assert client.get("/kiosk/", follow_redirects=False).status_code == 302
    client.post("/kiosk/unlock", data={"pin": "4242"})
    assert client.get("/kiosk/").status_code == 200


def test_lookup_by_last_four_finds_the_household(app, client, church_id):
    a_child(church_id, phone="573-555-0142")
    client.post("/kiosk/unlock", data={"pin": "4242"})
    response = client.post("/kiosk/", data={"action": "lookup", "phone": "0142"})
    assert b"The Webb family" in response.data
    miss = client.post("/kiosk/", data={"action": "lookup", "phone": "9999"})
    assert b"could not find" in miss.data


def test_check_in_issues_one_code_for_the_household(app, client, church_id):
    household, child = a_child(church_id)
    sibling = Child(
        church_id=church_id, household_id=household.id, first_name="Leo", last_name="Webb"
    )
    db.session.add(sibling)
    db.session.commit()
    service = a_service(church_id, hours_from_now=1)

    client.post("/kiosk/unlock", data={"pin": "4242"})
    client.post(
        f"/kiosk/check-in/{household.id}", data={"child_id": [str(child.id), str(sibling.id)]}
    )
    records = CheckIn.query.all()
    assert len(records) == 2
    assert len({r.code for r in records}) == 1
    assert all(r.service_id == service.id for r in records)


def test_wrong_code_does_not_release_a_child(app, client, church_id):
    household, child = a_child(church_id)
    client.post("/kiosk/unlock", data={"pin": "4242"})
    client.post(f"/kiosk/check-in/{household.id}", data={"child_id": str(child.id)})
    record = CheckIn.query.one()

    wrong = "999" if record.code != "999" else "111"
    response = client.post(
        "/kiosk/check-out",
        data={"action": "release", "checkin_id": record.id, "code": wrong},
        follow_redirects=True,
    )
    db.session.refresh(record)
    assert record.checked_out_at is None
    assert b"does not match" in response.data

    client.post(
        "/kiosk/check-out",
        data={"action": "release", "checkin_id": record.id, "code": record.code},
        follow_redirects=True,
    )
    db.session.refresh(record)
    assert record.checked_out_at is not None
    assert record.checked_out_by.endswith(record.code)


def test_allergies_show_on_the_kiosk(app, client, church_id):
    household, _ = a_child(church_id, allergies="Peanuts")
    client.post("/kiosk/unlock", data={"pin": "4242"})
    response = client.post("/kiosk/", data={"action": "lookup", "phone": "0142"})
    assert b"Peanuts" in response.data


def test_kids_team_flags_missing_background_check(app, client, church_id):
    person(church_id)
    volunteer = person(church_id, email="vol@example.com", role="member")
    kids_team = Team.query.filter_by(church_id=church_id, name="Kids").one()
    membership = TeamMembership(
        church_id=church_id, team_id=kids_team.id, person_id=volunteer.id
    )
    db.session.add(membership)
    db.session.commit()
    assert membership.needs_clearance is True

    sign_in(client)
    response = client.get("/staff/kids")
    assert b"without a background check" in response.data

    membership.cleared_at = utcnow()
    db.session.commit()
    assert membership.needs_clearance is False


# --- groups -----------------------------------------------------------------


def test_join_and_leave_a_group(app, client, church_id):
    member = person(church_id, email="member@example.com", role="member")
    group = Group(church_id=church_id, name="Tuesday Men", capacity=4)
    db.session.add(group)
    db.session.commit()

    sign_in(client, "member@example.com")
    client.post("/app/groups", data={"group_id": group.id, "action": "join"}, follow_redirects=True)
    assert group.member_count == 1

    client.post("/app/groups", data={"group_id": group.id, "action": "leave"}, follow_redirects=True)
    assert group.member_count == 0
    assert GroupMembership.query.count() == 1  # history kept, status changed


def test_a_full_group_refuses_new_members(app, client, church_id):
    member = person(church_id, email="member@example.com", role="member")
    group = Group(church_id=church_id, name="Small", capacity=1)
    db.session.add(group)
    db.session.flush()
    db.session.add(
        GroupMembership(church_id=church_id, group_id=group.id, person_id=person(church_id).id)
    )
    db.session.commit()
    assert group.has_room is False

    sign_in(client, "member@example.com")
    response = client.post(
        "/app/groups", data={"group_id": group.id, "action": "join"}, follow_redirects=True
    )
    assert b"is full" in response.data
    assert group.member_count == 1


# --- messaging --------------------------------------------------------------


def test_audience_targets_the_right_people(app, church_id):
    stages = Stage.query.order_by(Stage.position).all()
    interested = person(church_id, email="a@example.com", role="member", stage_index=0)
    launch = person(church_id, email="b@example.com", role="member", stage_index=2)

    everyone = {p.email for p in audience_query(church_id, "everyone")}
    assert {"a@example.com", "b@example.com"} <= everyone

    launch_team = {p.email for p in audience_query(church_id, "launch_team")}
    assert "b@example.com" in launch_team
    assert "a@example.com" not in launch_team

    assert audience_query(church_id, "serving") == []


def test_announcement_is_only_visible_to_its_audience(app, church_id):
    interested = person(church_id, email="a@example.com", role="member", stage_index=0)
    launch = person(church_id, email="b@example.com", role="member", stage_index=2)
    db.session.add(
        Announcement(
            church_id=church_id,
            title="Launch team huddle",
            body="Saturday at 9.",
            audience="launch_team",
        )
    )
    db.session.commit()

    assert [a.title for a in announcements_for(launch)] == ["Launch team huddle"]
    assert announcements_for(interested) == []


def test_posting_without_email_does_not_send(app, client, church_id):
    person(church_id)
    sign_in(client)
    client.post(
        "/staff/messages",
        data={"action": "post", "title": "Doors open", "body": "October 12.", "audience": "everyone"},
        follow_redirects=True,
    )
    announcement = Announcement.query.one()
    assert announcement.emailed_at is None
    assert announcement.email_count == 0
