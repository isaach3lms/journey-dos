"""The sequence engine.

Three entry points, and the middle one is the important one:

`enroll_for_stage` runs when somebody arrives at a stage.
`stop_for_person` runs when a human logs contact, or the stage changes.
`run_due` runs on a schedule and sends what is due.

**The hard stops are checked twice on purpose.** Once at the moment they happen,
so the enrollment is visibly stopped on the person's record, and again inside
`run_due` immediately before sending. A person can be phoned in the hour
between a step falling due and the worker waking up, and the answer that
matters is the one at the instant of sending. Checking only at the event would
leave a race; checking only at send would leave a pastor looking at a record
that still claims a sequence is running.
"""

from __future__ import annotations

from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.mail import NotQueued, queue
from app.models import KIND_EMAIL, PersonEvent, SequenceEnrollment
from app.models.base import utcnow
from app.models.sequence import (
    REASON_CONTACT,
    REASON_FINISHED,
    REASON_MANUAL,
    REASON_NO_EMAIL,
    REASON_OPTED_OUT,
    REASON_STAGE_LEFT,
    REASON_TARGET_STAGE,
    STATUS_ACTIVE,
)
from app.sequences import MAX_CATCHUP_DAYS, get, render, triggered_by
from app.stages import stage_order


def enroll_for_stage(person, actor=None) -> list[SequenceEnrollment]:
    """Start any sequence this stage triggers. Caller commits."""
    started = []

    for sequence in triggered_by(person.stage):
        existing = db.session.scalar(
            db.select(SequenceEnrollment).where(
                SequenceEnrollment.person_id == person.id,
                SequenceEnrollment.sequence_code == sequence.code,
            )
        )
        # Already been through it. Moving back and forth across a stage
        # boundary should not restart the welcome series each time.
        if existing is not None:
            continue

        enrollment = SequenceEnrollment(
            church_id=person.church_id,
            person_id=person.id,
            sequence_code=sequence.code,
            enrolled_at=utcnow(),
        )
        db.session.add(enrollment)
        # Flush before reading step_index. SQLAlchemy applies a column
        # `default=` at INSERT, not at construction, so an unflushed object
        # has None where the schema says 0.
        db.session.flush()
        enrollment.schedule_next()
        started.append(enrollment)

        PersonEvent.record(
            person,
            KIND_EMAIL,
            f"Started: {sequence.name}",
            detail=sequence.description,
            actor=actor,
        )

    return started


def stop_for_person(person, reason: str, actor=None) -> int:
    """End every running sequence for this person. Caller commits."""
    stopped = 0
    for enrollment in db.session.scalars(
        SequenceEnrollment.active_for_person(person.church_id, person.id)
    ):
        enrollment.stop(reason)
        stopped += 1
        PersonEvent.record(
            person,
            KIND_EMAIL,
            f"Stopped: {enrollment.sequence_name}",
            detail=enrollment.end_reason_label,
            actor=actor,
        )
    return stopped


def on_contact_logged(person, actor=None) -> int:
    """Hard stop one: a human actually talked to them.

    The most important line in this increment. A welcome series that keeps
    emailing somebody the pastor already phoned is worse than no automation: it
    tells the person nobody is paying attention.
    """
    return stop_for_person(person, REASON_CONTACT, actor=actor)


def on_stage_changed(person, previous_stage: str, actor=None) -> int:
    """Hard stop two, plus the ordinary case of moving on.

    Reaching the target stage is success. Moving to some other stage means the
    sequence is aimed at somebody the person no longer is.
    """
    stopped = 0
    for enrollment in db.session.scalars(
        SequenceEnrollment.active_for_person(person.church_id, person.id)
    ):
        sequence = enrollment.sequence
        if sequence is None:
            enrollment.complete(REASON_FINISHED)
            continue

        if stage_order(person.stage) >= stage_order(sequence.target_stage):
            enrollment.complete(REASON_TARGET_STAGE)
        elif person.stage != sequence.trigger_stage:
            enrollment.stop(REASON_STAGE_LEFT)
        else:
            continue

        stopped += 1
        PersonEvent.record(
            person,
            KIND_EMAIL,
            f"Stopped: {enrollment.sequence_name}",
            detail=enrollment.end_reason_label,
            actor=actor,
        )

    enroll_for_stage(person, actor=actor)
    return stopped


def _still_running(enrollment) -> str | None:
    """Re-check the hard stops at the instant of sending.

    Returns a reason to stop, or None to proceed.
    """
    person = enrollment.person
    if person is None or person.is_archived:
        return REASON_MANUAL

    sequence = enrollment.sequence
    if sequence is None:
        return REASON_FINISHED

    # Hard stop one. `last_contact_at` is set only by a logged conversation,
    # never by a note, which is the distinction increment 3 exists to keep.
    if person.last_contact_at is not None and person.last_contact_at >= enrollment.enrolled_at:
        return REASON_CONTACT

    # Hard stop two.
    if stage_order(person.stage) >= stage_order(sequence.target_stage):
        return REASON_TARGET_STAGE

    if person.stage != sequence.trigger_stage:
        return REASON_STAGE_LEFT

    if not person.email:
        return REASON_NO_EMAIL

    step = sequence.step_at(enrollment.step_index)
    if step is None:
        return REASON_FINISHED

    if not person.allows(step.category):
        return REASON_OPTED_OUT

    return None


def run_due(church_id: int | None = None, limit: int = 200) -> dict:
    """Send every step that has fallen due. Returns a count per outcome."""
    counts = {"sent": 0, "stopped": 0, "skipped": 0, "completed": 0}

    for enrollment in list(db.session.scalars(
        SequenceEnrollment.due(church_id=church_id, limit=limit)
    )):
        reason = _still_running(enrollment)
        if reason is not None:
            if reason in (REASON_FINISHED, REASON_TARGET_STAGE):
                enrollment.complete(reason)
                counts["completed"] += 1
            else:
                enrollment.stop(reason)
                counts["stopped"] += 1
            db.session.commit()
            continue

        person = enrollment.person
        sequence = enrollment.sequence
        step = sequence.step_at(enrollment.step_index)

        # A step this overdue is not worth sending. If the worker was off for a
        # month, the person on day 2 of a welcome series does not want the day
        # 2 email in September, they want to be left alone.
        if enrollment.next_due_at < utcnow() - timedelta(days=MAX_CATCHUP_DAYS):
            enrollment.advance()
            counts["skipped"] += 1
            db.session.commit()
            continue

        church = person.church
        try:
            queue(
                church_id=enrollment.church_id,
                category=step.category,
                subject=render(step.subject, person, church),
                body_text=render(step.body, person, church),
                person=person,
                # One send per enrollment per step, so a worker that runs
                # twice, or crashes after queuing, cannot email twice.
                dedupe_key=f"sequence:{enrollment.id}:step:{enrollment.step_index}",
            )
        except NotQueued as exc:
            current_app.logger.info(
                "Sequence step not queued for person %s: %s", person.id, exc
            )
            enrollment.stop(REASON_OPTED_OUT)
            counts["stopped"] += 1
            db.session.commit()
            continue

        person.ensure_unsubscribe_token()
        PersonEvent.record(
            person,
            KIND_EMAIL,
            f"{sequence.name}: {render(step.subject, person, church)[:150]}",
            detail="Sent automatically",
        )
        enrollment.advance()
        counts["sent"] += 1
        db.session.commit()

    return counts
