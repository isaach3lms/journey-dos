"""The member app.

The same database, the same brand, a different reader. A member sees their own
record and nothing else, and that is enforced by only ever loading
`current_user.person` rather than by taking an id from a URL. There is no route
in this blueprint that accepts a person id, so there is no way to ask it for
somebody else's record.

Staff and leaders can reach it too, showing their own record. That is a preview
of what the church's people see, not impersonation: there is deliberately no
way for a staff member to view the member app *as* another person. Reading a
member's screen through their eyes would mean a staff account could see a
private view with no audit trail, and nothing in this increment needs it.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
from app.models import PushSubscription
from app.models.audit import PERSON_ARCHIVED
from app.models.message import Conversation, Message
from app.models.service import ACCEPTED, DECLINED, ServiceAssignment
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

    return render_template(
        "member/home.html",
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
        categories=CATEGORIES,
        optional_categories=OPTIONAL_CATEGORIES,
        push_devices=PushSubscription.device_count(g.church.id, current_user.id),
        vapid_public_key=current_app.config.get("VAPID_PUBLIC_KEY", ""),
        tab="you",
        **_base_context(person),
    )


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
    return redirect(url_for("member.you"))


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

    return redirect(url_for("member.you"))


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
    """A link out to the church's own giving page. No money moves through here."""
    person = current_user.person
    if person is None:
        return render_template("member/unlinked.html", church=g.church, content=MEMBER)

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
    return render_template(
        "member/serve.html",
        assignments=assignments,
        svc=SERVICES,
        tab="serve",
        **_base_context(person),
    )


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

    conversations = db.session.scalars(
        Conversation.visible_to(g.church.id, person)
    ).all()
    return render_template(
        "member/chat.html",
        conversations=conversations,
        unread={c.id: c.unread_for(person.id) for c in conversations},
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

    membership = conversation.membership_for(person.id)
    if membership is not None:
        membership.mark_read()
        db.session.commit()

    return render_template(
        "member/thread.html",
        conversation=conversation,
        can_post=conversation.can_post(person, is_staff=current_user.is_staff),
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
    if not conversation.can_post(person, is_staff=current_user.is_staff):
        abort(403)

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(MESSAGES["post_empty"], "error")
        return redirect(url_for("member.chat_thread", conversation_id=conversation.id))

    Message.post(conversation, person, body[:4000])
    db.session.commit()

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
        return redirect(url_for("member.you"))

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
