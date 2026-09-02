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

from app.content import MESSAGES
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
        kinds=(KIND_ANNOUNCEMENT, KIND_ROOM),
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
        active="messages",
    )


@bp.post("/<int:conversation_id>/post/")
@login_required
@min_role("leader")
def post(conversation_id: int):
    conversation = Conversation.get_for_church(g.church.id, conversation_id)
    if conversation is None:
        abort(404)

    person = _acting_person()
    if not conversation.can_post(
        getattr(person, "id", -1), is_staff=current_user.is_staff
    ):
        abort(403)

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(MESSAGES["post_empty"], "error")
        return redirect(url_for("messages.thread", conversation_id=conversation.id))

    Message.post(conversation, person, body[:4000])

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

    message.soft_delete()
    db.session.commit()

    flash(MESSAGES["deleted"], "notice")
    return redirect(url_for("messages.thread", conversation_id=conversation.id))


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
