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
from datetime import date, datetime

from flask_login import current_user, login_required

from app.content import AUTOMATION, EMAIL, GIVING, PEOPLE, STUCK
from app.extensions import db
from app import households
from app.categories import CATEGORIES, OPTIONAL_CATEGORIES
from app.models import (
    ACCOUNT_DECLINED,
    ACCOUNT_DONE,
    AccountRequest,
    GuestCard,
    NextStepSignup,
    HEARD_LABELS,
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
    Household,
    SupportRequest,
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
from app.ages import KID as AGE_KID, YOUTH as AGE_YOUTH, YOUTH_FROM_AGE
from app.stages import (
    AGE_FILTERS,
    YOUTH,
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

    if stage and stage not in STAGE_BY_CODE and stage not in AGE_FILTERS:
        # An unknown stage in the query string is a typo or a probe. Showing
        # everyone would silently misreport the filter, so refuse instead.
        abort(404)

    # Kids and youth are filters, not stages. Filtering by a real stage
    # excludes both, for the same reason the rail does not count them into
    # one: a child carries their family's stage so the roster has something
    # to sort by, and is not somebody the church is discipling through it.
    children, group = None, None
    if stage == KIDS:
        group, stage = AGE_KID, None
    elif stage == YOUTH:
        group, stage = AGE_YOUTH, None
    elif stage:
        children = False

    query = Person.search(g.church.id, term=term, stage=stage,
                          children=children, group=group)
    pagination = db.paginate(query, page=page, per_page=PAGE_SIZE, error_out=False)

    return render_template(
        "people/index.html",
        church=g.church,
        content=PEOPLE,
        people=pagination.items,
        pagination=pagination,
        stages=stages_for(g.church),
        counts=Person.stage_counts(g.church.id),
        kids_count=Person.group_count(g.church.id, AGE_KID),
        kids_filter=KIDS,
        youth_count=Person.group_count(g.church.id, AGE_YOUTH),
        youth_filter=YOUTH,
        total=Person.total_for_church(g.church.id),
        active_stage=request.args.get("stage") or None,
        term=term,
        waiting=db.session.scalars(
            Person.waiting_for_approval(g.church.id)
        ).all(),
        archived_count=Person.archived_count(g.church.id),
        # On the button, so staff can see there is something to read without
        # opening the page to find out.
        guest_cards=GuestCard.waiting_count(g.church.id),
        account_requests=AccountRequest.open_count(g.church.id),
        signup_count=NextStepSignup.open_count(g.church.id),
        # For the child form's family picker. Children are the only thing
        # added from this page that must belong to one.
        households=db.session.scalars(
            db.select(Household)
            .where(Household.church_id == g.church.id)
            .order_by(Household.name)
        ).all(),
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
        # For "waiting N days" on the background check. Passed in rather than
        # computed in the template, so the page and the server agree on what
        # day it is.
        today=date.today(),
        youth_from_age=YOUTH_FROM_AGE,
        recurring=db.session.scalars(
            ExternalRecurringGift.for_person(g.church.id, person.id)
        ).all(),
        enrollments=db.session.scalars(
            SequenceEnrollment.for_person(g.church.id, person.id)
        ).all(),
        automation=AUTOMATION,
        household_members=household_members,
        support_requests=db.session.scalars(
            SupportRequest.for_person(g.church.id, person.id)
        ).all(),
        households=db.session.scalars(
            db.select(Household)
            .where(Household.church_id == g.church.id)
            .order_by(Household.name)
        ).all(),
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


@bp.get("/support/")
@login_required
@min_role("leader")
def support_list():
    """Every open request, oldest first. The dashboard shows the first five."""
    from app.models import SupportRequest

    return render_template(
        "people/support.html",
        church=g.church,
        content=PEOPLE,
        requests=db.session.scalars(
            SupportRequest.open_for_church(g.church.id)
        ).all(),
        answered=db.session.scalars(
            SupportRequest.answered_for_church(g.church.id)
        ).all(),
        answered_count=SupportRequest.answered_count(g.church.id),
        active="people",
    )


@bp.post("/support/<int:request_id>/answered/")
@login_required
@min_role("leader")
def answer_support(request_id: int):
    """Somebody dealt with it, and their name goes on that.

    Not deleted, and not closed by the clock. The record of who asked and
    who replied is what a church needs if it is ever asked whether somebody
    was looked after.
    """
    from app.models import SupportRequest

    ask = SupportRequest.get_for_church(g.church.id, request_id)
    if ask is None:
        abort(404)

    back = redirect(
        request.form.get("back")
        or url_for("people.detail", person_id=ask.person_id, _anchor="support")
    )
    if not ask.answer(current_user):
        flash(PEOPLE["support_already"].format(name=ask.answered_by_name or "Somebody"),
              "notice")
        return back

    PersonEvent.record(
        ask.person,
        KIND_CONTACT,
        PEOPLE["support_answered_event"],
        detail=None,
        actor=current_user,
    )
    db.session.commit()
    flash(PEOPLE["support_answered"].format(name=ask.person.first_name), "notice")
    return back


@bp.post("/support/<int:request_id>/reopen/")
@login_required
@min_role("leader")
def reopen_support(request_id: int):
    """Closed too soon. It happens, and it should not need a database."""
    from app.models import SupportRequest

    ask = SupportRequest.get_for_church(g.church.id, request_id)
    if ask is None:
        abort(404)
    ask.reopen()
    db.session.commit()
    flash(PEOPLE["support_reopened"], "notice")
    return redirect(url_for("people.detail", person_id=ask.person_id, _anchor="support"))


@bp.post("/<int:person_id>/household/code/")
@login_required
@min_role("leader")
def household_code(person_id: int):
    """Give this family a check-in code, or replace the one they have.

    Replacing is the one that matters: a custody situation, or a family who
    believe somebody else knows their code. The old one stops working the
    moment this returns, which is the point, so it is audited with the name
    of whoever did it.
    """
    from app.models.audit import PIN_ROTATED

    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)
    back = redirect(url_for("people.detail", person_id=person.id, _anchor="household"))

    home = person.household
    if home is None:
        flash(PEOPLE["code_no_household"], "error")
        return back

    had_one = bool(home.checkin_pin)
    code = home.regenerate_checkin_pin() if had_one else home.ensure_checkin_pin()

    if had_one:
        # The code itself is deliberately not in the audit entry. The log is
        # designed to be read, kept and exported; a working code in it is
        # worse than no log.
        audit_record(
            PIN_ROTATED,
            PEOPLE["code_audit"].format(household=home.name),
            actor=current_user,
            subject_type="household",
            subject_id=home.id,
            subject_label=home.name,
        )
    db.session.commit()

    flash(
        (PEOPLE["code_rotated"] if had_one else PEOPLE["code_created"]).format(
            household=home.name, code=code
        ),
        "notice",
    )
    return back


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


MAX_HOUSEHOLD_NAME = 160


@bp.post("/<int:person_id>/household/")
@login_required
@min_role("leader")
def set_household(person_id: int):
    """Move somebody into a family, out of one, or into a brand new one.

    Written for the case that produced it: the same person entered twice, once
    by the office and once by themselves, with the family on one record and
    the email on the other. Staff put the family on the record worth keeping
    and archive the other, and nothing is retyped.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    before = person.household
    new_name = (request.form.get("household_name") or "").strip()
    raw_id = (request.form.get("household_id") or "").strip()

    if new_name:
        target = Household(church_id=g.church.id, name=new_name[:MAX_HOUSEHOLD_NAME])
        db.session.add(target)
        db.session.flush()
        started = True
    elif raw_id:
        if not raw_id.isdigit():
            flash(PEOPLE["household_unknown"], "error")
            return redirect(url_for("people.detail", person_id=person.id))
        target = Household.get_for_church(g.church.id, int(raw_id))
        if target is None:
            flash(PEOPLE["household_unknown"], "error")
            return redirect(url_for("people.detail", person_id=person.id))
        started = False
    else:
        target, started = None, False

    if target is not None and before is not None and target.id == before.id:
        flash(
            PEOPLE["household_already"].format(name=person.first_name,
                                               household=target.name),
            "notice",
        )
        return redirect(url_for("people.detail", person_id=person.id))

    if target is None and before is None:
        return redirect(url_for("people.detail", person_id=person.id))

    person.household_id = target.id if target is not None else None

    # A family with a child in it needs a check-in code, and one that has just
    # gained its first child has not got one yet.
    if target is not None and any(m.is_child for m in target.members):
        target.ensure_checkin_pin()

    detail = []
    if before is not None:
        detail.append(PEOPLE["household_event_left"].format(household=before.name))
    if target is not None:
        detail.append(PEOPLE["household_event_joined"].format(household=target.name))
    PersonEvent.record(
        person,
        KIND_NOTE,
        PEOPLE["household_event"],
        detail=". ".join(detail),
        actor=current_user,
    )

    # An emptied household is clutter, and its check-in code should not stay
    # live. Check-in records copy the name rather than reading it, so the
    # Sunday history keeps saying who collected whom.
    emptied = None
    if before is not None:
        db.session.flush()
        if not [m for m in before.members if m.id != person.id]:
            emptied = before.name
            db.session.delete(before)

    db.session.commit()

    if target is None:
        flash(PEOPLE["household_removed"].format(name=person.first_name), "notice")
    elif started:
        flash(
            PEOPLE["household_started"].format(household=target.name,
                                               name=person.first_name),
            "notice",
        )
    else:
        flash(
            PEOPLE["household_moved"].format(name=person.first_name,
                                             household=target.name),
            "notice",
        )
    if emptied:
        flash(PEOPLE["household_emptied"].format(household=emptied), "notice")

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

    # A child is a different record with different rules, so it gets its own
    # path through this route rather than a handful of `if is_child` branches
    # threaded through an adult's.
    if request.form.get("is_child"):
        return _add_child_from_roster(first, last, stage)

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


# ---------------------------------------------------------------------------
# Children
#
# Staff had no way to create one. `is_child` could only be set by an import or
# from a shell, which meant the kiosk could check in only the children
# somebody had already put in the database by hand. A child record exists for
# exactly one reason, Sunday check-in, and check-in is keyed on the household,
# so a child without a family is a record that cannot do the only job it has.
# That is why a household is required here and optional everywhere else.
# ---------------------------------------------------------------------------

def _parse_birthdate(raw: str):
    """A date, or a reason it is not one. Returns (value, error_key)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        value = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None, "child_birthday_bad"
    if value > date.today():
        return None, "child_birthday_future"
    return value, None


def _add_child_from_roster(first: str, last: str, stage: str):
    """A child entered from the roster page, with their family chosen there.

    The case this is for is a family nobody has met before: a card with a
    parent and two children on it, and no record for any of them. Staff enter
    the parent, then each child, naming the new family once and picking it
    after that.
    """
    back = redirect(url_for("people.index", **_filters()))

    birthdate, bad = _parse_birthdate(request.form.get("birthdate"))
    if bad:
        flash(PEOPLE[bad], "error")
        return back

    try:
        household, started = households.choose(
            g.church.id,
            household_id=request.form.get("household_id"),
            new_name=request.form.get("household_name"),
        )
    except households.HouseholdError:
        flash(PEOPLE["household_unknown"], "error")
        return back

    # The one rule that differs from adding an adult. A child with no
    # household cannot be checked in, and nothing on any screen would show
    # that until a parent was standing at a kiosk on Sunday.
    if household is None:
        flash(PEOPLE["child_needs_household"], "error")
        return back

    # An empty surname follows the family's rather than refusing a form over
    # something already answered: most children share it, and the household
    # was just named after it.
    if not last:
        last = _surname_in(household) or ""

    try:
        households.check_room(household, first, last)
    except households.HouseholdError as refused:
        flash(PEOPLE[f"child_{refused.reason}"].format(**refused.detail), "error")
        # A typed name has already created the family by this point. Nothing
        # commits on this path, but leaving the flush in place would make an
        # orphan family out of any later commit added to this request.
        db.session.rollback()
        return back

    child = households.place_child(
        household,
        church_id=g.church.id,
        first=first,
        last=last,
        birthdate=birthdate,
        stage=stage,
        notes=request.form.get("notes"),
        # Entered by a human with roster access, so there is nobody to approve.
        approved_at=utcnow(),
    )

    PersonEvent.record(
        child, KIND_CREATED,
        PEOPLE["child_event"].format(name=current_user.name),
        detail=PEOPLE["child_event_detail"].format(household=household.name),
        actor=current_user,
    )
    db.session.commit()

    flash(
        PEOPLE["child_added_new" if started else "child_added"].format(
            name=child.first_name, household=household.name
        ),
        "notice",
    )
    # Ticked on the same form, hidden by its script, and refused here. Saying
    # so beats dropping a request somebody made on purpose.
    if request.form.get("with_login"):
        flash(PEOPLE["child_no_login"], "notice")
    return redirect(url_for("people.detail", person_id=child.id))


def _surname_in(household) -> str | None:
    """The surname the adults in a family use, if they agree on one."""
    names = {
        (m.last_name or "").strip()
        for m in household.members
        if not m.is_child and not m.is_archived and (m.last_name or "").strip()
    }
    return names.pop() if len(names) == 1 else None


@bp.post("/<int:person_id>/family/add/")
@login_required
@min_role("leader")
def add_child(person_id: int):
    """A child added from a person's own record, into their family.

    The other half of the same job and the half staff will use most: the
    parent is already on screen, so there is no family to choose and no
    surname or stage to decide. A parent with no household gets one, because
    that is the state anybody who signed themselves up is in.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    back = redirect(
        url_for("people.detail", person_id=person.id, _anchor="household")
    )

    first = (request.form.get("first_name") or "").strip()
    if not first:
        flash(PEOPLE["child_first_required"], "error")
        return back

    birthdate, bad = _parse_birthdate(request.form.get("birthdate"))
    if bad:
        flash(PEOPLE[bad], "error")
        return back

    last = (request.form.get("last_name") or "").strip() or person.last_name
    household = households.start_for(person)

    try:
        households.check_room(household, first, last)
    except households.HouseholdError as refused:
        flash(PEOPLE[f"child_{refused.reason}"].format(**refused.detail), "error")
        db.session.rollback()
        return back

    child = households.place_child(
        household,
        church_id=g.church.id,
        first=first,
        last=last,
        birthdate=birthdate,
        # A family arrives together, so a child starts where their parent is
        # rather than at the front of a follow-up path meant for adults.
        stage=person.stage,
        notes=request.form.get("notes"),
        approved_at=utcnow(),
    )

    PersonEvent.record(
        child, KIND_CREATED,
        PEOPLE["child_event"].format(name=current_user.name),
        detail=PEOPLE["child_event_detail"].format(household=household.name),
        actor=current_user,
    )
    db.session.commit()

    flash(
        PEOPLE["child_added"].format(name=child.first_name,
                                     household=household.name),
        "notice",
    )
    return back


# ---------------------------------------------------------------------------
# Connect cards
#
# What guests submitted, in their own words. Separate from the roster view
# because most of a card has nowhere to live on a person: how they heard
# about the church, whether they asked to be contacted, and whatever they
# wrote in the box.
# ---------------------------------------------------------------------------

GUEST_PAGE_SIZE = 40


@bp.get("/guests/")
@login_required
@min_role("leader")
def guests():
    """Every connect card, newest first.

    Where the dashboard tile points. Somebody who sees "6 first time guests"
    wants to know who they were, and until now the only answer was a roster
    filtered by a date nobody could see.
    """
    show_discarded = request.args.get("discarded") == "1"
    page = max(1, request.args.get("page", type=int) or 1)

    pagination = db.paginate(
        GuestCard.for_church(g.church.id, include_discarded=show_discarded),
        page=page, per_page=GUEST_PAGE_SIZE, error_out=False,
    )

    return render_template(
        "people/guests.html",
        church=g.church,
        content=PEOPLE,
        # Built from the request rather than typed into a setting, so it is
        # right on whatever hostname this church actually uses and cannot
        # drift from it.
        card_url=url_for("public.welcome", _external=True, _scheme="https"),
        cards=pagination.items,
        pagination=pagination,
        show_discarded=show_discarded,
        heard_labels=HEARD_LABELS,
        total=GuestCard.waiting_count(g.church.id),
        active="people",
    )


@bp.post("/guests/<int:card_id>/discard/")
@login_required
@min_role("leader")
def discard_guest(card_id: int):
    """Rubbish from a public form, put out of the way.

    Not a delete, and deliberately not touching the person. A card and a
    roster record are different things: a staff member clearing a junk
    submission is saying this card is noise, not that the person it matched
    should be removed from the church.
    """
    card = GuestCard.get_for_church(g.church.id, card_id)
    if card is None:
        abort(404)

    # `_filters` carries the roster's stage and search, which this page does
    # not have. What it does have is the discarded toggle, and losing it would
    # bounce somebody out of the view they were clearing.
    carried = {}
    for key in ("discarded", "page"):
        value = request.form.get(key) or request.args.get(key)
        if value:
            carried[key] = value
    back = redirect(url_for("people.guests", **carried))

    if request.form.get("restore"):
        if card.restore():
            db.session.commit()
            flash(PEOPLE["guest_restored"].format(name=card.full_name), "notice")
        return back

    if card.discard(actor=current_user):
        db.session.commit()
        flash(PEOPLE["guest_discarded"].format(name=card.full_name), "notice")
    return back


# ---------------------------------------------------------------------------
# Account requests
#
# What somebody filled in on the sign-in page when they asked to be set up.
# The email that goes out carries all of it, because whoever does the setup
# needs it in front of them; this page exists so the request survives that
# inbox.
# ---------------------------------------------------------------------------

@bp.get("/requests/")
@login_required
@min_role("leader")
def requests():
    """Account requests, waiting ones first."""
    show_all = request.args.get("all") == "1"
    page = max(1, request.args.get("page", type=int) or 1)

    pagination = db.paginate(
        AccountRequest.for_church(g.church.id, include_done=show_all),
        page=page, per_page=GUEST_PAGE_SIZE, error_out=False,
    )

    return render_template(
        "people/requests.html",
        church=g.church,
        content=PEOPLE,
        asks=pagination.items,
        pagination=pagination,
        show_all=show_all,
        waiting=AccountRequest.open_count(g.church.id),
        active="people",
    )


@bp.post("/requests/<int:request_id>/")
@login_required
@min_role("leader")
def resolve_request(request_id: int):
    """Mark one set up, declined, or back on the list.

    Deliberately does not create the login. The household here is free text
    that a person reads and turns into real records, and a button that made
    an account straight from it would be guessing at the part that needs
    judgement.
    """
    ask = AccountRequest.get_for_church(g.church.id, request_id)
    if ask is None:
        abort(404)

    carried = {}
    for key in ("all", "page"):
        value = request.form.get(key) or request.args.get(key)
        if value:
            carried[key] = value
    back = redirect(url_for("people.requests", **carried))

    action = (request.form.get("action") or "").strip()

    if action == "reopen":
        if ask.reopen():
            db.session.commit()
            flash(PEOPLE["requests_reopened"].format(name=ask.full_name), "notice")
        return back

    if action not in (ACCOUNT_DONE, ACCOUNT_DECLINED):
        abort(400)

    if ask.resolve(action, actor=current_user):
        db.session.commit()
        flash(
            PEOPLE["requests_marked_done" if action == ACCOUNT_DONE
                   else "requests_marked_declined"].format(name=ask.full_name),
            "notice",
        )
    return back


@bp.post("/<int:person_id>/flags/")
@login_required
@min_role("leader")
def set_flags(person_id: int):
    """The two things on the snapshot a human knows and the system cannot.

    Both are recorded on the timeline rather than only on the record. A
    background check is a safeguarding fact, and "who said this was done, and
    when" is the question asked after something has gone wrong, by which
    point a column holding only the current value answers nothing.
    """
    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(404)

    back = redirect(url_for("people.detail", person_id=person.id))

    giver = bool(request.form.get("is_regular_giver"))
    ordered = bool(request.form.get("background_check_ordered"))

    changes = []

    if giver != person.is_regular_giver:
        person.is_regular_giver = giver
        changes.append(PEOPLE["flag_giver_on"] if giver
                       else PEOPLE["flag_giver_off"])

    if ordered != person.background_check_ordered:
        # Unticking clears the date rather than keeping it, because a date
        # left behind on an unticked box is a record that says two things.
        person.background_check_ordered_on = date.today() if ordered else None
        changes.append(PEOPLE["flag_check_on"] if ordered
                       else PEOPLE["flag_check_off"])

    # The kids/youth line. Blank means "work it out from the birthday", which
    # is the normal state and the one a church should land back on after a
    # birthday gets corrected.
    raw = (request.form.get("youth_override") or "").strip()
    if person.is_child and raw in ("", "kid", "youth"):
        wanted = None if raw == "" else (raw == "youth")
        if wanted != person.youth_override:
            was = person.age_group
            person.youth_override = wanted
            if person.age_group != was:
                changes.append(PEOPLE["flag_group_changed"].format(
                    before=PEOPLE["youth_group_" + was],
                    after=PEOPLE["youth_group_" + person.age_group]))
            else:
                changes.append(PEOPLE["flag_group_set"])

    if not changes:
        return back

    PersonEvent.record(
        person, KIND_NOTE, PEOPLE["flag_event"],
        detail=". ".join(changes), actor=current_user,
    )
    db.session.commit()
    flash(PEOPLE["flag_saved"].format(name=person.first_name), "notice")
    return back


# ---------------------------------------------------------------------------
# Next step sign-ups
#
# People who tapped a tile on the home screen of the app to say they are
# interested in baptism, volunteering, or whatever else the church offers.
# See app/next_steps.py for the offers and app/signups.py for the alert.
#
# Its own screen rather than a filter on the roster, for the same reason
# connect cards have one: the question "who is waiting to hear back from us"
# is not answerable by looking at people, and a church that cannot answer it
# is a church where somebody asked to be baptised in March and nobody
# noticed.
# ---------------------------------------------------------------------------

SIGNUP_PAGE_SIZE = 50


@bp.get("/signups/")
@login_required
@min_role("leader")
def signups():
    from app.models import NextStepSignup

    show_done = request.args.get("done") == "1"
    query = (
        NextStepSignup.recent_for_church(g.church.id, limit=SIGNUP_PAGE_SIZE)
        if show_done
        else NextStepSignup.open_for_church(g.church.id, limit=SIGNUP_PAGE_SIZE)
    )

    return render_template(
        "people/signups.html",
        church=g.church,
        content=PEOPLE,
        signups=db.session.scalars(query).all(),
        show_done=show_done,
        open_count=NextStepSignup.open_count(g.church.id),
        active="people",
    )


@bp.post("/signups/<int:signup_id>/done/")
@login_required
@min_role("leader")
def mark_signup_done(signup_id: int):
    """Dealt with. Not a delete: the ask and who answered it stay."""
    from app.models import NextStepSignup

    signup = NextStepSignup.get_for_church(g.church.id, signup_id)
    if signup is None:
        abort(404)

    # `handle` refuses to overwrite, so a double-tapped button does not
    # rewrite who dealt with it to whoever happened to tap second.
    changed = signup.handle(actor=current_user)
    db.session.commit()

    flash(PEOPLE["signups_marked"] if changed else PEOPLE["signups_already"],
          "notice" if changed else "error")
    return redirect(url_for("people.signups"))
