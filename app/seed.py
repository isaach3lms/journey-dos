import os

from flask import current_app

from .brand import BRAND
from .extensions import db
from .models import Church, Person, Stage

# The Journey Church journey. Fewer stages than a launched church needs,
# because a plant does not have members or volunteers yet. Add stages as the
# church grows. stuck_after_days is the alert threshold per stage.
STAGES = [
    ("Interested", 14, "Told us they want to hear more. No contact yet."),
    ("Connected", 21, "A pastor has made real contact."),
    ("Launch team", 30, "Committed to opening the doors with us."),
    ("Serving", 45, "On a team with a role and a leader."),
    ("Leading", 60, "Leading a team, a group, or another person."),
]


def seed():
    slug = current_app.config["CHURCH_SLUG"]
    church = Church.query.filter_by(slug=slug).first()
    if not church:
        church = Church(
            name=BRAND["church_name"],
            slug=slug,
            timezone="America/Chicago",
            tithely_form_id=current_app.config.get("TITHELY_FORM_ID") or None,
            tithely_give_url=current_app.config.get("TITHELY_GIVE_URL") or None,
        )
        db.session.add(church)
        db.session.flush()

    for position, (name, stuck, description) in enumerate(STAGES):
        existing = Stage.query.filter_by(church_id=church.id, position=position).first()
        if existing:
            existing.name = name
            existing.stuck_after_days = stuck
            existing.description = description
            continue
        db.session.add(
            Stage(
                church_id=church.id,
                name=name,
                position=position,
                stuck_after_days=stuck,
                description=description,
            )
        )

    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password:
        admin = Person.query.filter_by(church_id=church.id, email=admin_email).first()
        if not admin:
            admin = Person(
                church_id=church.id,
                first_name=os.environ.get("ADMIN_FIRST_NAME", "Journey"),
                last_name=os.environ.get("ADMIN_LAST_NAME", "Admin"),
                email=admin_email,
                role="admin",
                source="seed",
            )
            db.session.add(admin)
        admin.role = "admin"
        admin.set_password(admin_password)

    db.session.commit()
    return church
