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
from app.broadcast import EVERYONE, audience_label, audiences, resolve
from app.broadcast import send as send_broadcast
from app.content import MESSAGES
from app.models.audit import (
    CHAT_DELETED,
    CHURCH_EMAIL_SENT,
    MESSAGE_DELETED,
    REPORT_RESOLVED,
)
from app.models.moderation import (
    REPORT_DISMISSED,
    REPORT_OPEN,
    REPORT_REMOVED,
    MessageReport,
)
from app.chat_notify import notify_announcement, notify_new_message
from app.moderation import objectionable_terms
from app.extensions import db
from app.models import (
    CONVERSATION_KINDS,
    EmailTemplate,
    KIND_ANNOUNCEMENT,
    KIND_ROOM,
    Conversation,
    ConversationMember,
    Message,
    Person,
)
from app.models.email_template import TEMPLATE_MESSAGE
from app.saved_templates import (
    count_use as count_template_use,
    prefill as prefill_from_template,
    save_from_form as save_template,
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


def _sidebar(selected_id: int | None = None) -> dict:
    """Everything the left column needs, for whichever page is rendering it.

    One function because the list is on both the index and a thread, and a
    sidebar that disagreed with itself between the two would be a sidebar
    that loses your unread count the moment you click something.
    """
    person = _acting_person()
    person_id = getattr(person, "id", None)

    groups = db.session.scalars(Conversation.groups_for_church(g.church.id)).all()
    directs = db.session.scalars(
        Conversation.directs_for(g.church.id, person_id)
    ).all()

    return {
        "groups": groups,
        "directs": directs,
        "unread": {
            conversation.id: conversation.unread_for(person_id)
            for conversation in list(groups) + list(directs)
        } if person_id else {},
        "selected_id": selected_id,
        "me": person,
        "my_person_id": person_id,
        # Who a new direct thread could be opened with. Staff and leaders
        # only, so the list is built once here rather than on every render of
        # a form a member will never see.
        "direct_candidates": (
            _direct_candidates(person_id)
            if Conversation.may_start_direct(
                is_staff=current_user.is_staff,
                is_leader=current_user.at_least("leader"),
            )
            else []
        ),
        "may_start_direct": Conversation.may_start_direct(
            is_staff=current_user.is_staff,
            is_leader=current_user.at_least("leader"),
        ),
    }


def _direct_candidates(person_id: int | None):
    """People a direct thread can be started with.

    Everybody on the roster except the person doing the starting. Archived
    people are already out, because `Person.for_church` leaves them out.
    """
    return [
        person
        for person in db.session.scalars(Person.for_church(g.church.id))
        if person.id != person_id
    ]


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
        **_sidebar(),
    )


@bp.post("/direct/")
@login_required
@min_role("leader")
def start_direct():
    """Open a one-to-one thread with somebody, or go to the existing one.

    Idempotent by design: `find_or_create_direct` is keyed on the pair, so
    pressing this twice lands in the same conversation rather than making a
    second one that splits the history.
    """
    if not Conversation.may_start_direct(
        is_staff=current_user.is_staff,
        is_leader=current_user.at_least("leader"),
    ):
        abort(403)

    me = _acting_person()
    if me is None:
        # A staff login with no roster record has no identity to be the other
        # half of a private conversation. Rooms work for them; this cannot.
        flash(MESSAGES["direct_needs_person"], "error")
        return redirect(url_for("messages.index"))

    person_id = request.form.get("person_id", type=int)
    other = Person.get_for_church(g.church.id, person_id) if person_id else None
    if other is None:
        flash(MESSAGES["direct_nobody"], "error")
        return redirect(url_for("messages.index"))

    if other.id == me.id:
        flash(MESSAGES["direct_self"], "error")
        return redirect(url_for("messages.index"))

    conversation, created = Conversation.find_or_create_direct(
        g.church.id, me.id, other.id,
        title=f"{me.full_name} and {other.full_name}",
    )
    db.session.commit()

    if created:
        flash(MESSAGES["direct_started"].format(name=other.full_name), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


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

    # A leader may open any room the church runs. A private thread between
    # two other people is not one of those, and the role that lets somebody
    # moderate a group chat is not a key to everybody's direct messages.
    # Reports are the route for a direct thread somebody needs looked at.
    if conversation.is_direct and not conversation.can_read(person):
        abort(404)

    mine = {
        message.id
        for message in conversation.messages
        if _is_own(message, person, current_user.name)
    }

    in_room = {m.person_id for m in conversation.members}
    candidates = [] if conversation.is_direct else [
        candidate
        for candidate in db.session.scalars(Person.for_church(g.church.id))
        if candidate.id not in in_room
    ]

    # Opening it is reading it.
    membership = conversation.membership_for(person.id) if person else None
    if membership is not None:
        membership.mark_read()
        db.session.commit()

    # Saved messages, for a thread with one person in it. Filled with that
    # person here rather than at send time, so what staff read in the box and
    # what lands in their thread are the same words.
    other = conversation.other_member(person.id) if person else None
    writing_to = other.person if other is not None else None

    return render_template(
        "messages/thread.html",
        church=g.church,
        content=MESSAGES,
        conversation=conversation,
        candidates=candidates,
        mine=mine,
        writing_to=writing_to,
        templates=db.session.scalars(
            EmailTemplate.for_church(g.church.id, kind=TEMPLATE_MESSAGE)
        ).all() if conversation.is_direct else [],
        prefill=prefill_from_template(
            writing_to, kind=TEMPLATE_MESSAGE
        ) if writing_to is not None else {},
        **_sidebar(selected_id=conversation.id),
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

    # See `Message.repeat_of`. Staff post from the same slow page members do.
    if Message.repeat_of(conversation, person, body[:4000],
                         author_name=current_user.name) is not None:
        return redirect(url_for("messages.thread", conversation_id=conversation.id))

    posted = Message.post(conversation, person, body[:4000], author_name=current_user.name)
    db.session.flush()

    # Keeping it, if this is one worth sending again. Only on a direct
    # thread: a saved template is something written to a person, and the
    # placeholders in it have nobody to stand for in a room of thirty.
    saved = None
    if conversation.is_direct and person is not None:
        other = conversation.other_member(person.id)
        writing_to = other.person if other is not None else None
        if writing_to is not None:
            saved = save_template(writing_to, body, kind=TEMPLATE_MESSAGE)
            count_template_use(kind=TEMPLATE_MESSAGE)

    queued = 0
    if conversation.is_announcement:
        # Both channels through one call, with the tick box choosing only
        # whether email is one of them. An announcement pushed nobody at all
        # before this: the chat notifier excludes announcements, and the only
        # thing here was the email. See app/chat_notify.py.
        queued, _ = notify_announcement(
            conversation, posted, body,
            also_email=request.form.get("also_email") == "on",
        )
    else:
        notify_new_message(conversation, posted, author_person=person,
                           author_name=current_user.name)

    db.session.commit()

    flash(
        MESSAGES["emailed"].format(count=queued) if queued else MESSAGES["posted"],
        "notice",
    )
    if saved is not None:
        flash(MESSAGES["template_saved"].format(name=saved.name), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


# Emailing an announcement used to live here as its own loop, which is how it
# came to push nobody: this function was the whole of "tell the church
# something was posted", and it only knew about email. It now lives in
# app/chat_notify.py as `notify_announcement`, which does both channels and
# takes the tick box as an argument.


# ---------------------------------------------------------------------------
# A church-wide email
#
# Separate from posting an announcement, because the two are different acts.
# An announcement is something the church says, and it stays on a screen.
# This is a letter, it goes to an audience staff chose, and once it is in
# somebody's inbox it cannot be edited or taken down. That asymmetry is why
# this screen shows the recipient count before sending and records the send in
# the audit log, and why posting does neither.
# ---------------------------------------------------------------------------

@bp.get("/email/")
@login_required
@min_role("staff")
def compose_email():
    """Staff only, not leaders.

    A leader runs a room. Reaching the whole church in their inbox is a
    different level of authority, and the person who has it is on staff.
    """
    return render_template(
        "messages/email.html",
        church=g.church,
        content=MESSAGES,
        audiences=audiences(g.church.id),
        # Everybody, children included. The audience filters are about what a
        # mass send should sweep up; this list is for naming one person.
        people=db.session.scalars(Person.for_church(g.church.id)).all(),
        active="messages",
    )


@bp.post("/email/")
@login_required
@min_role("staff")
def send_email():
    audience = (request.form.get("audience") or EVERYONE).strip()

    # One person is an audience of one. Everything below this line, the
    # filter, the recipient check, the duplicate guard, the audit entry and
    # the send itself, runs exactly as it does for the whole church.
    one_person = None
    if (request.form.get("mode") or "").strip() == "person":
        person_id = request.form.get("person_id", type=int)
        one_person = Person.get_for_church(g.church.id, person_id) if person_id else None
        if one_person is None:
            flash(MESSAGES["direct_nobody"], "error")
            return redirect(url_for("messages.compose_email"))
        audience = f"person:{one_person.id}"

    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()

    if not subject or not body:
        flash(MESSAGES["email_needs_both"], "error")
        return redirect(url_for("messages.compose_email"))

    # The same filter that applies to a message in a room. A church-wide email
    # is the last place something should slip through unread by a human.
    terms = objectionable_terms(f"{subject}\n{body}")
    if terms:
        flash(MESSAGES["filter_refused"].format(terms='", "'.join(terms)), "error")
        return redirect(url_for("messages.compose_email"))

    if not resolve(g.church.id, audience):
        flash(
            MESSAGES["email_person_nobody"] if one_person is not None
            else MESSAGES["email_nobody"],
            "error",
        )
        return redirect(url_for("messages.compose_email"))

    label = audience_label(g.church.id, audience)
    sent = send_broadcast(
        church_id=g.church.id,
        audience=audience,
        subject=subject[:200],
        body_text=body[:20000],
        push=request.form.get("also_push") == "on",
        actor=current_user,
        # The audience, the subject, and the minute. Two sends of the same
        # thing in one minute are the double-clicked button, and the second is
        # dropped. The same email deliberately sent again an hour later is a
        # different send and goes.
        reference=f"{audience}:{subject[:60]}:{_minute_stamp()}",
    )

    record(
        CHURCH_EMAIL_SENT,
        f"{sent.emailed} emails sent to {label}: {subject[:80]}",
        actor=current_user,
        subject_type="church_email",
        subject_label=label,
    )
    db.session.commit()

    if not sent.emailed:
        flash(MESSAGES["email_already_sent"], "error")
        return redirect(url_for("messages.compose_email"))

    flash(
        MESSAGES["email_one_sent"].format(name=label) if one_person is not None
        else MESSAGES["email_sent"].format(count=sent.emailed, audience=label),
        "notice",
    )
    return redirect(url_for("messages.index"))


def _minute_stamp() -> str:
    from app.models.base import utcnow

    return utcnow().strftime("%Y%m%d%H%M")


@bp.post("/<int:conversation_id>/templates/<int:template_id>/delete/")
@login_required
@min_role("leader")
def delete_template(conversation_id: int, template_id: int):
    """Take a saved message out of the picker.

    Archived rather than deleted, for the same reason the email one is: the
    messages already sent from it are in people's threads, and a church
    asking what it said to new visitors last spring should not find the
    answer gone because somebody tidied a list.
    """
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    template = EmailTemplate.get_for_church(
        g.church.id, template_id, kind=TEMPLATE_MESSAGE
    )
    if conversation is None or template is None:
        abort(404)

    template.is_archived = True
    db.session.commit()

    flash(MESSAGES["template_removed"].format(name=template.name), "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


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
    if conversation.is_direct:
        # A direct thread is two people, and `direct_pair` is unique on that
        # pair. A third member would make the title a lie and the key wrong,
        # and the two people in it did not agree to an audience. Start a room.
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
