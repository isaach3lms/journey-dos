"""Local demo data only. Never run against production."""
import random
from datetime import timedelta

from app import create_app
from app.extensions import db, utcnow
from app.models import Person, Stage
from app.seed import seed

SAMPLE = [
    ("Marcus", "Webb", 0, 43), ("Tyler", "Brooks", 0, 5), ("Priya", "Anand", 1, 12),
    ("Dana", "Whitfield", 1, 30), ("Kevin", "Nguyen", 2, 8), ("Rachel", "Kim", 2, 61),
    ("Alicia", "Romero", 3, 9), ("Sam", "Ortiz", 4, 15),
]

app = create_app()
with app.app_context():
    db.create_all()
    church = seed()
    stages = Stage.query.filter_by(church_id=church.id).order_by(Stage.position).all()
    for first, last, index, days in SAMPLE:
        email = f"{first.lower()}.{last.lower()}@example.com"
        if Person.query.filter_by(church_id=church.id, email=email).first():
            continue
        person = Person(
            church_id=church.id, first_name=first, last_name=last, email=email,
            stage_id=stages[index].id, stage_since=utcnow() - timedelta(days=days),
            source=random.choice(["connect card", "launch team", "manual"]),
        )
        db.session.add(person)
    admin = Person.query.filter_by(church_id=church.id, email="admin@example.com").first()
    if not admin:
        admin = Person(church_id=church.id, first_name="Dennis", last_name="Hering",
                       email="admin@example.com", role="admin", source="seed",
                       stage_id=stages[-1].id)
        db.session.add(admin)
    admin.set_password("journey1234")
    db.session.commit()

    # sample gifts so the giving view is not empty locally
    from app.importers import import_giving_csv
    sample = (
        "Transaction ID,Date,Amount,Fund,Name,Email,Payment Type,Recurring\n"
        "d-1,07/12/2026,\"$1,200.00\",General,Priya Anand,priya.anand@example.com,Card,Yes\n"
        "d-2,07/19/2026,$310.00,General,Kevin Nguyen,kevin.nguyen@example.com,Card,Yes\n"
        "d-3,07/26/2026,$150.00,Building,Dana Whitfield,dana.whitfield@example.com,Card,No\n"
        "d-4,08/02/2026,$75.00,General,Walk In,,Cash,No\n"
    )
    import_giving_csv(sample.encode(), church.id)

    # enroll the sample people so the automations view has rows
    from app.automations import enroll
    for person in Person.query.filter_by(church_id=church.id).all():
        enroll(person, commit=False)
    db.session.commit()
    print("Demo data loaded. Sign in at /account/login as admin@example.com / journey1234")

# ---------------------------------------------------------------------------
# Phase 3 demo data: launch Sunday, kids, groups, announcements
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta as _td
from zoneinfo import ZoneInfo

from app.ministry import (
    Announcement, Child, Group, GroupMembership, Household, Service,
    ServiceAssignment, ServiceElement, Team, TeamMembership,
)

with app.app_context():
    from app.models import Church as _Church
    church = _Church.query.first()
    people = Person.query.filter_by(church_id=church.id).all()
    by_name = {p.first_name: p for p in people}

    if not Service.query.filter_by(church_id=church.id).first():
        central = ZoneInfo("America/Chicago")
        launch = datetime(2026, 10, 12, 10, 0, tzinfo=central).astimezone(ZoneInfo("UTC"))
        for offset, name in [(0, "Launch Sunday"), (7, "Sunday Gathering")]:
            service = Service(
                church_id=church.id, name=name, starts_at=launch + _td(days=offset)
            )
            db.session.add(service)
            db.session.flush()
            plan = [
                ("Welcome", "Countdown and welcome", 5),
                ("Worship", "Worship set", 20),
                ("Announcements", "Announcements", 4),
                ("Giving", "Giving moment", 3),
                ("Message", "Message: Adventurously Expectant", 30),
                ("Response", "Response and prayer", 6),
                ("Dismissal", "Dismissal", 2),
            ]
            for position, (kind, title, minutes) in enumerate(plan):
                db.session.add(ServiceElement(
                    church_id=church.id, service_id=service.id, position=position,
                    kind=kind, title=title, minutes=minutes,
                ))
            if offset == 0:
                for person_name, role, status in [
                    ("Priya", "Worship lead", "confirmed"),
                    ("Kevin", "Sound", "invited"),
                    ("Dana", "Kids check in", "confirmed"),
                    ("Sam", "Hospitality", "invited"),
                ]:
                    person = by_name.get(person_name)
                    if person:
                        db.session.add(ServiceAssignment(
                            church_id=church.id, service_id=service.id,
                            person_id=person.id, role=role, status=status,
                        ))

    if not Household.query.filter_by(church_id=church.id).first():
        households = [
            ("The Webb family", "573-555-0142", [("Ada", "Webb", 2019, "Kids", "Peanuts"),
                                                 ("Leo", "Webb", 2021, "Kids", None)]),
            ("The Nguyen family", "573-555-0198", [("Mai", "Nguyen", 2017, "Elementary", None)]),
        ]
        for name, phone, kids in households:
            household = Household(church_id=church.id, name=name, phone=phone)
            db.session.add(household)
            db.session.flush()
            for first, last, year, room, allergies in kids:
                db.session.add(Child(
                    church_id=church.id, household_id=household.id,
                    first_name=first, last_name=last,
                    birthdate=datetime(year, 5, 4).date(), room=room, allergies=allergies,
                ))

    if not Group.query.filter_by(church_id=church.id).first():
        for name, day, time, location, capacity, leader in [
            ("Tuesday Men", "Tuesday", "6:30 am", "Downtown Jackson", 10, "Kevin"),
            ("Wednesday Women", "Wednesday", "7:00 pm", "The Kim home", 12, "Rachel"),
            ("Young Families", "Sunday", "5:00 pm", "Rotating homes", 16, "Dana"),
        ]:
            leader_person = by_name.get(leader)
            db.session.add(Group(
                church_id=church.id, name=name, day_of_week=day, meeting_time=time,
                location=location, capacity=capacity,
                leader_id=leader_person.id if leader_person else None,
                description="Open to anyone. Bring a friend.",
            ))

    kids_team = Team.query.filter_by(church_id=church.id, name="Kids").first()
    if kids_team and not kids_team.memberships and by_name.get("Dana"):
        db.session.add(TeamMembership(
            church_id=church.id, team_id=kids_team.id,
            person_id=by_name["Dana"].id, role="Check in desk",
        ))

    if not Announcement.query.filter_by(church_id=church.id).first():
        db.session.add(Announcement(
            church_id=church.id, title="Doors open October 12",
            body="First service is 10:00 am. Bring someone with you.",
            audience="everyone", is_pinned=True,
        ))

    db.session.commit()
    print("Phase 3 demo data loaded. Kiosk PIN is 1012 at /kiosk.")
