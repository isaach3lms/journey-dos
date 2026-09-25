"""The roster.

Every query in this module is scoped to `g.church.id`. There is no exception
and there is no code path that loads a person by primary key alone. The
model-side helpers are what enforce it; this module never builds its own
`select(Person)`.
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
from datetime import date

from flask_login import current_user, login_required

from app.content import AUTOMATION, EMAIL, GIVING, PEOPLE, STUCK
from app.extensions import db
from app.categories import CATEGORIES, OPTIONAL_CATEGORIES
from app.models import (
    CONTACT_METHODS,
    KIND_CONTACT,
    KIND_CREATED,
    KIND_EMAIL,
    KIND_NEXT_STEP,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_OPEN,
    ContactLog,
    KIND_NOTE,
    KIND_STAGE_CHANGE,
    ExternalGift,
    ExternalRecurringGift,
    NextStep,
    OutboxMessage,
    Person,
    PersonEvent,
    SequenceEnrollment,
    User,
)
from app.models.base import utcnow
from app.audit import record as audit_record
from app.automation import enroll_for_stage, on_contact_logged, on_stage_changed
from app.models.audit import SEQUENCE_STOPPED
from app.mail import NotQueued, opt_in, opt_out, queue
from app.security import min_role
from app.stages import (
    CONTACT_WINDOW_DAYS,
    STAGE_BY_CODE,
    is_forward,
    next_stage,
    KIDS,
    recommended_next_step,
    stage_label,
    stages_for,
)

bp = Blueprint("people", __name__, url_prefix="/people")

PAGE_SIZE = 25


@bp.get("/")
@login_required
@min_role("leader")
def index():
    term = (request.args.get("q") or "").strip()
    stage = (request.args.get("stage") or "").strip() or None
    page = max(1, request.args.get("page", type=int) or 1)

    if stage and stage not in STAGE_BY_CODE and stage != KIDS:
        # An unknown stage in the query string is a typo or a probe. Showing
        # everyone would silently misreport the filter, so refuse instead.
        abort(404)

    # Kids are a filter, not a stage. Filtering by a real stage excludes them
    # for the same reason the rail does not count them into one.
    children = None
    if stage == KIDS:
        children, stage = True, None
    elif stage:
        children = False

    query = Person.search(g.church.id, term=term, stage=stage, children=children)
    pagination = db.paginate(query, page=page, per_page=PAGE_SIZE, error_out=False)

    return render_template(
        "people/index.html",
        church=g.church,
        content=PEOPLE,
        people=pagination.items,
        pagination=pagination,
        stages=stages_for(g.church),
        counts=Person.stage_counts(g.church.id),
        kids_count=Person.child_count(g.church.id),
        kids_filter=KIDS,
        total=Person.total_for_church(g.church.id),
        active_stage=request.args.get("stage") or None,
        term=term,
        waiting=db.session.scalars(
            Person.waiting_for_approval(g.church.id)
        ).all(),
        archived_count=Person.archived_count(g.church.id),
        active="people",
    )


@bp.get("/<int:person_id>/")
@login_required
@min_role("leader")
def detail(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        # 404, not 403. Telling one church that a person id exists somewhere
        # else is itself a disclosure.
        abort(404)

    events = db.session.scalars(
        PersonEvent.for_person(g.church.id, person.id)
    ).all()

    household_members = []
    if person.household is not None:
        household_members = [
            member for member in person.household.members if member.id != person.id
        ]

    return render_template(
        "people/detail.html",
        church=g.church,
        content=PEOPLE,
        stuck=STUCK,
        person=person,
        events=events,
        contacts=db.session.scalars(
            ContactLog.for_person(g.church.id, person.id)
        ).all(),
        open_steps=db.session.scalars(
            NextStep.open_for_person(g.church.id, person.id)
        ).all(),
        recommended=recommended_next_step(person.stage),
        assignable=_assignable_users(),
        contact_methods=CONTACT_METHODS,
        waiting_for_approval=person.is_waiting_for_approval,
        email=EMAIL,
        categories=CATEGORIES,
        optional_categories=OPTIONAL_CATEGORIES,
        emails=db.session.scalars(
            OutboxMessage.for_person(g.church.id, person.id)
        ).all(),
        giving=GIVING,
        gifts=db.session.scalars(
            ExternalGift.for_person(g.church.id, person.id)
        ).all(),
        giving_summary=ExternalGift.person_summary(g.church.id, person.id),
        recurring=db.session.scalars(
            ExternalRecurringGift.for_person(g.church.id, person.id)
        ).all(),
        enrollments=db.session.scalars(
            SequenceEnrollment.for_person(g.church.id, person.id)
        ).all(),
        automation=AUTOMATION,
        household_members=household_members,
        stages=stages_for(g.church),
        next_stage=next_stage(person.stage),
        active="people",
    )


def _assignable_users():
    """Staff and leaders at this church. A member cannot own a next step."""
    return db.session.scalars(
        db.select(User)
        .where(
            User.church_id == g.church.id,
            User.role.in_(("staff", "leader")),
            User.is_active_account.is_(True),
        )
        .order_by(User.name)
    ).all()


@bp.post("/<int:person_id>/stage/")
@login_required
@min_role("leader")
def move_stage(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    target = (request.form.get("stage") or "").strip()
    if target not in STAGE_BY_CODE:
        abort(400)

    if target == person.stage:
        return redirect(url_for("people.detail", person_id=person.id))

    previous = person.stage
    direction = "forward" if is_forward(previous, target) else "back"

    person.stage = target
    # Resetting the clock is the point. Increment 3's stuck engine measures
    # time in the current stage, so a move has to restart it or a person who
    # just advanced would immediately read as stuck.
    person.stage_since = utcnow()

    PersonEvent.record(
        person,
        KIND_STAGE_CHANGE,
        PEOPLE["stage_moved"].format(
            frm=stage_label(previous), to=stage_label(target)
        ),
        detail=PEOPLE["stage_moved_detail"].format(direction=direction),
        actor=current_user,
    )
    # Hard stop two, and the ordinary case of arriving somewhere new.
    on_stage_changed(person, previous, actor=current_user)
    db.session.commit()

    flash(
        PEOPLE["stage_flash"].format(
            name=person.first_name, stage=stage_label(target)
        ),
        "notice",
    )
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/note/")
@login_required
@min_role("leader")
def add_note(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    body = (request.form.get("body") or "").strip()
    if not body:
        flash(PEOPLE["note_empty"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    PersonEvent.record(
        person,
        KIND_NOTE,
        body[:255],
        detail=body if len(body) > 255 else None,
        actor=current_user,
    )
    db.session.commit()

    flash(PEOPLE["note_saved"], "notice")
    return redirect(url_for("people.detail", person_id=person.id))


# ---------------------------------------------------------------------------
# Increment 3: contact, next steps, ownership
# ---------------------------------------------------------------------------

@bp.get("/stuck/")
@login_required
@min_role("leader")
def stuck_list():
    people = db.session.scalars(Person.stuck(g.church.id)).all()
    return render_template(
        "people/stuck.html",
        church=g.church,
        content=PEOPLE,
        stuck=STUCK,
        people=people,
        window=CONTACT_WINDOW_DAYS,
        active="people",
    )


@bp.post("/<int:person_id>/contact/")
@login_required
@min_role("leader")
def log_contact(person_id: int):
    """Record that a human actually talked to this person.

    This is the hard stop. Logging contact updates `last_contact_at`, which is
    what clears a stuck flag. A note does not, deliberately: writing "should
    call Marcus" in the timeline is not calling Marcus, and a system that
    treats them the same will quietly stop flagging the people it exists to
    flag.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    method = (request.form.get("method") or "").strip()
    if method not in CONTACT_METHODS:
        abort(400)

    summary = (request.form.get("summary") or "").strip()
    if not summary:
        flash(STUCK["contact_empty"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    now = utcnow()
    contact = ContactLog(
        church_id=person.church_id,
        person_id=person.id,
        method=method,
        summary=summary[:255],
        detail=summary if len(summary) > 255 else None,
        occurred_at=now,
        logged_by_user_id=current_user.id,
        logged_by_name=current_user.name,
    )
    db.session.add(contact)

    # Only ever move forward. Backfilling an older conversation must not make
    # a person look more recently contacted than they are.
    if person.last_contact_at is None or now > person.last_contact_at:
        person.last_contact_at = now

    PersonEvent.record(
        person,
        KIND_CONTACT,
        f"{contact.method_label}: {summary[:180]}",
        actor=current_user,
        occurred_at=now,
    )
    # Hard stop one. A human stepped in, so the system steps back.
    on_contact_logged(person, actor=current_user)
    db.session.commit()

    flash(STUCK["contact_saved"].format(name=person.first_name), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/step/")
@login_required
@min_role("leader")
def assign_step(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    title = (request.form.get("title") or "").strip()
    if not title:
        flash(STUCK["step_title_required"], "error")
        return redirect(url_for("people.detail", person_id=person.id))

    owner = None
    owner_id = request.form.get("owner_user_id", type=int)
    if owner_id:
        # Scoped lookup. An owner id from another church must not attach.
        owner = db.session.scalar(
            db.select(User).where(
                User.id == owner_id,
                User.church_id == g.church.id,
                User.role.in_(("staff", "leader")),
            )
        )
        if owner is None:
            abort(400)

    due_on = None
    raw_due = (request.form.get("due_on") or "").strip()
    if raw_due:
        try:
            due_on = date.fromisoformat(raw_due)
        except ValueError:
            abort(400)

    step = NextStep(
        church_id=person.church_id,
        person_id=person.id,
        title=title[:200],
        owner_user_id=owner.id if owner else None,
        owner_name=owner.name if owner else None,
        due_on=due_on,
        status=STATUS_OPEN,
        created_by_user_id=current_user.id,
    )
    db.session.add(step)

    PersonEvent.record(
        person,
        KIND_NEXT_STEP,
        f"Next step assigned: {title[:180]}",
        detail=f"Owner: {owner.name if owner else 'nobody yet'}",
        actor=current_user,
    )
    db.session.commit()

    flash(
        STUCK["step_assigned"].format(owner=owner.name if owner else "nobody yet"),
        "notice",
    )
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/step/<int:step_id>/close/")
@login_required
@min_role("leader")
def close_step(person_id: int, step_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    step = db.session.scalar(
        db.select(NextStep).where(
            NextStep.id == step_id,
            NextStep.church_id == g.church.id,
            NextStep.person_id == person.id,
        )
    )
    if step is None:
        abort(404)

    status = request.form.get("status") or STATUS_DONE
    if status not in (STATUS_DONE, STATUS_DROPPED):
        abort(400)

    step.close(status)
    PersonEvent.record(
        person,
        KIND_NEXT_STEP,
        f"Next step {step.status_label.lower()}: {step.title[:180]}",
        actor=current_user,
    )
    db.session.commit()

    flash(STUCK["step_closed"].format(title=step.title), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/owner/")
@login_required
@min_role("leader")
def set_owner(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    owner_id = request.form.get("owner_user_id", type=int)
    if not owner_id:
        person.owner_user_id = None
        person.owner_name = None
        db.session.commit()
        flash(STUCK["owner_cleared"], "notice")
        return redirect(url_for("people.detail", person_id=person.id))

    owner = db.session.scalar(
        db.select(User).where(
            User.id == owner_id,
            User.church_id == g.church.id,
            User.role.in_(("staff", "leader")),
        )
    )
    if owner is None:
        abort(400)

    person.owner_user_id = owner.id
    person.owner_name = owner.name
    db.session.commit()

    flash(
        STUCK["owner_set"].format(owner=owner.name, name=person.first_name), "notice"
    )
    return redirect(url_for("people.detail", person_id=person.id))


# ---------------------------------------------------------------------------
# Increment 4: email
# ---------------------------------------------------------------------------

@bp.post("/<int:person_id>/email/")
@login_required
@min_role("leader")
def send_email(person_id: int):
    """Queue a message. Nothing is sent inside this request.

    A web request that calls a third party API is exactly as slow and as
    reliable as that API, and a failure after the commit loses the message
    with nobody aware of it.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    category = (request.form.get("category") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    # Named `message_body` rather than `body`: the note form on this same page
    # already uses `body`, and two fields with one name on one page is a trap
    # for anything that addresses them by name.
    body = (request.form.get("message_body") or "").strip()

    try:
        message = queue(
            church_id=g.church.id,
            category=category,
            subject=subject,
            body_text=body,
            person=person,
            actor=current_user,
        )
    except NotQueued as exc:
        flash(EMAIL["cannot"].format(reason=str(exc)), "error")
        return redirect(url_for("people.detail", person_id=person.id))

    if message is None:
        flash(EMAIL["duplicate"], "notice")
    else:
        # The unsubscribe token is minted now rather than at person creation,
        # because most people never receive a message and an unused secret is
        # a liability rather than an asset.
        person.ensure_unsubscribe_token()
        db.session.commit()
        flash(EMAIL["queued"].format(name=person.first_name), "notice")

    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/preferences/")
@login_required
@min_role("leader")
def set_preferences(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    for category in OPTIONAL_CATEGORIES:
        person.set_preference(
            category.code, request.form.get(f"cat_{category.code}") == "on"
        )
    db.session.commit()

    flash(EMAIL["prefs_saved"], "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/optout/")
@login_required
@min_role("leader")
def toggle_opt_out(person_id: int):
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    if person.has_opted_out:
        opt_in(person)
        message = EMAIL["opt_in_done"]
    else:
        opt_out(person, reason=f"Set by {current_user.name}")
        message = EMAIL["opt_out_done"]
    db.session.commit()

    flash(message.format(name=person.first_name), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/sequence/<int:enrollment_id>/stop/")
@login_required
@min_role("leader")
def stop_sequence(person_id: int, enrollment_id: int):
    """Stop a sequence by hand.

    Staff should always be able to overrule the automation without having to
    fake a phone call to do it.
    """
    from app.models.sequence import REASON_MANUAL

    person = Person.get_for_church(g.church.id, person_id)
    enrollment = SequenceEnrollment.get_for_church(g.church.id, enrollment_id)
    if person is None or enrollment is None or enrollment.person_id != person.id:
        abort(404)

    if enrollment.is_active:
        enrollment.stop(REASON_MANUAL)
        PersonEvent.record(
            person,
            KIND_EMAIL,
            f"Stopped: {enrollment.sequence_name}",
            detail=enrollment.end_reason_label,
            actor=current_user,
        )
        audit_record(
            SEQUENCE_STOPPED,
            f"{enrollment.sequence_name} stopped for {person.full_name}",
            actor=current_user,
            subject_type="sequence_enrollment",
            subject_id=enrollment.id,
            subject_label=person.full_name,
        )
        db.session.commit()

    flash(AUTOMATION["stopped"], "notice")
    return redirect(url_for("people.detail", person_id=person.id))


@bp.post("/<int:person_id>/approve/")
@login_required
@min_role("leader")
def approve_person(person_id: int):
    """Confirm a self-registered person is real.

    Until this happens they can use their own record and read published plans,
    but church-wide announcements are hidden from them.
    """
    from app.models.audit import ROLE_CHANGED

    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    if person.approve(actor=current_user):
        PersonEvent.record(
            person,
            KIND_CREATED,
            PEOPLE["approved_event"],
            detail=PEOPLE["approved_detail"].format(name=current_user.name),
            actor=current_user,
        )
        audit_record(
            ROLE_CHANGED,
            f"{person.full_name} was approved after signing themselves up",
            actor=current_user,
            subject_type="person",
            subject_id=person.id,
            subject_label=person.full_name,
        )

        if person.email:
            try:
                queue(
                    church_id=g.church.id,
                    # Transactional: this is about their account, not news.
                    category="account",
                    subject=PEOPLE["approved_email_subject"].format(church=g.church.name),
                    body_text=PEOPLE["approved_email_body"].format(
                        name=person.first_name, church=g.church.name
                    ),
                    person=person,
                    dedupe_key=f"approved:{person.id}",
                )
            except NotQueued:
                pass

        db.session.commit()

    flash(PEOPLE["approved"].format(name=person.full_name), "notice")
    return redirect(url_for("people.detail", person_id=person.id))


# ---------------------------------------------------------------------------
# Archiving in bulk
#
# Archive, never delete. A person row is referenced by check-in history,
# matched giving, service assignments, and messages. Deleting one erases a
# child's check-in record, which a church has to keep, and rewrites who said
# what in a room.
# ---------------------------------------------------------------------------

@bp.post("/archive/")
@login_required
@min_role("leader")
def bulk_archive():
    from app.models.audit import PERSON_ARCHIVED

    ids = request.form.getlist("person_id", type=int)
    people = Person.get_many_for_church(g.church.id, ids)

    if not people:
        flash(PEOPLE["bulk_none"], "error")
        return redirect(url_for("people.index", **_filters()))

    mine = current_user.person_id
    archived = []
    for person in people:
        # Archiving your own record hides you from the roster you are standing
        # on, which is confusing rather than dangerous, and never intended.
        if mine is not None and person.id == mine:
            flash(PEOPLE["bulk_self"], "error")
            continue
        if person.archive():
            archived.append(person)

    for person in archived:
        PersonEvent.record(
            person, KIND_CREATED, PEOPLE["archived_event"], actor=current_user
        )

    if archived:
        audit_record(
            PERSON_ARCHIVED,
            f"{len(archived)} people archived",
            actor=current_user,
            subject_type="person",
            subject_label=", ".join(p.full_name for p in archived[:5]),
            detail=f"Archived: {', '.join(p.full_name for p in archived)}"[:1900],
        )
        db.session.commit()
        flash(PEOPLE["bulk_archived"].format(count=len(archived)), "notice")

    return redirect(url_for("people.index", **_filters()))


@bp.get("/archived/")
@login_required
@min_role("leader")
def archived():
    return render_template(
        "people/archived.html",
        church=g.church,
        content=PEOPLE,
        people=db.session.scalars(Person.archived_for_church(g.church.id)).all(),
        active="people",
    )


@bp.post("/archived/restore/")
@login_required
@min_role("leader")
def bulk_restore():
    ids = request.form.getlist("person_id", type=int)
    people = Person.get_many_for_church(g.church.id, ids)

    restored = [person for person in people if person.unarchive()]
    if restored:
        db.session.commit()
        flash(PEOPLE["bulk_restored"].format(count=len(restored)), "notice")
    else:
        flash(PEOPLE["bulk_none"], "error")

    return redirect(url_for("people.archived"))


def _filters() -> dict:
    """Carry the roster's filter and search back through a redirect.

    Archiving forty people and landing on an unfiltered page means finding
    your place again, which is how somebody archives the wrong batch next.
    """
    carried = {}
    for key in ("stage", "q", "page"):
        value = request.form.get(key) or request.args.get(key)
        if value:
            carried[key] = value
    return carried


@bp.post("/add/")
@login_required
@min_role("leader")
def add_person():
    """Put one person on the roster, optionally with a login.

    The roster has only ever been filled by a spreadsheet import, by somebody
    signing themselves up, or from a terminal. A leader with a connection card
    in their hand had no way in at all.
    """
    from app.accounts import create_login
    from app.models.audit import ROLE_CHANGED
    from app.stages import STAGE_CODES

    first = (request.form.get("first_name") or "").strip()
    last = (request.form.get("last_name") or "").strip()
    if not first:
        flash(PEOPLE["add_first_required"], "error")
        return redirect(url_for("people.index"))

    stage = (request.form.get("stage") or "visitor").strip()
    if stage not in STAGE_CODES:
        abort(400)

    person = Person(
        church_id=g.church.id,
        first_name=first[:80],
        last_name=last[:80],
        email=(request.form.get("email") or "").strip().lower() or None,
        phone=(request.form.get("phone") or "").strip() or None,
        stage=stage,
        first_seen_on=utcnow().date(),
        # Entered by a human with roster access, so there is nobody to approve.
        approved_at=utcnow(),
    )
    db.session.add(person)
    db.session.flush()

    PersonEvent.record(
        person, KIND_CREATED,
        PEOPLE["add_event"].format(name=current_user.name), actor=current_user,
    )
    enroll_for_stage(person, actor=current_user)

    message = PEOPLE["add_done"].format(name=person.full_name)

    # Only staff hand out logins. A leader can add people all day; deciding
    # who can sign in is a different level of trust.
    if request.form.get("with_login") and current_user.is_staff:
        if not person.email:
            message = PEOPLE["add_login_needs_email"].format(name=person.full_name)
        else:
            user = create_login(
                g.church.id, person.full_name, person.email,
                (request.form.get("role") or "member").strip(),
                actor=current_user, person=person,
            )
            if user is None:
                message = PEOPLE["add_login_taken"].format(
                    name=person.full_name, email=person.email
                )
            else:
                message = PEOPLE["add_done_login"].format(name=person.full_name)
                audit_record(
                    ROLE_CHANGED,
                    f"{user.name} was given a {user.role} account",
                    actor=current_user,
                    subject_type="user", subject_id=user.id, subject_label=user.name,
                )

    db.session.commit()
    flash(message, "notice")
    return redirect(url_for("people.detail", person_id=person.id))
