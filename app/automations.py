"""
The automation runner.

Called by a Render cron job once a day. Everything is idempotent: running it
twice in one day sends nothing twice, because each send advances last_step_sent
and stamps last_sent_at.
"""

from datetime import timedelta

from flask import current_app

from .brand import BRAND
from .emails import send_email
from .extensions import db, utcnow
from .models import Enrollment, Interaction, Person, Stage
from .sequences import SEQUENCES, render, sequence_for_source


def enroll(person: Person, commit: bool = True):
    """Put a person into the sequence that matches how they came in."""
    key, sequence = sequence_for_source(person.source)
    if not sequence:
        return None
    existing = Enrollment.query.filter_by(
        church_id=person.church_id, person_id=person.id, sequence_key=key
    ).first()
    if existing:
        return existing
    enrollment = Enrollment(
        church_id=person.church_id, person_id=person.id, sequence_key=key
    )
    db.session.add(enrollment)
    if commit:
        db.session.commit()
    return enrollment


def stop(enrollment: Enrollment, reason: str) -> None:
    enrollment.stopped_at = utcnow()
    enrollment.stop_reason = reason


def _should_stop(enrollment: Enrollment, person: Person, sequence: dict):
    """Automation yields to humans and to progress."""
    if not person.is_active_record:
        return "person archived"

    contacted_by_human = any(
        item.kind != "automated" and item.occurred_at > enrollment.enrolled_at
        for item in person.interactions
    )
    if contacted_by_human:
        return "a person made contact"

    stop_stage_name = sequence.get("stop_at_stage")
    if stop_stage_name and person.stage:
        stop_stage = Stage.query.filter_by(
            church_id=person.church_id, name=stop_stage_name
        ).first()
        if stop_stage and person.stage.position >= stop_stage.position:
            return f"reached {stop_stage_name}"
    return None


def run_sequences(site_url: str = None) -> dict:
    """Send every step that is due. Returns a summary for the log."""
    site_url = site_url or current_app.config.get("SITE_URL", "")
    now = utcnow()
    sent = 0
    stopped = 0
    completed = 0

    active = Enrollment.query.filter(
        Enrollment.stopped_at.is_(None), Enrollment.completed_at.is_(None)
    ).all()

    for enrollment in active:
        sequence = SEQUENCES.get(enrollment.sequence_key)
        person = db.session.get(Person, enrollment.person_id)
        if not sequence or not person:
            stop(enrollment, "sequence or person missing")
            stopped += 1
            continue

        reason = _should_stop(enrollment, person, sequence)
        if reason:
            stop(enrollment, reason)
            stopped += 1
            continue

        next_index = enrollment.last_step_sent + 1
        if next_index >= len(sequence["steps"]):
            enrollment.completed_at = now
            completed += 1
            continue

        step = sequence["steps"][next_index]
        due_at = enrollment.enrolled_at + timedelta(days=step["delay_days"])
        if due_at > now:
            continue

        # One automated message per person per day, no matter how many
        # sequences they sit in.
        if enrollment.last_sent_at and (now - enrollment.last_sent_at) < timedelta(hours=20):
            continue

        subject = render(step["subject"], person, BRAND, site_url)
        body = render(step["body"], person, BRAND, site_url)
        delivered = send_email(to=person.email, subject=subject, html=body)
        if not delivered:
            continue

        enrollment.last_step_sent = next_index
        enrollment.last_sent_at = now
        db.session.add(
            Interaction(
                church_id=person.church_id,
                person_id=person.id,
                kind="automated",
                summary=f"{sequence['name']}: {subject}",
            )
        )
        sent += 1
        if next_index == len(sequence["steps"]) - 1:
            enrollment.completed_at = now
            completed += 1

    db.session.commit()
    return {"sent": sent, "stopped": stopped, "completed": completed}


def staff_digest(church_id: int, to: str) -> bool:
    """The weekly email that makes the product's promise visible: here is who
    stopped moving, and here is who nobody has talked to."""
    people = Person.query.filter_by(church_id=church_id, is_active_record=True).all()
    stuck = sorted([p for p in people if p.is_stuck], key=lambda p: p.days_in_stage, reverse=True)
    cold = [p for p in people if p.last_contact_at is None]
    new_this_week = [p for p in people if (utcnow() - p.created_at).days <= 7]

    def block(title, rows):
        if not rows:
            return f"<h3 style='margin:18px 0 6px'>{title}</h3><p style='margin:0'>None.</p>"
        items = "".join(f"<li>{row}</li>" for row in rows)
        return f"<h3 style='margin:18px 0 6px'>{title}</h3><ul style='margin:0'>{items}</ul>"

    html = (
        "<div style='font-family:Inter,Arial,sans-serif;max-width:560px'>"
        f"<p>{len(people)} people. {len(stuck)} stuck. {len(cold)} never contacted.</p>"
        + block(
            "Stuck too long",
            [f"{p.full_name} — {p.stage.name if p.stage else 'no stage'}, {p.days_in_stage} days"
             for p in stuck[:15]],
        )
        + block("Nobody has contacted", [f"{p.full_name} — added {p.created_at:%b %d}" for p in cold[:15]])
        + block("New this week", [f"{p.full_name} — {p.source}" for p in new_this_week[:15]])
        + "</div>"
    )
    return send_email(to=to, subject="Journey: who needs a call this week", html=html)
