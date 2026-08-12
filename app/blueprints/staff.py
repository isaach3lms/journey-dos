from functools import wraps

from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..automations import enroll
from ..extensions import db, utcnow
from ..importers import import_giving_csv
from ..models import (
    Enrollment,
    GivingRecord,
    Interaction,
    Person,
    Stage,
    congregation,
    giving_totals,
    stage_summary,
)
from ..sequences import SEQUENCES

bp = Blueprint("staff", __name__, url_prefix="/staff")


def staff_only(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_staff:
            abort(404)
        return view(*args, **kwargs)

    return wrapped


@bp.route("/")
@staff_only
def dashboard():
    church_id = current_user.church_id
    summary = stage_summary(church_id)
    people = congregation(church_id).all()
    stuck = sorted(
        [p for p in people if p.is_stuck], key=lambda p: p.days_in_stage, reverse=True
    )
    no_contact = [p for p in people if p.last_contact_at is None]
    return render_template(
        "staff/dashboard.html",
        summary=summary,
        total=len(people),
        stuck=stuck[:10],
        stuck_count=len(stuck),
        no_contact=no_contact[:10],
        no_contact_count=len(no_contact),
    )


@bp.route("/people")
@staff_only
def people():
    church_id = current_user.church_id
    query = congregation(church_id)

    stage_id = request.args.get("stage", type=int)
    if stage_id:
        query = query.filter_by(stage_id=stage_id)

    search = (request.args.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Person.first_name.ilike(like),
                Person.last_name.ilike(like),
                Person.email.ilike(like),
            )
        )

    stages = Stage.query.filter_by(church_id=church_id).order_by(Stage.position).all()
    return render_template(
        "staff/people.html",
        people=query.order_by(Person.created_at.desc()).all(),
        stages=stages,
        stage_id=stage_id,
        q=search,
    )


@bp.route("/people/<int:person_id>", methods=["GET", "POST"])
@staff_only
def person(person_id: int):
    record = Person.query.filter_by(
        id=person_id, church_id=current_user.church_id
    ).first_or_404()
    stages = (
        Stage.query.filter_by(church_id=current_user.church_id).order_by(Stage.position).all()
    )

    if request.method == "POST":
        action = request.form.get("action")
        if action == "move":
            target = Stage.query.filter_by(
                id=request.form.get("stage_id", type=int), church_id=record.church_id
            ).first_or_404()
            record.move_to_stage(target, actor=current_user, note=request.form.get("note", ""))
            flash(f"Moved to {target.name}.", "success")
        elif action == "role":
            if not current_user.is_admin:
                flash("Only an admin can change access.", "error")
            elif record.id == current_user.id:
                # Nobody demotes themselves out of the only admin account by
                # accident on a Saturday night.
                flash("Change your own access from another admin account.", "error")
            else:
                new_role = request.form.get("role")
                if new_role in Person.ROLES:
                    record.role = new_role
                    flash(f"{record.first_name} is now {new_role}.", "success")

        elif action == "invite":
            from .auth import _send_link

            _send_link(record, "reset" if record.password_hash else "claim")
            flash(f"Account link emailed to {record.email}.", "success")

        elif action == "log":
            summary = (request.form.get("summary") or "").strip()
            if summary:
                record.log_contact(
                    request.form.get("kind", "call"), summary, actor=current_user
                )
                flash("Contact logged.", "success")
        db.session.commit()
        return redirect(url_for("staff.person", person_id=record.id))

    gifts = (
        GivingRecord.query.filter_by(church_id=record.church_id, person_id=record.id)
        .order_by(GivingRecord.given_at.desc())
        .all()
    )
    return render_template(
        "staff/person.html",
        p=record,
        stages=stages,
        kinds=Interaction.KINDS,
        roles=Person.ROLES,
        gifts=gifts,
        gift_total=sum(g.amount_cents for g in gifts) / 100.0,
        sequences=SEQUENCES,
    )


@bp.route("/people/new", methods=["GET", "POST"])
@staff_only
def new_person():
    """Manual entry for people who show up in a room, not on a form."""
    church_id = current_user.church_id
    stages = Stage.query.filter_by(church_id=church_id).order_by(Stage.position).all()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        first = (request.form.get("first_name") or "").strip()
        if not first or not email:
            flash("Name and email are both required.", "error")
            return render_template("staff/new_person.html", stages=stages)

        existing = Person.query.filter_by(church_id=church_id, email=email).first()
        if existing:
            flash("That email is already in the system.", "info")
            return redirect(url_for("staff.person", person_id=existing.id))

        record = Person(
            church_id=church_id,
            first_name=first,
            last_name=(request.form.get("last_name") or "").strip(),
            email=email,
            phone=(request.form.get("phone") or "").strip() or None,
            notes=(request.form.get("notes") or "").strip() or None,
            source="added by staff",
        )
        db.session.add(record)
        db.session.flush()
        stage = Stage.query.filter_by(
            id=request.form.get("stage_id", type=int), church_id=church_id
        ).first() or stages[0]
        record.move_to_stage(stage, actor=current_user, note="Added by staff")
        db.session.commit()
        flash(f"{record.full_name} added.", "success")
        return redirect(url_for("staff.person", person_id=record.id))

    return render_template("staff/new_person.html", stages=stages)


@bp.route("/giving", methods=["GET", "POST"])
@staff_only
def giving():
    church_id = current_user.church_id

    if request.method == "POST":
        upload = request.files.get("csv")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Upload a .csv exported from Tithely.", "error")
            return redirect(url_for("staff.giving"))
        result = import_giving_csv(upload.read(), church_id)
        flash(
            f"Imported {result['added']} gifts. "
            f"{result['skipped']} were already in the system. "
            f"{result['unmatched']} could not be matched to a person by email.",
            "success",
        )
        return redirect(url_for("staff.giving"))

    window = request.args.get("window", "30")
    since = None if window == "all" else utcnow() - timedelta(days=int(window))
    totals = giving_totals(church_id, since)
    return render_template("staff/giving.html", t=totals, window=window)


@bp.route("/automations")
@staff_only
def automations():
    enrollments = (
        Enrollment.query.filter_by(church_id=current_user.church_id)
        .order_by(Enrollment.enrolled_at.desc())
        .limit(100)
        .all()
    )
    return render_template(
        "staff/automations.html", enrollments=enrollments, sequences=SEQUENCES
    )
