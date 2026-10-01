"""Answering a serving invitation from the email, without signing in.

The second route in this application a signed-out person may use to change
stored data, so the same care as `app/blueprints/unsubscribe.py` applies and
for the same reasons.

**Why no sign-in.** A volunteer asked on a Tuesday evening answers from a
phone in about four seconds or does not answer at all. Putting a password in
front of "can you play on Sunday" is how a plan stays half unanswered until
Saturday, and a leader ends up texting nine people. The token is the
credential, and it is deliberately the weakest credential in the system: 32
random bytes that answer one question about one Sunday. It cannot read a
profile, see who else is serving, change a plan, or sign anybody in. Losing
one means somebody could answer one invitation wrongly, which a leader sees on
the plan and can undo.

**Why answering is a POST.** Mail clients and corporate security scanners
fetch every link in a message before a human sees it. A GET that accepted
would mean a spam filter signing volunteers up for Sunday. So the emailed link
opens a page that says what is being asked, and the answer is a button on it.
The `answer` query parameter only pre-selects which button is highlighted; it
never acts on its own.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app.content import SERVICES
from app.extensions import db
from app.models import ACCEPTED, DECLINED, ServiceAssignment

bp = Blueprint("invite", __name__, url_prefix="/serve-invite")


def _assignment_for(token: str):
    """The assignment this token answers for, on this church's address.

    The host check matches unsubscribe: a token minted for one tenant does
    nothing on another's address, even though the token alone would identify
    the row.
    """
    assignment = ServiceAssignment.by_respond_token(token)
    if assignment is None:
        return None
    if assignment.church_id != getattr(g.church, "id", None):
        return None
    return assignment


@bp.get("/<token>/")
def respond(token: str):
    assignment = _assignment_for(token)
    if assignment is None:
        abort(404)

    answer = request.args.get("answer")
    return render_template(
        "invite/respond.html",
        church=g.church,
        content=SERVICES,
        assignment=assignment,
        service=assignment.service,
        # Highlights a button. Never acts on its own: see the module docstring.
        leaning=answer if answer in ("accept", "decline") else None,
    )


@bp.post("/<token>/")
def answer(token: str):
    assignment = _assignment_for(token)
    if assignment is None:
        abort(404)

    choice = request.form.get("answer")
    if choice not in ("accept", "decline"):
        flash(SERVICES["invite_pick_one"], "error")
        return redirect(url_for("invite.respond", token=token))

    # Changing an answer is allowed, and on purpose. A volunteer who accepted
    # on Tuesday and is ill on Saturday needs to be able to say so from the
    # same link, and a leader would far rather know on Saturday.
    assignment.respond(ACCEPTED if choice == "accept" else DECLINED)
    db.session.commit()

    return render_template(
        "invite/done.html",
        church=g.church,
        content=SERVICES,
        assignment=assignment,
        service=assignment.service,
    )
