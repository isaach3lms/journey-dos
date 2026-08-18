"""One-click unsubscribe.

The only route in the application that a signed-out stranger may use to change
stored data, so it is worth being explicit about why that is safe.

The token is 32 random bytes, per person, minted the first time a message goes
out. It cannot be derived from a person id, so possessing one link does not let
anyone unsubscribe anyone else. It is still scoped to the church resolved from
the host, so a token from one tenant does nothing on another's address.

Confirmation is a POST. Mail clients and security scanners fetch every link in
a message, and a GET that unsubscribes would mean a corporate spam filter
quietly opting people out of their own church's email.
"""

from flask import Blueprint, abort, g, render_template

from app.content import EMAIL
from app.extensions import db
from app.mail import opt_out
from app.models import Person

bp = Blueprint("unsubscribe", __name__, url_prefix="/unsubscribe")


def _person_for(token: str):
    if not token or len(token) < 20:
        return None
    return db.session.scalar(
        db.select(Person).where(
            Person.unsubscribe_token == token,
            Person.church_id == g.church.id,
        )
    )


@bp.get("/<token>/")
def confirm(token: str):
    person = _person_for(token)
    if person is None:
        return render_template(
            "unsubscribe/invalid.html", church=g.church, content=EMAIL
        ), 404
    return render_template(
        "unsubscribe/confirm.html", church=g.church, content=EMAIL,
        person=person, token=token,
    )


@bp.post("/<token>/")
def do_unsubscribe(token: str):
    person = _person_for(token)
    if person is None:
        abort(404)

    opt_out(person, reason="One-click unsubscribe link")
    db.session.commit()

    return render_template(
        "unsubscribe/done.html", church=g.church, content=EMAIL, person=person
    )
