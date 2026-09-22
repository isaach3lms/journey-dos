"""Messaging.

Authorization is on the conversation, not on the route. `can_read` and
`can_post` are model methods so the staff view, the member view, and any future
caller cannot disagree about who may do what. A rule duplicated in two
blueprints is a rule that will eventually be enforced in one of them.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.audit import record
from app.content import MESSAGES
from app.models.audit import CHAT_DELETED, MESSAGE_DELETED, REPORT_RESOLVED
from app.models.moderation import (
    REPORT_DISMISSED,
    REPORT_OPEN,
    REPORT_REMOVED,
    MessageReport,
)
from app.chat_notify import notify_new_message
from app.moderation import objectionable_terms
from app.extensions import db
from app.mail import NotQueued, queue
from app.models import (
    CONVERSATION_KINDS,
    KIND_ANNOUNCEMENT,
    KIND_ROOM,
    Conversation,
    ConversationMember,
    Message,
    Person,
)
from app.security import min_role

bp = Blueprint("messages", __name__, url_prefix="/messages")


def _acting_person():
    """The Person behind the signed-in staff user, if there is one.

    Staff without a roster record can still post; the message records their
    name and no person id. That is better than refusing to let a pastor
    announce something because nobody linked his login yet.
    """
    return current_user.person


@bp.get("/")
@login_required
@min_role("leader")
def index():
    return render_template(
        "messages/index.html",
        church=g.church,
        content=MESSAGES,
        conversations=db.session.scalars(Conversation.for_church(g.church.id)).all(),
        recent=db.session.scalars(Message.recent_for_church(g.church.id)).all(),
        kinds=(KIND_ANNOUNCEMENT, KIND_ROOM),
        open_reports=MessageReport.open_count(g.church.id),
        active="messages",
    )


@bp.post("/")
@login_required
@min_role("leader")
def create():
    title = (request.form.get("title") or "").strip()
    if not title:
        flash(MESSAGES["title_required"], "error")
        return redirect(url_for("messages.index"))

    kind = (request.form.get("kind") or KIND_ROOM).strip()
    # Direct conversations are started from a person, never from this form.
    if kind not in (KIND_ANNOUNCEMENT, KIND_ROOM):
        abort(400)

    conversation = Conversation(
        church_id=g.church.id, kind=kind, title=title[:160],
        topic=(request.form.get("topic") or "").strip() or None,
    )
    db.session.add(conversation)
    db.session.commit()

    flash(MESSAGES["created"].format(title=conversation.title), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


@bp.get("/<int:conversation_id>/")
@login_required
@min_role("leader")
def thread(conversation_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    person = _acting_person()
    mine = {
        message.id
        for message in conversation.messages
        if _is_own(message, person, current_user.name)
    }

    in_room = {m.person_id for m in conversation.members}
    candidates = [
        person
        for person in db.session.scalars(Person.for_church(g.church.id))
        if person.id not in in_room
    ]

    return render_template(
        "messages/thread.html",
        church=g.church,
        content=MESSAGES,
        conversation=conversation,
        candidates=candidates,
        mine=mine,
        can_post=conversation.can_post(
            person,
            is_staff=current_user.is_staff,
            is_leader=current_user.at_least("leader"),
        ),
        active="messages",
    )


def _is_own(message, person, name: str | None) -> bool:
    """Is this staff member the one who wrote it?

    A staff login may have no roster record, in which case the only trace of
    who wrote a message is the name stored on it. Matching on that is enough
    to decide which side of the thread a bubble sits on, and it decides
    nothing else.
    """
    if person is not None and message.author_person_id is not None:
        return message.author_person_id == person.id
    if message.author_person_id is None:
        return bool(name) and message.author_name == name
    return False


@bp.post("/<int:conversation_id>/post/")
@login_required
@min_role("leader")
def post(conversation_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    person = _acting_person()
    if not conversation.can_post(
        person,
        is_staff=current_user.is_staff,
        is_leader=current_user.at_least("leader"),
    ):
        abort(403)

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(MESSAGES["post_empty"], "error")
        return redirect(url_for("messages.thread", conversation_id=conversation.id))

    terms = objectionable_terms(body)
    if terms:
        flash(MESSAGES["filter_refused"].format(terms='", "'.join(terms)), "error")
        return redirect(url_for("messages.thread", conversation_id=conversation.id))

    posted = Message.post(conversation, person, body[:4000], author_name=current_user.name)
    db.session.flush()
    notify_new_message(conversation, posted, author_person=person, author_name=current_user.name)

    queued = 0
    if request.form.get("also_email") == "on" and conversation.is_announcement:
        queued = _email_announcement(conversation, body)

    db.session.commit()

    flash(
        MESSAGES["emailed"].format(count=queued) if queued else MESSAGES["posted"],
        "notice",
    )
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


def _email_announcement(conversation, body: str) -> int:
    """Send an announcement by email as well as posting it.

    Under the `announcement` category, which is opt-out-able. Somebody who
    turned church announcements off still sees it in the app; they simply do
    not get a second copy in their inbox, which is what they asked for.
    """
    queued = 0
    for person in db.session.scalars(Person.for_church(g.church.id)):
        if not person.email or not person.allows("announcement"):
            continue
        try:
            message = queue(
                church_id=g.church.id,
                category="announcement",
                subject=MESSAGES["email_subject"].format(
                    church=g.church.name, title=conversation.title
                ),
                body_text=body,
                person=person,
                dedupe_key=f"announcement:{conversation.id}:{len(conversation.messages)}"
                f":person:{person.id}",
            )
        except NotQueued:
            continue
        if message is not None:
            person.ensure_unsubscribe_token()
            queued += 1
    return queued


@bp.post("/<int:conversation_id>/messages/<int:message_id>/delete/")
@login_required
@min_role("leader")
def delete_message(conversation_id: int, message_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    message = Message.get_for_church(g.church.id, message_id)
    if conversation is None or message is None or message.conversation_id != conversation.id:
        abort(404)

    remove_message(conversation, message, current_user)
    db.session.commit()

    flash(MESSAGES["deleted"], "notice")
    # From the monitoring list, go back to the list, not into the room.
    if request.form.get("back") == "recent":
        return redirect(url_for("messages.index", _anchor="recent"))
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


def remove_message(conversation, message, actor) -> None:
    """Delete one message, close its reports, and record who did it.

    The one path for every delete, from the staff screen, the monitoring
    list, or a leader's phone, so they cannot drift apart. Caller commits.
    """
    author = message.author_name or "someone"
    message.soft_delete()
    _close_reports_for(message, REPORT_REMOVED)
    record(
        MESSAGE_DELETED,
        f"A message from {author} was deleted in {conversation.title}",
        actor=actor,
        subject_type="message",
        subject_id=message.id,
        subject_label=conversation.title,
    )


@bp.post("/<int:conversation_id>/delete/")
@login_required
@min_role("staff")
def delete_chat(conversation_id: int):
    """Remove a whole chat.

    Staff only. Every message is cleared the same way a single delete clears
    one, the chat is closed so nobody can post, and it drops off every
    member's list. The rows stay so the audit log and any report still point
    at something, which is also why this is not a hard delete.
    """
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    cleared = 0
    for message in conversation.messages:
        if not message.is_deleted:
            message.soft_delete()
            _close_reports_for(message, REPORT_REMOVED)
            cleared += 1
    conversation.is_archived = True
    record(
        CHAT_DELETED,
        f"{conversation.title} was deleted with {cleared} messages",
        actor=current_user,
        subject_type="conversation",
        subject_id=conversation.id,
        subject_label=conversation.title,
    )
    db.session.commit()

    flash(MESSAGES["chat_deleted"].format(title=conversation.title, count=cleared), "notice")
    return redirect(url_for("messages.index"))


@bp.post("/<int:conversation_id>/members/")
@login_required
@min_role("leader")
def add_member(conversation_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)
    if conversation.is_announcement:
        # An announcement has no membership rows on purpose. Adding one would
        # imply the others are excluded.
        abort(400)

    person_id = request.form.get("person_id", type=int)
    person = Person.get_for_church(g.church.id, person_id) if person_id else None
    if person is None:
        abort(400)

    if conversation.membership_for(person.id) is not None:
        flash(MESSAGES["already_in"].format(name=person.full_name), "error")
        return redirect(url_for("messages.thread", conversation_id=conversation.id))

    db.session.add(
        ConversationMember(
            church_id=g.church.id,
            conversation_id=conversation.id,
            person_id=person.id,
        )
    )
    db.session.commit()

    flash(MESSAGES["member_added"].format(name=person.full_name), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


@bp.post("/<int:conversation_id>/members/<int:member_id>/delete/")
@login_required
@min_role("leader")
def remove_member(conversation_id: int, member_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    membership = db.session.scalar(
        db.select(ConversationMember).where(
            ConversationMember.id == member_id,
            ConversationMember.church_id == g.church.id,
            ConversationMember.conversation_id == conversation.id,
        )
    )
    if membership is None:
        abort(404)

    name = membership.person.full_name if membership.person else "Someone"
    db.session.delete(membership)
    db.session.commit()

    flash(MESSAGES["member_removed"].format(name=name), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


@bp.post("/<int:conversation_id>/archive/")
@login_required
@min_role("leader")
def archive(conversation_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    conversation.is_archived = not conversation.is_archived
    db.session.commit()

    flash(MESSAGES["archived"].format(title=conversation.title), "notice")
    return redirect(url_for("messages.index"))



# ---------------------------------------------------------------------------
# Reported messages
#
# A report is a request for a human, not an automatic takedown. One report
# does not remove a message, because in a room of twelve people one unhappy
# reader could silence anybody. Somebody here decides, and the decision is
# audited.
# ---------------------------------------------------------------------------

def _close_reports_for(message, status: str) -> int:
    closed = 0
    for report in db.session.scalars(
        db.select(MessageReport).where(
            MessageReport.message_id == message.id,
            MessageReport.status == REPORT_OPEN,
        )
    ):
        report.resolve(status, current_user)
        closed += 1
    return closed


@bp.get("/reports/")
@login_required
@min_role("leader")
def reports():
    return render_template(
        "messages/reports.html",
        church=g.church,
        content=MESSAGES,
        open_reports=db.session.scalars(MessageReport.open_for_church(g.church.id)).all(),
        closed_reports=db.session.scalars(MessageReport.recent_closed(g.church.id)).all(),
        conversations={
            c.id: c for c in db.session.scalars(
                Conversation.for_church(g.church.id, include_archived=True)
            )
        },
        active="messages",
    )


@bp.post("/reports/<int:report_id>/<decision>/")
@login_required
@min_role("leader")
def decide_report(report_id: int, decision: str):
    report = MessageReport.get_for_church(g.church.id, report_id)
    if report is None:
        abort(404)
    if decision not in ("remove", "keep"):
        abort(400)

    message = report.message
    conversation = Conversation.get_for_church(g.church.id, report.conversation_id)
    room = conversation.title if conversation else "a room"

    if decision == "remove":
        if message is not None and not message.is_deleted:
            author = message.author_name or "someone"
            message.soft_delete()
            record(
                MESSAGE_DELETED,
                f"A message from {author} was deleted in {room}",
                actor=current_user,
                subject_type="message", subject_id=message.id, subject_label=room,
                detail="Removed after a report.",
            )
        # Every report on the same message closes together. Three people
        # reporting one message is one decision, not three.
        if message is not None:
            _close_reports_for(message, REPORT_REMOVED)
        report.resolve(REPORT_REMOVED, current_user)
        flash(MESSAGES["report_removed"], "notice")
    else:
        report.resolve(REPORT_DISMISSED, current_user)
        flash(MESSAGES["report_kept"], "notice")

    record(
        REPORT_RESOLVED,
        f"A report in {room} was decided: "
        + ("removed" if decision == "remove" else "kept"),
        actor=current_user,
        subject_type="message_report", subject_id=report.id, subject_label=room,
    )
    db.session.commit()
    return redirect(url_for("messages.reports"))
