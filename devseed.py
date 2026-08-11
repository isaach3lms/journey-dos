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
