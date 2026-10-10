"""Kids check-in.

**The kiosk requires a signed-in staff or leader session.** A tablet in a lobby
accepting PIN attempts from anyone who walks past is a device that will
eventually enumerate every household code in the church. The volunteer running
the desk signs the tablet in once on a Sunday morning; the families who use it
never see a login. Attempts are also rate limited per browser session, so a
tablet left unattended still cannot be walked through the code space.

Nothing here accepts a household PIN where a pickup code belongs, or the
reverse. See `app/pickup.py` for why that separation is the whole safety model.
"""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from app.audit import record
from app.ages import bands_for
from app.content import KIDS
from app.kids_notify import tell_checked_in, tell_checked_out
from app.models.audit import CHILD_CHECKED_OUT, TAG_REPRINTED
from app.extensions import db
from app.mail import NotQueued, queue
from app.models import Checkin, CheckinSession, Household, Person
from app.pickup import normalize
from app.security import min_role, sign_in
from app.timeutil import from_local

bp = Blueprint("kids", __name__, url_prefix="/kids")

# A tablet in a lobby is physically available to anyone. Ten wrong codes in a
# row is far past a parent mistyping and well short of walking the space.
MAX_PIN_ATTEMPTS = 10
ATTEMPT_KEY = "kiosk_pin_attempts"


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

@bp.get("/")
@login_required
@min_role("leader")
def index():
    return render_template(
        "kids/index.html",
        church=g.church,
        content=KIDS,
        open_session=CheckinSession.open_session(g.church.id),
        sessions=db.session.scalars(CheckinSession.recent(g.church.id)).all(),
        active="kids",
    )


@bp.post("/sessions/")
@login_required
@min_role("leader")
def open_session():
    raw = (request.form.get("starts_at") or "").strip()
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        flash(KIDS["bad_time"], "error")
        return redirect(url_for("kids.index"))

    checkin_session = CheckinSession(
        church_id=g.church.id,
        name=(request.form.get("name") or "Sunday").strip()[:160] or "Sunday",
        starts_at=from_local(naive, g.church),
    )
    db.session.add(checkin_session)
    db.session.commit()

    flash(KIDS["opened"].format(name=checkin_session.name), "notice")
    return redirect(url_for("kids.index"))


@bp.post("/sessions/<int:session_id>/toggle/")
@login_required
@min_role("leader")
def toggle_session(session_id: int):
    checkin_session = CheckinSession.get_for_church(g.church.id, session_id)
    if checkin_session is None:
        abort(404)

    if checkin_session.is_open:
        # Closing never checks anyone out. A child still in a room at the end
        # of a service is exactly what staff need to see.
        checkin_session.close()
        flash(KIDS["closed"].format(name=checkin_session.name), "notice")
    else:
        checkin_session.reopen()
        flash(KIDS["opened"].format(name=checkin_session.name), "notice")
    db.session.commit()

    return redirect(url_for("kids.index"))


# ---------------------------------------------------------------------------
# Kiosk
# ---------------------------------------------------------------------------

def _attempts() -> int:
    return session.get(ATTEMPT_KEY, 0)


def _record_failed_attempt() -> None:
    session[ATTEMPT_KEY] = _attempts() + 1


def _clear_attempts() -> None:
    session.pop(ATTEMPT_KEY, None)


@bp.get("/kiosk/")
@login_required
@min_role("leader")
def kiosk():
    return render_template(
        "kids/kiosk.html",
        church=g.church,
        content=KIDS,
        open_session=CheckinSession.open_session(g.church.id),
        locked=_attempts() >= MAX_PIN_ATTEMPTS,
    )


@bp.post("/kiosk/")
@login_required
@min_role("leader")
def kiosk_pin():
    checkin_session = CheckinSession.open_session(g.church.id)
    if checkin_session is None:
        flash(KIDS["kiosk_closed"], "error")
        return redirect(url_for("kids.kiosk"))

    if _attempts() >= MAX_PIN_ATTEMPTS:
        flash(KIDS["kiosk_too_many"], "error")
        return redirect(url_for("kids.kiosk"))

    pin = normalize(request.form.get("pin"))
    last_name = (request.form.get("last_name") or "").strip()
    matches = Household.matching_code(g.church, pin, last_name or None) if pin else []

    if not matches:
        _record_failed_attempt()
        flash(KIDS["kiosk_unknown"], "error")
        return redirect(url_for("kids.kiosk"))

    if len(matches) > 1:
        _clear_attempts()
        if last_name:
            # Two families of the same name sharing four digits. Rare enough
            # that a volunteer sorting it out beats another guessing game.
            flash(KIDS["kiosk_still_tied"], "error")
            return redirect(url_for("kids.kiosk"))

        # Two families whose parents' numbers end the same way. Ask for one
        # more thing rather than guessing, and show nothing until they answer:
        # a screen listing the families that matched would hand whoever typed
        # those four digits the names of everybody they belong to.
        return render_template(
            "kids/kiosk.html",
            church=g.church,
            content=KIDS,
            open_session=checkin_session,
            locked=False,
            tie_pin=pin,
        )

    _clear_attempts()
    return redirect(
        url_for("kids.kiosk_family", household_id=matches[0].id)
    )


@bp.get("/kiosk/family/<int:household_id>/")
@login_required
@min_role("leader")
def kiosk_family(household_id: int):
    checkin_session = CheckinSession.open_session(g.church.id)
    household = Household.get_for_church(g.church.id, household_id)
    if checkin_session is None or household is None:
        abort(404)

    already = {
        c.person_id for c in checkin_session.checkins if c.household_id == household.id
    }
    return render_template(
        "kids/family.html",
        church=g.church,
        content=KIDS,
        household=household,
        members=household.members,
        # Grouped so a volunteer can see which room somebody is for. Built
        # here rather than in the template, because the order is the point:
        # kids first, because this screen is mostly used for them.
        bands=bands_for(household.members),
        already=already,
        open_session=checkin_session,
    )


@bp.post("/kiosk/family/<int:household_id>/")
@login_required
@min_role("leader")
def kiosk_check_in(household_id: int):
    checkin_session = CheckinSession.open_session(g.church.id)
    household = Household.get_for_church(g.church.id, household_id)
    if checkin_session is None or household is None:
        abort(404)

    person_ids = request.form.getlist("person_id", type=int)
    if not person_ids:
        flash(KIDS["check_in_none"], "error")
        return redirect(url_for("kids.kiosk_family", household_id=household.id))

    # One code for the whole household, so a parent carries one, not three.
    code = checkin_session.issue_pickup_code(household.id)
    checked_in = []

    for person_id in person_ids:
        person = Person.get_for_church(g.church.id, person_id)
        # A person id from outside this household would put somebody else's
        # child under this family's pickup code.
        if person is None or person.household_id != household.id:
            continue
        if any(
            c.person_id == person.id and c.household_id == household.id
            for c in checkin_session.checkins
        ):
            continue

        db.session.add(
            Checkin(
                church_id=g.church.id,
                session_id=checkin_session.id,
                person_id=person.id,
                household_id=household.id,
                # Copied so a household rename or a child moving later does not
                # rewrite what happened this Sunday.
                household_name=household.name,
                pickup_code=code,
                checked_in_by_user_id=current_user.id,
            )
        )
        checked_in.append(person)

    # Committed before the rows are read, and that is load bearing. The loop
    # above read `checkin_session.checkins` to skip anybody already in, which
    # loads the collection; new rows added to the session do not appear in a
    # collection that is already loaded until something expires it. A flush
    # here instead of a commit leaves `rows` empty, which silently prints a
    # label page with no tags on it.
    db.session.commit()

    # The rows, not just the people, because a tag carries the room and the
    # pickup code and those live on the check-in.
    rows = [
        c for c in checkin_session.checkins
        if c.household_id == household.id
        and c.person_id in {p.id for p in checked_in}
    ]

    # Tell the household's adults. Not the pickup code: that stays on the
    # printed tag, where it is handed to one person rather than rendered on a
    # lock screen. See app/kids_notify.py.
    if rows:
        tell_checked_in(g.church, household, checked_in, rows,
                        by_user=current_user)
        db.session.commit()

    return render_template(
        "kids/label.html",
        church=g.church,
        content=KIDS,
        household=household,
        code=code,
        checked_in=checked_in,
        checkin_session=checkin_session,
        checkins=rows,
        # The roll, so the page can tell the printer what size the paper is.
        # Printing the page itself is the one-tap path and it needs a page
        # size or AirPrint assumes Letter and shrinks a 62mm tag onto it.
        size=g.church.label_size,
        pdf_url=tag_pdf_url(checkin_session, rows) if rows else None,
    )


@bp.route("/kiosk/forgot/", methods=["GET", "POST"])
@login_required
@min_role("leader")
def kiosk_forgot():
    """Email the household's check-in code.

    The spec says text it. There is no SMS provider in this system yet, and
    inventing one here would mean a second vendor, a second set of credentials,
    and a second thing to rotate. Email uses the outbox that already exists,
    and the `account` category means it reaches somebody who unsubscribed from
    everything else.
    """
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        # An address may belong to two people in one household. They share a
        # household, so they share a PIN, and either one is the right answer.
        person = db.session.scalars(
            db.select(Person).where(
                Person.church_id == g.church.id,
                Person.email == email,
                Person.is_archived.is_(False),
            )
        ).first() if email else None

        # Everything below is conditional and the response is not. A kiosk that
        # says "no such address" is a device for finding out who attends.
        if person is not None and person.household is not None:
            pin = person.household.ensure_checkin_pin()
            try:
                queue(
                    church_id=g.church.id,
                    category="kids_checkin",
                    subject=KIDS["forgot_email_subject"].format(church=g.church.name),
                    body_text=KIDS["forgot_email_body"].format(
                        name=person.first_name, church=g.church.name, pin=pin
                    ),
                    person=person,
                )
            except NotQueued:
                pass
            db.session.commit()

        flash(KIDS["forgot_sent"], "notice")
        return redirect(url_for("kids.kiosk"))

    return render_template("kids/forgot.html", church=g.church, content=KIDS)


# ---------------------------------------------------------------------------
# Name tags
# ---------------------------------------------------------------------------
#
# A tag carries the child's name, the room, and the pickup code. The pickup
# code is the part that matters: it is what a person has to produce to take a
# child out of a room, so a tag is a physical credential and the screen that
# prints one is treated like any other screen that shows a credential.
#
# Two rules hold for every path in here:
#
# 1. **Printing never issues a code.** The code was issued at check-in and the
#    parent is carrying it. Generating a new one on a reprint would leave the
#    family holding a code the desk no longer recognises, which is worse than
#    no tag at all.
# 2. **A reprint is logged.** Asking for a second copy of somebody's pickup
#    code is exactly what it would look like if a stranger were trying to
#    collect a child, so the log names who asked and for whom.
#
# What is deliberately *not* on a tag: a person's notes. That field carries
# pastoral context, and a tag is left on a table, dropped in a hallway, and
# handed to whoever is at the desk. If a child has an allergy that has to be
# on the tag, it belongs in a field of its own that was written to be printed.

# The first print happens on the check-in screen itself, from markup that is
# already on the page. Nothing here is reached by a volunteer working the
# normal Sunday flow, which is what lets every route below be logged without
# qualification: if this code ran, somebody asked for a second copy.

# ---------------------------------------------------------------------------
# Tags as a printable file
#
# The HTML tags above print from a desktop browser and do not print at all
# from the iOS app, because Apple's web view implements no print function. The
# button was silently dead on the one device check-in actually runs on.
#
# A PDF fixes two of the three problems. It opens in a viewer that has a print
# sheet, and it carries real physical dimensions, so a 62mm label prints at
# 62mm instead of being scaled to whatever the printer guessed from an HTML
# page.
#
# The third problem is why this route takes a signed token instead of a
# session. To reach a print sheet at all the file has to open outside the app,
# and a browser opened from an app does not carry that app's cookies: an iOS
# web view and Safari keep separate cookie stores. A `@login_required` PDF
# would therefore have shown a login screen at the one moment nobody can stop
# to type a password. See app/labeltoken.py for what the signature covers.
# ---------------------------------------------------------------------------

@bp.get("/tags/pdf/<token>/")
def tags_pdf(token: str):
    """The tags named by a signed token, as a file a printer can take.

    No `login_required`, deliberately: the token is the credential, it is
    good for ten minutes, and it names the exact check-in rows rather than
    anything that could be widened.

    Nothing is recorded here. Whoever minted the token was authorized and the
    reprint was logged at that point; logging again would count one reprint
    twice and make the log useless for the question it exists to answer.
    """
    from app.labeltoken import verify

    payload = verify(token, g.church.id)
    if payload is None:
        abort(404)

    checkins = [
        checkin for checkin in (
            Checkin.get_for_church(g.church.id, checkin_id)
            for checkin_id in payload["k"]
        )
        if checkin is not None and checkin.session_id == payload["s"]
    ]
    if not checkins:
        abort(404)

    checkin_session = CheckinSession.get_for_church(g.church.id, payload["s"])
    if checkin_session is None:
        abort(404)

    who = checkins[0].household_name if len(checkins) > 1 else checkins[0].person.full_name
    return _tag_pdf_response(checkin_session, checkins, who or "tags")


def _tag_pdf_response(checkin_session, checkins, who: str):
    from app.labels import render_tags
    from app.timeutil import format_local

    pdf = render_tags(
        checkins=checkins,
        church=g.church,
        checkin_session=checkin_session,
        size=g.church.label_size,
        nudge=g.church.tag_nudge,
        # `format_local(value, church, fmt)`. The church argument was missing
        # here, so the format string landed in it and every tag ever printed
        # carried the default format instead: "Thursday 9:30am" where the
        # date belongs. It never raised, because `zone_for` falls back rather
        # than throwing on a bad value, which is why it survived this long.
        when=format_local(checkin_session.starts_at, g.church, "%b %-d"),
    )
    safe = "".join(c for c in who if c.isalnum() or c in " -_").strip() or "tags"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            # inline, not attachment: the point is that it opens in a viewer
            # with a print button, not that it lands in Files.
            "Content-Disposition": f'inline; filename="{safe} tags.pdf"',
            # A pickup code is a live credential for collecting a child.
            "Cache-Control": "no-store, private",
        },
    )


def tag_pdf_url(checkin_session, checkins) -> str:
    """The signed link for these tags. Minted by whoever renders the page."""
    from app.labeltoken import sign

    return url_for(
        "kids.tags_pdf",
        token=sign(
            church_id=g.church.id,
            session_id=checkin_session.id,
            checkin_ids=[c.id for c in checkins],
        ),
    )


# ---------------------------------------------------------------------------
# Can this tablet print?
#
# Built because the answer was unknowable from here. Name tags were not coming
# out of the lobby iPad, the server was generating correct PDFs the whole time,
# and the difference was the device: Apple's web view has no print function, so
# inside the installed app the Print button did nothing and said nothing.
#
# There is no way to diagnose that remotely. The wrapper is a separate project,
# the tablet has no console, and the only person who can see the screen is a
# volunteer on a Sunday morning. So the tablet reports on itself, the same way
# the push problem was finally pinned down by a page that asked the phone.
#
# The sample tag matters as much as the report. "Your device says it can open
# files" is a claim; a tag coming out of the printer is the answer, and it can
# be had on a Tuesday rather than discovered during check-in.
# ---------------------------------------------------------------------------

@bp.get("/kiosk/print-check/")
@login_required
def print_check():
    """What this tablet can and cannot do with a tag."""
    from app.labeltoken import sign_sample

    return render_template(
        "kids/print_check.html",
        church=g.church,
        content=KIDS,
        size=g.church.label_size,
        sample_url=url_for("kids.sample_tag_pdf",
                           token=sign_sample(church_id=g.church.id)),
        kiosk_url=url_for("kids.kiosk"),
    )


@bp.get("/tags/sample/<token>/")
def sample_tag_pdf(token: str):
    """One tag with invented details, on the church's real label size.

    No `login_required`, for the same reason the real tags have none: the
    point is to open it outside the app, where the app's cookies do not reach.
    The token is the credential.

    Nothing real is on it. No pickup code, no child, no family, no session, so
    a leaked sample link discloses the church's name and the fact that it owns
    a label printer.
    """
    from app.labeltoken import verify_sample
    from app.labels import render_tags
    from types import SimpleNamespace

    if not verify_sample(token, g.church.id):
        abort(404)

    sample = SimpleNamespace(
        person=SimpleNamespace(first_name=KIDS["sample_first"],
                               last_name=KIDS["sample_last"]),
        room=KIDS["sample_room"],
        # Visibly not a code. A real one is letters from a restricted
        # alphabet, and a volunteer must never be able to mistake this tag
        # for one that authorises collecting a child.
        pickup_code=KIDS["sample_code"],
    )
    pdf = render_tags(
        checkins=[sample],
        church=g.church,
        checkin_session=SimpleNamespace(name=KIDS["sample_room"]),
        size=g.church.label_size,
        nudge=g.church.tag_nudge,
        when=KIDS["sample_when"],
    )
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="sample tag.pdf"',
            "Cache-Control": "no-store, private",
        },
    )


@bp.get("/tags/alignment/<token>/")
def alignment_tag_pdf(token: str):
    """A ruler on a label, for a printer that prints off-centre.

    The same token as the sample tag and for the same reason: it has to open
    outside the app, where the app's cookies do not reach, and it carries
    nothing about anybody. A leaked link is a picture of a ruler.

    This exists because "the names are cut off" is not a number. Nothing in
    the print path reports how far the printer has moved the page, so the
    only honest way to find out is to print a scale and read it.
    """
    from app.labels import render_alignment_test
    from app.labeltoken import verify_sample

    if not verify_sample(token, g.church.id):
        abort(404)

    pdf = render_alignment_test(
        church=g.church,
        size=g.church.label_size,
        nudge=g.church.tag_nudge,
    )
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="label alignment.pdf"',
            "Cache-Control": "no-store, private",
        },
    )


# ---------------------------------------------------------------------------
# Setting a tablet up
# ---------------------------------------------------------------------------

@bp.route("/kiosk/setup/<token>/", methods=["GET", "POST"])
def kiosk_setup(token: str):
    """Sign this tablet in as the kiosk, once.

    No `login_required`: the token is the credential, which is the entire
    point. A volunteer holding a tablet in a lobby has nothing to type.

    GET shows a button and POST does the work, because a link in a group chat
    is fetched by every preview bot that sees it, and a GET that signed
    somebody in would be burned before a human touched it.
    """
    from app.models.kiosk import KioskSetupToken

    found = KioskSetupToken.redeem(g.church.id, token)
    if found is None:
        return render_template(
            "kids/setup_invalid.html", church=g.church, content=KIDS
        ), 404

    if request.method == "GET":
        return render_template(
            "kids/setup.html", church=g.church, content=KIDS, token=token,
            label=found.label,
        )

    user = found.user
    if user is None or not user.is_kiosk or not user.is_active:
        return render_template(
            "kids/setup_invalid.html", church=g.church, content=KIDS
        ), 404

    found.consume()
    user.register_successful_login()
    db.session.commit()

    # A year, so nobody meets a login screen on a Sunday morning. What makes
    # that safe is that a kiosk account can only reach these screens; see
    # app/kiosk.py. `sign_in` knows this account is a kiosk and applies the
    # year itself, so the duration is no longer written out twice in two
    # files that could disagree.
    sign_in(user)

    flash(KIDS["kiosk_setup_done"], "notice")
    return redirect(url_for("kids.kiosk"))


@bp.get("/tags/<int:session_id>/family/<int:household_id>/")
@login_required
@min_role("leader")
def tags_for_household(session_id: int, household_id: int):
    """Every tag for one family in one session, on one sheet."""
    checkin_session = CheckinSession.get_for_church(g.church.id, session_id)
    household = Household.get_for_church(g.church.id, household_id)
    if checkin_session is None or household is None:
        abort(404)

    checkins = checkin_session.checkins_for_household(household.id)
    if not checkins:
        flash(KIDS["tags_none"], "error")
        return redirect(url_for("kids.index"))

    record(
        TAG_REPRINTED,
        KIDS["tags_reprint_family"],
        subject_type="household",
        subject_id=household.id,
        subject_label=household.name,
        detail=", ".join(c.person.full_name for c in checkins),
    )
    db.session.commit()

    return render_template(
        "kids/tags.html",
        church=g.church,
        content=KIDS,
        checkin_session=checkin_session,
        checkins=checkins,
        size=g.church.label_size,
        pdf_url=tag_pdf_url(checkin_session, checkins),
    )


@bp.get("/tags/child/<int:checkin_id>/")
@login_required
@min_role("leader")
def tag_for_child(checkin_id: int):
    """One child's tag, on its own."""
    checkin = Checkin.get_for_church(g.church.id, checkin_id)
    if checkin is None:
        abort(404)

    record(
        TAG_REPRINTED,
        KIDS["tags_reprint"],
        subject_type="person",
        subject_id=checkin.person_id,
        subject_label=checkin.person.full_name,
        detail=checkin.household_name or "",
    )
    db.session.commit()

    return render_template(
        "kids/tags.html",
        church=g.church,
        content=KIDS,
        checkin_session=checkin.session,
        checkins=[checkin],
        size=g.church.label_size,
        pdf_url=tag_pdf_url(checkin.session, [checkin]),
    )


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------

@bp.get("/checkout/")
@login_required
@min_role("leader")
def checkout():
    checkin_session = CheckinSession.open_session(g.church.id)
    code = normalize(request.args.get("code"))
    last_name = (request.args.get("last_name") or "").strip()
    matches = (
        Checkin.by_pickup_code(g.church.id, checkin_session.id, code,
                               last_name or None)
        if checkin_session and code
        else []
    )

    # Under phone codes a code can reach two families at once. Showing both
    # would put somebody else's children on the screen with a working check
    # box beside them, so nothing is shown until a last name narrows it.
    tied = len(Checkin.households_in(matches)) > 1
    if tied:
        matches = []

    return render_template(
        "kids/checkout.html",
        church=g.church,
        content=KIDS,
        open_session=checkin_session,
        code=code,
        last_name=last_name,
        matches=matches,
        tied=tied,
        asked_last_name=bool(last_name),
        searched=bool(code),
    )


@bp.post("/sessions/<int:session_id>/delete/")
@login_required
@min_role("staff")
def delete_session(session_id: int):
    """Delete a session and every check-in on it.

    Mostly for a Sunday entered twice, or the rehearsal sessions from setting
    the system up. It is not recoverable, and what it destroys is the record
    of which children were in a room and who took them home, so three things
    guard it:

    1. **Staff, not leader.** Everything else in Kids is open to the
       volunteers running the desk. This is not.
    2. **An open session cannot be deleted.** Children may be in a room right
       now, and their pickup codes would go with it. Close it first, which
       makes the decision two deliberate steps.
    3. **The tick box is required**, and the audit entry carries the names of
       every child who was on it. After this runs, that entry is the only
       thing left that says the morning happened.
    """
    from app.models.audit import CHECKIN_SESSION_DELETED
    from app.timeutil import format_local

    checkin_session = CheckinSession.get_for_church(g.church.id, session_id)
    if checkin_session is None:
        abort(404)

    if checkin_session.is_open:
        flash(KIDS["delete_close_first"], "error")
        return redirect(url_for("kids.index"))

    if request.form.get("confirm") != "on":
        flash(KIDS["delete_confirm_required"], "error")
        return redirect(url_for("kids.index"))

    when = format_local(checkin_session.starts_at, g.church, "%B %-d, %Y")
    label = f"{checkin_session.name} on {when}"
    children = [c.person.full_name for c in checkin_session.checkins]

    record(
        CHECKIN_SESSION_DELETED,
        f"{label} was deleted",
        actor=current_user,
        subject_type="checkin_session",
        subject_id=checkin_session.id,
        subject_label=label,
        detail=(
            f"{len(children)} children checked in"
            + (": " + ", ".join(children) if children else ".")
        ),
    )
    db.session.delete(checkin_session)
    db.session.commit()

    flash(KIDS["deleted"].format(name=checkin_session.name, when=when), "notice")
    return redirect(url_for("kids.index"))


@bp.post("/checkins/<int:checkin_id>/out/")
@login_required
@min_role("leader")
def check_out_one(checkin_id: int):
    """Check one child out from the staff list.

    The desk screen is still the way this should normally happen, because it
    asks who is collecting and that is the fact worth keeping. This path
    exists for the rest of Sunday: a parent who collected before anybody got
    to a screen, a child moved to another room, a row left open at the end of
    the morning.

    `collected_by` is deliberately left empty rather than filled with the
    staff member's name. They did not collect the child, and a record saying
    they did would be worse than one that admits it does not know. Who pressed
    the button is in the audit entry, where it belongs.
    """
    checkin = Checkin.get_for_church(g.church.id, checkin_id)
    if checkin is None:
        abort(404)

    if not checkin.is_present:
        flash(KIDS["roster_already_out"].format(name=checkin.person.full_name),
              "error")
        return redirect(url_for("kids.index"))

    checkin.check_out(collected_by=None, user=current_user)
    record(
        CHILD_CHECKED_OUT,
        f"{checkin.person.full_name} checked out from the staff list",
        actor=current_user,
        subject_type="checkin",
        subject_id=checkin.id,
        subject_label=checkin.person.full_name,
        detail=KIDS["roster_no_name"],
    )

    # Told the same way as the desk, with no name for who collected them,
    # because that is the truth here and this route refuses to invent one.
    tell_checked_out(g.church, [checkin], collected_by=None, by_user=current_user)

    db.session.commit()

    flash(KIDS["roster_checked_out"].format(name=checkin.person.full_name),
          "notice")
    return redirect(url_for("kids.index"))


@bp.post("/checkout/")
@login_required
@min_role("leader")
def do_checkout():
    checkin_session = CheckinSession.open_session(g.church.id)
    if checkin_session is None:
        abort(404)

    code = normalize(request.form.get("code"))
    last_name = (request.form.get("last_name") or "").strip()
    checkin_ids = request.form.getlist("checkin_id", type=int)
    if not checkin_ids:
        flash(KIDS["checkout_none"], "error")
        return redirect(url_for("kids.checkout", code=code, last_name=last_name or None))

    # Re-derive from the code rather than trusting the ids in the form. An id
    # alone would let a posted request check out a child whose code the person
    # at the desk never had.
    rows = Checkin.by_pickup_code(g.church.id, checkin_session.id, code,
                                  last_name or None)

    # The same guard the screen applies, enforced again here. A posted form is
    # not a screen: without this, a code shared by two families would check
    # out either one's children on an id the sender guessed.
    if len(Checkin.households_in(rows)) > 1:
        flash(KIDS["checkout_tied"], "error")
        return redirect(url_for("kids.checkout", code=code))

    allowed = {c.id: c for c in rows}

    collected_by = (request.form.get("collected_by") or "").strip()
    names = []
    collected = []
    for checkin_id in checkin_ids:
        checkin = allowed.get(checkin_id)
        if checkin is None or not checkin.is_present:
            continue
        checkin.check_out(collected_by=collected_by, user=current_user)
        names.append(checkin.person.full_name)
        collected.append(checkin)
        # The pickup code is deliberately not recorded. Who left with a child
        # is the fact worth keeping; the code that authorised it is a secret.
        record(
            CHILD_CHECKED_OUT,
            f"{checkin.person.full_name} collected by "
            f"{collected_by or 'somebody not named'}",
            actor=current_user,
            subject_type="checkin",
            subject_id=checkin.id,
            subject_label=checkin.person.full_name,
        )

    # The notification somebody is actually waiting on. A grandparent collects
    # a child at 11:40 and the parent in the service used to find out by
    # walking to an empty room.
    if collected:
        tell_checked_out(g.church, collected, collected_by=collected_by,
                         by_user=current_user)

    db.session.commit()

    if names:
        flash(KIDS["checkout_done"].format(names=", ".join(names)), "notice")
    else:
        flash(KIDS["checkout_already"], "error")
    return redirect(url_for("kids.checkout"))
