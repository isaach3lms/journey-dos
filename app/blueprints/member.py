"""The member app.

The same database, the same brand, a different reader. A member sees their own
record and nothing else, and that is enforced by only ever loading
`current_user.person` rather than by taking an id from a URL. There is no route
in this blueprint that accepts a person id in its URL, so there is no way to
ask it for somebody else's record. One route takes an id in a form body,
`remove_family_member`, and it matches that id against the member's own
household before acting, so it can only name somebody already on their
screen.

Staff and leaders can reach it too, showing their own record. That is a preview
of what the church's people see, not impersonation: there is deliberately no
way for a staff member to view the member app *as* another person. Reading a
member's screen through their eyes would mean a staff account could see a
private view with no audit trail, and nothing in this increment needs it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from flask import (
    Blueprint,
    current_app,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, logout_user

from app.categories import CATEGORIES, OPTIONAL_CATEGORIES
from app.content import GIVING, GROUPS, MEMBER, MESSAGES, PRIVACY, RESOURCES, SERVICES
from app.extensions import db
from app.chat_notify import notify_new_message
from app.mail import opt_in, opt_out
from app.bible import parse as parse_reference
from app.bible.providers import fetch_passage
from app.models import (
    RSVP_CHOICES,
    Group,
    GroupMeeting,
    MeetingRSVP,
    NextStep,
    Resource,
    ResourceSession,
    SessionCompletion,
)
from app.audit import record as audit_record
from app.models import Household, Person, PushSubscription
from app.models.audit import PERSON_ARCHIVED
from app.models.message import Conversation, Message
from app.models import MessageReport, PersonBlock
from app.models.moderation import SOURCE_BLOCK, SOURCE_REPORT
from app.moderation import objectionable_terms
from app.models.service import ACCEPTED, DECLINED, ServiceAssignment
from app.models.person_event import KIND_CREATED, KIND_NOTE, PersonEvent
from app.stages import STAGE_BY_CODE, stages_for

bp = Blueprint("member", __name__, url_prefix="/me")


def _greeting(name: str) -> str:
    """Time of day from the server's clock.

    A per-church timezone lands with Services at increment 10, which is the
    first feature where being an hour out actually matters. Until then UTC is
    honest about being approximate rather than pretending otherwise.
    """
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        key = "greeting_morning"
    elif hour < 17:
        key = "greeting_afternoon"
    else:
        key = "greeting_evening"
    return MEMBER[key].format(name=name)


def _base_context(person):
    return {
        "church": g.church,
        "content": MEMBER,
        "person": person,
        "is_preview": current_user.role != "member",
    }


@bp.get("/")
@login_required
def home():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    open_steps = db.session.scalars(
        NextStep.open_for_person(g.church.id, person.id)
    ).all()

    days = None
    if person.first_seen_on:
        days = (datetime.now(timezone.utc).date() - person.first_seen_on).days + 1

    from app.models.verse import WeeklyVerse
    from app.timeutil import now_local

    return render_template(
        "member/home.html",
        verse=WeeklyVerse.current(g.church.id, now_local(g.church).date()),
        greeting=_greeting(person.first_name),
        waiting_for_approval=person.is_waiting_for_approval,
        days=days,
        open_steps=open_steps,
        stage=STAGE_BY_CODE.get(person.stage),
        stages=stages_for(g.church),
        tab="home",
        **_base_context(person),
    )


@bp.get("/you/")
@login_required
def you():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    household_members = []
    pin = None
    if person.household is not None:
        household_members = [
            member for member in person.household.members if member.id != person.id
        ]
        # Minted on first view rather than at household creation. Most
        # households never open this screen, and an unused code is one more
        # secret to look after for no benefit.
        pin = person.household.ensure_checkin_pin()
        db.session.commit()

    return render_template(
        "member/you.html",
        household_members=household_members,
        pin=pin,
        stages=stages_for(g.church),
        group_names=[g_.name for g_ in db.session.scalars(
            Group.for_person(g.church.id, person.id)
        ).all()],
        serving_count=db.session.scalar(
            db.select(db.func.count(ServiceAssignment.id)).where(
                ServiceAssignment.church_id == g.church.id,
                ServiceAssignment.person_id == person.id,
                ServiceAssignment.status != DECLINED,
            )
        ) or 0,
        categories=CATEGORIES,
        optional_categories=OPTIONAL_CATEGORIES,
        push_devices=PushSubscription.device_count(g.church.id, current_user.id),
        blocks=db.session.scalars(PersonBlock.for_blocker(g.church.id, person.id)).all(),
        msg=MESSAGES,
        vapid_public_key=current_app.config.get("VAPID_PUBLIC_KEY", ""),
        tab="you",
        **_base_context(person),
    )


@bp.post("/you/details/")
@login_required
def save_details():
    """A member correcting their own record.

    Only their own: the person comes from the session, never from the form, so
    there is no id anybody could change. Email is not here. It is the address
    they sign in with, and letting somebody retype it in a profile screen is
    how an account gets locked out of itself. The church office changes that.

    An address belongs to the household, so editing it changes what the rest
    of the family sees too. The screen says so rather than hiding it.
    """
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    first = (request.form.get("first_name") or "").strip()
    last = (request.form.get("last_name") or "").strip()
    if not first or not last:
        flash(MEMBER["details_name_required"], "error")
        return redirect(url_for("member.you", open="details", _anchor="details"))

    birthdate = person.birthdate
    raw_birthday = (request.form.get("birthdate") or "").strip()
    if raw_birthday:
        try:
            birthdate = datetime.strptime(raw_birthday, "%Y-%m-%d").date()
        except ValueError:
            flash(MEMBER["details_birthday_bad"], "error")
            return redirect(url_for("member.you", open="details", _anchor="details"))
        if birthdate > date.today():
            flash(MEMBER["details_birthday_future"], "error")
            return redirect(url_for("member.you", open="details", _anchor="details"))
    else:
        birthdate = None

    before = {
        "name": person.full_name,
        "phone": person.phone,
        "birthday": person.birthdate,
    }
    person.first_name = first[:80]
    person.last_name = last[:80]
    person.phone = (request.form.get("phone") or "").strip()[:40] or None
    person.birthdate = birthdate

    changed = []
    if before["name"] != person.full_name:
        changed.append("name")
    if before["phone"] != person.phone:
        changed.append("phone")
    if before["birthday"] != person.birthdate:
        changed.append("birthday")

    household = person.household
    if household is not None:
        was = (household.address_line, household.city, household.postal_code)
        household.address_line = (request.form.get("address_line") or "").strip()[:200] or None
        household.city = (request.form.get("city") or "").strip()[:80] or None
        household.postal_code = (request.form.get("postal_code") or "").strip()[:20] or None
        if was != (household.address_line, household.city, household.postal_code):
            changed.append("address")

    if changed:
        # Staff see this on the person's timeline, so a name that changed in
        # the app is not a mystery when somebody asks about it later.
        PersonEvent.record(
            person, KIND_NOTE, MEMBER["details_event"],
            detail=MEMBER["details_event_detail"].format(fields=", ".join(changed)),
        )
    db.session.commit()

    flash(MEMBER["details_saved"] if changed else MEMBER["details_unchanged"], "notice")
    return redirect(url_for("member.you", open="details", _anchor="details"))


# A household holds one family. Twelve covers the largest real family and
# stops a bored member making a hundred records.
MAX_HOUSEHOLD_MEMBERS = 12


@bp.post("/family/add/")
@login_required
def add_family_member():
    """A parent adding their own child, ready for kids check-in.

    Adding a child is the one thing a member cannot do from anywhere else
    without calling the office, and it is what makes Sunday check-in work: a
    child needs a record, a household, and that household needs a PIN.

    The household is created here when the member does not have one yet,
    because a family of one with no household is exactly the state a
    self-signed-up parent arrives in.
    """
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    first = (request.form.get("first_name") or "").strip()
    # Most families share a surname, so an empty one follows the parent's
    # rather than refusing the form over something we can already answer.
    last = (request.form.get("last_name") or "").strip() or person.last_name
    if not first:
        flash(MEMBER["family_first_required"], "error")
        return redirect(url_for("member.you", open="family", _anchor="family"))

    birthdate = None
    raw_birthday = (request.form.get("birthdate") or "").strip()
    if raw_birthday:
        try:
            birthdate = datetime.strptime(raw_birthday, "%Y-%m-%d").date()
        except ValueError:
            flash(MEMBER["details_birthday_bad"], "error")
            return redirect(url_for("member.you", open="family", _anchor="family"))
        if birthdate > date.today():
            flash(MEMBER["details_birthday_future"], "error")
            return redirect(url_for("member.you", open="family", _anchor="family"))

    household = person.household
    if household is None:
        household = Household(
            church_id=g.church.id,
            name=MEMBER["family_household_name"].format(last=person.last_name),
        )
        db.session.add(household)
        db.session.flush()
        person.household_id = household.id

    living = [m for m in household.members if not m.is_archived]
    if len(living) >= MAX_HOUSEHOLD_MEMBERS:
        flash(MEMBER["family_full"].format(max=MAX_HOUSEHOLD_MEMBERS), "error")
        return redirect(url_for("member.you", open="family", _anchor="family"))

    full_name = f"{first} {last}".strip().lower()
    if any(m.full_name.strip().lower() == full_name for m in living):
        flash(MEMBER["family_duplicate"].format(name=f"{first} {last}".strip()), "error")
        return redirect(url_for("member.you", open="family", _anchor="family"))

    is_child = request.form.get("is_child") == "on"
    child = Person(
        church_id=g.church.id,
        first_name=first[:80],
        last_name=last[:80],
        birthdate=birthdate,
        is_child=is_child,
        household_id=household.id,
        # A family arrives together, so the child starts where the parent is
        # rather than at the front of a follow-up path meant for adults.
        stage=person.stage,
        notes=(request.form.get("notes") or "").strip()[:500] or None,
    )
    db.session.add(child)
    db.session.flush()

    PersonEvent.record(
        child, KIND_CREATED, MEMBER["family_event"],
        detail=MEMBER["family_event_detail"].format(name=person.full_name),
    )
    # The code is what the kiosk asks for, so it exists before they leave
    # this screen rather than the first time somebody opens the Family row.
    household.ensure_checkin_pin()
    db.session.commit()

    flash(MEMBER["family_added"].format(name=child.first_name), "notice")
    return redirect(url_for("member.you", open="family", _anchor="family"))


@bp.post("/family/remove/")
@login_required
def remove_family_member():
    """Undo a mistyped or duplicated child.

    The id arrives in the form rather than the URL, and is checked against
    this member's own household before anything happens, so it can only ever
    name somebody already on their own screen. The record is archived, never
    deleted: a child who has been checked in has a safety history worth
    keeping, and staff can bring the record back.
    """
    person = current_user.person
    if person is None or person.household is None:
        return redirect(url_for("member.you"))

    try:
        wanted = int(request.form.get("member_id") or 0)
    except ValueError:
        wanted = 0

    child = next(
        (
            m for m in person.household.members
            if m.id == wanted and m.id != person.id and m.is_child and not m.is_archived
        ),
        None,
    )
    if child is None:
        flash(MEMBER["family_remove_missing"], "error")
        return redirect(url_for("member.you", open="family", _anchor="family"))

    child.is_archived = True
    PersonEvent.record(
        child, KIND_NOTE, MEMBER["family_removed_event"],
        detail=MEMBER["family_event_detail"].format(name=person.full_name),
    )
    db.session.commit()

    flash(MEMBER["family_removed"].format(name=child.first_name), "notice")
    return redirect(url_for("member.you", open="family", _anchor="family"))


@bp.post("/you/preferences/")
@login_required
def set_preferences():
    """A member changing their own preferences. No id in the request."""
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    for category in OPTIONAL_CATEGORIES:
        person.set_preference(
            category.code, request.form.get(f"cat_{category.code}") == "on"
        )
    db.session.commit()

    flash(MEMBER["prefs_saved"], "notice")
    return redirect(url_for("member.you", open="notifications", _anchor="notifications"))


@bp.post("/you/optout/")
@login_required
def toggle_opt_out():
    person = current_user.person
    if person is None:
        return redirect(url_for("member.you"))

    if person.has_opted_out:
        opt_in(person)
    else:
        opt_out(person, reason="Turned off by the member in the app")
    db.session.commit()

    return redirect(url_for("member.you", open="notifications", _anchor="notifications"))


# ---------------------------------------------------------------------------
# Increment 6: reading
#
# These routes take a resource id, which every other route in this blueprint
# deliberately avoids. That is safe because a resource is church-wide content
# rather than one person's record: the id identifies something published to
# everyone, and `published_for_member` refuses drafts and other tenants. A
# person id would identify somebody, which is why none of these accept one.
# ---------------------------------------------------------------------------

@bp.get("/read/")
@login_required
def reading():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    resources = db.session.scalars(
        Resource.for_church(g.church.id, published_only=True)
    ).all()

    progress = {}
    for resource in resources:
        done = SessionCompletion.completed_session_ids(
            g.church.id, person.id, resource.id
        )
        progress[resource.id] = len(done)

    return render_template(
        "member/reading.html",
        resources=resources,
        progress=progress,
        res=RESOURCES,
        tab="read",
        **_base_context(person),
    )


@bp.get("/read/<int:resource_id>/")
@login_required
def read_resource(resource_id: int):
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    resource = Resource.published_for_member(g.church.id, resource_id)
    if resource is None:
        abort(404)

    done = SessionCompletion.completed_session_ids(g.church.id, person.id, resource.id)
    return render_template(
        "member/plan.html",
        resource=resource,
        done=done,
        res=RESOURCES,
        tab="read",
        **_base_context(person),
    )


@bp.get("/read/<int:resource_id>/<int:session_id>/")
@login_required
def read_session(resource_id: int, session_id: int):
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    resource = Resource.published_for_member(g.church.id, resource_id)
    session = ResourceSession.get_for_church(g.church.id, session_id)
    if resource is None or session is None or session.resource_id != resource.id:
        abort(404)

    done = SessionCompletion.completed_session_ids(g.church.id, person.id, resource.id)
    following = [s for s in resource.sessions if s.position > session.position]

    # The plan stores a reference, not the words. This is where it becomes
    # scripture, and it falls back to the public domain text rather than
    # failing if a licensed provider cannot answer.
    passage = fetch_passage(
        parse_reference(session.passage_ref),
        g.church,
        current_app.config.get("SECRET_KEY"),
    )

    return render_template(
        "member/session.html",
        passage=passage,
        resource=resource,
        session=session,
        is_done=session.id in done,
        next_session=following[0] if following else None,
        done_count=len(done),
        res=RESOURCES,
        tab="read",
        **_base_context(person),
    )


@bp.post("/read/<int:resource_id>/<int:session_id>/done/")
@login_required
def toggle_session(resource_id: int, session_id: int):
    person = current_user.person
    if person is None:
        return redirect(url_for("member.reading"))

    resource = Resource.published_for_member(g.church.id, resource_id)
    session = ResourceSession.get_for_church(g.church.id, session_id)
    if resource is None or session is None or session.resource_id != resource.id:
        abort(404)

    if request.form.get("undo") == "1":
        SessionCompletion.unmark(g.church.id, person.id, session.id)
    else:
        SessionCompletion.mark(g.church.id, person.id, session)
    db.session.commit()

    return redirect(
        url_for("member.read_session", resource_id=resource.id, session_id=session.id)
    )


@bp.get("/give/")
@login_required
def give():
    """The church's own giving page. No money moves through here.

    The Give tab links to the giving page itself, so this route exists for
    anything that still points at /me/give/: an old bookmark, a link in an
    email sent last month. It sends them where the tab would have. When the
    church has not set a giving link yet, the in-app page explains that
    rather than redirecting nowhere.
    """
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    if g.church.giving_form_url:
        return redirect(g.church.giving_form_url)

    return render_template(
        "member/give.html", give=GIVING, tab="give", **_base_context(person)
    )


# ---------------------------------------------------------------------------
# Increment 9: groups
#
# A member sees the groups they belong to. There is no route here that lists
# other people's groups or lets someone RSVP on another person's behalf: the
# person is always `current_user.person`.
# ---------------------------------------------------------------------------

@bp.get("/groups/")
@login_required
def groups():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    mine = db.session.scalars(Group.for_person(g.church.id, person.id)).all()
    return render_template(
        "member/groups.html",
        groups=mine,
        grp=GROUPS,
        rsvp_choices=RSVP_CHOICES,
        tab="groups",
        **_base_context(person),
    )


@bp.post("/groups/<int:meeting_id>/rsvp/")
@login_required
def rsvp(meeting_id: int):
    person = current_user.person
    if person is None:
        return redirect(url_for("member.groups"))

    meeting = GroupMeeting.get_for_church(g.church.id, meeting_id)
    if meeting is None:
        abort(404)

    # Belonging to the group is the authorization. Without this check anyone
    # signed in could answer for a meeting they were never invited to, and the
    # "9 going" count a leader plans around would be wrong.
    if not meeting.group.has_person(person.id):
        abort(403)

    response = (request.form.get("response") or "").strip()
    if response not in RSVP_CHOICES:
        abort(400)

    MeetingRSVP.set(g.church.id, meeting, person.id, response)
    db.session.commit()

    flash(GROUPS["rsvp_saved"].format(response=response.replace("_", " ")), "notice")
    return redirect(url_for("member.groups"))


# ---------------------------------------------------------------------------
# Increment 10: serving
# ---------------------------------------------------------------------------

@bp.get("/serve/")
@login_required
def serve():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    assignments = db.session.scalars(
        ServiceAssignment.upcoming_for_person(g.church.id, person.id)
    ).all()
    from app.models import TeamFile

    return render_template(
        "member/serve.html",
        assignments=assignments,
        team_files=db.session.scalars(TeamFile.for_person(g.church.id, person.id)).all(),
        svc=SERVICES,
        tab="serve",
        **_base_context(person),
    )


@bp.get("/serve/files/<int:file_id>/")
@login_required
def team_file(file_id: int):
    """A team's PDF, for the people on that team.

    404 rather than 403 for everyone else: whether the kids team has a
    curriculum file is the kids team's business.
    """
    from app.blueprints.services import serve_file
    from app.models import TeamFile
    from app.models.service import TeamMembership

    person = current_user.person
    if person is None:
        abort(404)
    record = TeamFile.get_for_church(g.church.id, file_id)
    if record is None:
        abort(404)
    on_team = db.session.scalar(
        db.select(TeamMembership).where(
            TeamMembership.church_id == g.church.id,
            TeamMembership.team_id == record.team_id,
            TeamMembership.person_id == person.id,
        )
    )
    if on_team is None and not current_user.at_least("leader"):
        abort(404)
    return serve_file(record)


@bp.get("/serve/charts/<int:chart_id>/")
@login_required
def song_chart(chart_id: int):
    """A song's chart, for the people scheduled to play it.

    Scheduled and not declined, on a published service whose plan uses the
    song, until the day of that service is over. Leaders and staff can always
    open it. 404 for everyone else, so a chart library is not browsable by
    guessing numbers. See app/models/songchart.py.
    """
    from app.blueprints.services import serve_file
    from app.models import SongChart

    record = SongChart.get_for_church(g.church.id, chart_id)
    if record is None:
        abort(404)
    if current_user.at_least("leader"):
        return serve_file(record)
    person = current_user.person
    if person is None or not SongChart.person_may_open(g.church.id, person.id, record):
        abort(404)
    return serve_file(record)


@bp.post("/serve/<int:assignment_id>/")
@login_required
def respond_to_assignment(assignment_id: int):
    person = current_user.person
    if person is None:
        return redirect(url_for("member.serve"))

    assignment = ServiceAssignment.get_for_church(g.church.id, assignment_id)
    if assignment is None:
        abort(404)

    # Answering for somebody else would put a name on a plan that never agreed
    # to it, and a worship leader would find out on Sunday morning.
    if assignment.person_id != person.id:
        abort(403)

    answer = (request.form.get("answer") or "").strip()
    if answer not in (ACCEPTED, DECLINED):
        abort(400)

    assignment.respond(answer)
    db.session.commit()

    flash(
        SERVICES["member_accepted"].format(position=assignment.role_name)
        if answer == ACCEPTED
        else SERVICES["member_declined"],
        "notice",
    )
    return redirect(url_for("member.serve"))


# ---------------------------------------------------------------------------
# Increment 12: messages
#
# Authorization lives on the conversation, not here. `can_read` and `can_post`
# are the same methods the staff blueprint calls, so the two views cannot
# disagree about who may see what.
# ---------------------------------------------------------------------------

@bp.get("/chat/")
@login_required
def chat():
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    if not current_user.has_accepted_community:
        return _agreement_page(person)

    conversations = db.session.scalars(
        Conversation.visible_to(g.church.id, person)
    ).all()
    blocked = PersonBlock.blocked_ids(g.church.id, person.id)
    return render_template(
        "member/chat.html",
        conversations=conversations,
        unread={c.id: c.unread_for(person.id, blocked) for c in conversations},
        msg=MESSAGES,
        tab="chat",
        **_base_context(person),
    )


@bp.get("/chat/<int:conversation_id>/")
@login_required
def chat_thread(conversation_id: int):
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)
    # 404 rather than 403. Telling somebody a private room exists is itself a
    # disclosure about who is talking to whom.
    if not conversation.can_read(person):
        abort(404)

    if not current_user.has_accepted_community:
        return _agreement_page(person)

    membership = conversation.membership_for(person.id)
    if membership is not None:
        membership.mark_read()
        db.session.commit()

    blocked = PersonBlock.blocked_ids(g.church.id, person.id)
    shown = conversation.messages_for(person.id, blocked)
    hidden = len(conversation.visible_messages) - len(shown)

    return render_template(
        "member/thread.html",
        conversation=conversation,
        messages=shown,
        hidden_count=hidden,
        me=person,
        can_post=conversation.can_post(
            person,
            is_staff=current_user.is_staff,
            is_leader=current_user.at_least("leader"),
        ),
        can_moderate=current_user.at_least("leader"),
        msg=MESSAGES,
        tab="chat",
        **_base_context(person),
    )


@bp.post("/chat/<int:conversation_id>/")
@login_required
def chat_post(conversation_id: int):
    person = current_user.person
    if person is None:
        return redirect(url_for("member.chat"))

    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None or not conversation.can_read(person):
        abort(404)
    if not conversation.can_post(
            person,
            is_staff=current_user.is_staff,
            is_leader=current_user.at_least("leader"),
        ):
        abort(403)

    if not current_user.has_accepted_community:
        return redirect(url_for("member.chat"))

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(MESSAGES["post_empty"], "error")
        return redirect(url_for("member.chat_thread", conversation_id=conversation.id))

    # Refused before it is stored. Nothing is logged with the text: a record
    # of things people almost said is not something a church should keep.
    terms = objectionable_terms(body)
    if terms:
        flash(MESSAGES["filter_refused"].format(terms='", "'.join(terms)), "error")
        return redirect(url_for("member.chat_thread", conversation_id=conversation.id))

    if (
        conversation.is_announcement
        and not current_user.at_least("leader")
        and _announcements_today(person) >= MEMBER_ANNOUNCEMENTS_PER_DAY
    ):
        flash(MESSAGES["announce_limit"].format(count=MEMBER_ANNOUNCEMENTS_PER_DAY), "error")
        return redirect(url_for("member.chat_thread", conversation_id=conversation.id))

    posted = Message.post(conversation, person, body[:4000])
    db.session.flush()
    notify_new_message(conversation, posted, author_person=person)
    db.session.commit()

    return redirect(url_for("member.chat_thread", conversation_id=conversation.id))


# A member can post to the whole church, but not flood it. Staff and leaders
# are not limited: they are the ones who would be cleaning up after it.
MEMBER_ANNOUNCEMENTS_PER_DAY = 5


def _announcements_today(person) -> int:
    from datetime import timedelta

    from sqlalchemy import func

    from app.models.base import utcnow
    from app.models.message import KIND_ANNOUNCEMENT

    return db.session.scalar(
        db.select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.church_id == person.church_id,
            Message.author_person_id == person.id,
            Conversation.kind == KIND_ANNOUNCEMENT,
            Message.sent_at >= utcnow() - timedelta(hours=24),
        )
    ) or 0


@bp.post("/chat/<int:conversation_id>/messages/<int:message_id>/delete/")
@login_required
def delete_message(conversation_id: int, message_id: int):
    """Staff and leaders deleting from the member app, on their phone.

    Same rule and same code path as the staff screen, so a pastor who sees
    something in the group chat on Sunday can take it down without finding
    a laptop.
    """
    from app.blueprints.messages import remove_message

    if not current_user.at_least("leader"):
        abort(403)
    person, conversation, message = _message_this_person_can_see(
        conversation_id, message_id
    )
    remove_message(conversation, message, current_user)
    db.session.commit()
    flash(MESSAGES["deleted"], "notice")
    return redirect(url_for("member.chat_thread", conversation_id=conversation.id))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@bp.post("/you/push/")
@login_required
def subscribe_push():
    """Register this browser for notifications.

    A subscription is per device, not per person. One member has a phone, a
    tablet, and a laptop, and turning notifications off on one must not
    silence the others.
    """
    payload = request.get_json(silent=True) or {}
    endpoint = (payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}

    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        # Without both keys a payload cannot be encrypted for this device, so
        # storing the row would mean a subscription that can never be used.
        return {"ok": False}, 400

    PushSubscription.register(
        g.church.id,
        current_user,
        endpoint=endpoint,
        p256dh=keys["p256dh"],
        auth=keys["auth"],
        label=(payload.get("label") or "")[:120] or None,
    )
    db.session.commit()

    return {"ok": True}


@bp.post("/you/push/off/")
@login_required
def unsubscribe_push():
    payload = request.get_json(silent=True) or {}
    endpoint = (payload.get("endpoint") or "").strip()

    if endpoint:
        digest = PushSubscription.hash_endpoint(endpoint)
        subscription = db.session.scalar(
            db.select(PushSubscription).where(
                PushSubscription.endpoint_hash == digest,
                PushSubscription.church_id == g.church.id,
                # Scoped to the signed-in user, so a stray endpoint cannot be
                # used to unsubscribe somebody else's device.
                PushSubscription.user_id == current_user.id,
            )
        )
        if subscription is not None:
            db.session.delete(subscription)
            db.session.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# Deleting your own account
#
# Required by the app stores for any app that lets people create an account,
# and right regardless. What it deletes is the thing the person created: the
# login. The pastoral record is the church's, in the same way a paper roll
# would be, and deleting it would take a child's check-in history and matched
# giving with it. The screen says so plainly rather than implying more.
# ---------------------------------------------------------------------------

@bp.post("/you/delete/")
@login_required
def delete_account():
    person = current_user.person

    # Re-entered, not just clicked. A phone left unlocked on a table is the
    # normal case, and an irreversible action deserves more than one tap.
    if not current_user.check_password(request.form.get("password") or ""):
        flash(MEMBER["delete_wrong_password"], "error")
        return redirect(url_for("member.you", open="account", _anchor="account"))

    if current_user.is_staff:
        from app.models import User

        remaining = db.session.scalar(
            db.select(db.func.count(User.id)).where(
                User.church_id == g.church.id,
                User.role == "staff",
                User.is_active_account.is_(True),
                User.id != current_user.id,
            )
        )
        if not remaining:
            # Deleting the last staff account locks the church out of its own
            # data with nobody able to undo it.
            flash(MEMBER["delete_last_staff"], "error")
            return redirect(url_for("member.you"))

    audit_record(
        PERSON_ARCHIVED,
        f"{current_user.name} deleted their own account",
        actor=current_user,
        subject_type="user",
        subject_id=current_user.id,
        subject_label=current_user.name,
        detail="The login was removed. The roster record was kept for the church.",
    )

    for subscription in PushSubscription.for_user(g.church.id, current_user.id):
        db.session.delete(subscription)

    if person is not None:
        # Unlinked, not deleted. Staff keep the record; nobody can sign in to
        # it, and a future account with the same address does not silently
        # inherit it, because linking needs a confirmed email either way.
        person.owner_user_id = None

    user = db.session.get(type(current_user._get_current_object()), current_user.id)
    logout_user()
    db.session.delete(user)
    db.session.commit()

    return render_template(
        "member/deleted.html", church=g.church, content=MEMBER
    )



# ---------------------------------------------------------------------------
# Community standards, reporting, and blocking
#
# App Store Guideline 1.2: people agree to terms with no tolerance for
# objectionable content before they post, can report what gets through, and
# can block the person who wrote it. Staff are told about every report and
# every block, because blocking usually means something happened.
#
# No route here takes a person id. A block is made from a message, so the
# person being blocked is whoever wrote something this person could see, and
# an unblock goes by the block's own id, scoped to whoever is asking.
# ---------------------------------------------------------------------------

def _agreement_page(person):
    from app.content import COMMUNITY

    return render_template(
        "member/agree.html",
        community=COMMUNITY,
        msg=MESSAGES,
        tab="chat",
        **_base_context(person),
    )


@bp.post("/chat/agree/")
@login_required
def chat_agree():
    current_user.accept_community()
    db.session.commit()
    flash(MESSAGES["agree_done"], "notice")
    return redirect(url_for("member.chat"))


def _message_this_person_can_see(conversation_id: int, message_id: int):
    """Load a message only if this person could read it.

    404 for anything else, including a message in a private room they are not
    in. Reporting or blocking must not become a way to learn a room exists.
    """
    person = current_user.person
    if person is None:
        abort(404)
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    message = Message.get_for_church(g.church.id, message_id)
    if (
        conversation is None
        or message is None
        or message.conversation_id != conversation.id
        or message.is_deleted
        or not conversation.can_read(person)
    ):
        abort(404)
    return person, conversation, message


def _alert_staff(report, reporter, message, conversation, source: str) -> None:
    """Email every active staff member. Caller commits.

    "Timely responses to concerns" is part of Guideline 1.2, and a report that
    waits for somebody to happen to open a screen is not timely.
    """
    from app.mail import NotQueued, queue
    from app.models import User

    action = (
        MESSAGES["alert_action_block"] if source == SOURCE_BLOCK
        else MESSAGES["alert_action_report"]
    )
    reason = (
        MESSAGES["alert_reason"].format(reason=report.reason) if report.reason else ""
    )
    link = url_for("messages.reports", _external=True,
                   _scheme="https" if request.is_secure else "http")
    staff = db.session.scalars(
        db.select(User).where(
            User.church_id == g.church.id,
            User.role == "staff",
            User.is_active_account.is_(True),
        )
    ).all()
    for user in staff:
        try:
            queue(
                church_id=g.church.id,
                category="moderation",
                subject=MESSAGES["alert_subject"].format(room=conversation.title),
                # The words of the message are deliberately not in the email.
                # Staff read them in the app, where removing them removes them
                # everywhere; a copy in forty inboxes cannot be taken back.
                body_text=MESSAGES["alert_body"].format(
                    reporter=reporter.full_name,
                    action=action,
                    author=message.author_name or "someone",
                    room=conversation.title,
                    reason=reason,
                    link=link,
                    church=g.church.name,
                ),
                to_email=user.email,
                to_name=user.name,
                dedupe_key=f"report:{report.id}:{source}:{user.id}",
            )
        except NotQueued:
            continue


@bp.post("/chat/<int:conversation_id>/messages/<int:message_id>/report/")
@login_required
def report_message(conversation_id: int, message_id: int):
    from app.models.audit import MESSAGE_REPORTED

    person, conversation, message = _message_this_person_can_see(conversation_id, message_id)
    back = url_for("member.chat_thread", conversation_id=conversation.id)

    if message.author_person_id == person.id:
        flash(MESSAGES["report_own"], "error")
        return redirect(back)

    report, created = MessageReport.file(
        message, person, request.form.get("reason"), source=SOURCE_REPORT
    )
    db.session.flush()

    if created:
        audit_record(
            MESSAGE_REPORTED,
            f"A message in {conversation.title} was reported",
            actor=current_user,
            subject_type="message", subject_id=message.id,
            subject_label=conversation.title,
        )
        _alert_staff(report, person, message, conversation, SOURCE_REPORT)
        db.session.commit()
        flash(MESSAGES["report_done"], "notice")
    else:
        db.session.commit()
        flash(MESSAGES["report_again"], "notice")

    return redirect(back)


@bp.post("/chat/<int:conversation_id>/messages/<int:message_id>/block/")
@login_required
def block_author(conversation_id: int, message_id: int):
    """Block whoever wrote this message.

    Acts immediately and needs nobody's permission. Staff are told and a
    report is filed, so a block is never the only record that something
    happened.
    """
    from app.models.audit import PERSON_BLOCKED

    person, conversation, message = _message_this_person_can_see(conversation_id, message_id)
    back = url_for("member.chat_thread", conversation_id=conversation.id)

    author = message.author
    if author is None:
        flash(MESSAGES["block_staff_author"], "error")
        return redirect(back)
    if author.id == person.id:
        flash(MESSAGES["block_self"], "error")
        return redirect(back)

    _, created = PersonBlock.add(person, author)
    report, _ = MessageReport.file(message, person, None, source=SOURCE_BLOCK)
    db.session.flush()

    if created:
        audit_record(
            PERSON_BLOCKED,
            f"{person.full_name} blocked {author.full_name}",
            actor=current_user,
            subject_type="person", subject_id=author.id,
            subject_label=author.full_name,
            detail=f"From a message in {conversation.title}.",
        )
        _alert_staff(report, person, message, conversation, SOURCE_BLOCK)

    db.session.commit()
    flash(MESSAGES["block_done"].format(name=author.full_name), "notice")
    return redirect(back)


@bp.post("/you/blocks/<int:block_id>/remove/")
@login_required
def unblock(block_id: int):
    person = current_user.person
    if person is None:
        abort(404)
    block = PersonBlock.get_for_blocker(g.church.id, person.id, block_id)
    if block is None:
        abort(404)

    name = block.blocked_name or "them"
    db.session.delete(block)
    db.session.commit()

    flash(MESSAGES["unblocked"].format(name=name), "notice")
    return redirect(url_for("member.you", open="blocked", _anchor="blocked"))
