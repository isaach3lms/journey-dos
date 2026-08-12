from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..content import GIVE
from ..extensions import db, utcnow
from ..ministry import Group, GroupMembership, ServiceAssignment, announcements_for
from ..models import Church, Stage

bp = Blueprint("portal", __name__, url_prefix="/app")


def _give_url(church: Church) -> str:
    url = church.tithely_give_url or current_app.config.get("TITHELY_GIVE_URL")
    form_id = church.tithely_form_id or current_app.config.get("TITHELY_FORM_ID")
    if not url and form_id:
        url = f"https://give.tithe.ly/?formId={form_id}"
    return url


@bp.route("/")
@login_required
def home():
    stages = (
        Stage.query.filter_by(church_id=current_user.church_id).order_by(Stage.position).all()
    )
    upcoming = [
        a
        for a in current_user.assignments
        if a.service and a.service.starts_at >= utcnow() and a.status != "declined"
    ]
    upcoming.sort(key=lambda a: a.service.starts_at)
    church = db.session.get(Church, current_user.church_id)
    return render_template(
        "portal/home.html",
        stages=stages,
        announcements=announcements_for(current_user)[:5],
        assignments=upcoming[:3],
        tzname=church.timezone,
    )


@bp.route("/journey")
@login_required
def journey():
    stages = (
        Stage.query.filter_by(church_id=current_user.church_id).order_by(Stage.position).all()
    )
    return render_template("portal/journey.html", stages=stages, events=current_user.stage_events)


@bp.route("/groups", methods=["GET", "POST"])
@login_required
def groups():
    church_id = current_user.church_id

    if request.method == "POST":
        group = Group.query.filter_by(
            id=request.form.get("group_id", type=int), church_id=church_id
        ).first_or_404()
        membership = GroupMembership.query.filter_by(
            group_id=group.id, person_id=current_user.id
        ).first()

        if request.form.get("action") == "leave":
            if membership:
                membership.status = "left"
                flash(f"You left {group.name}.", "info")
        else:
            if not group.has_room and not membership:
                flash(f"{group.name} is full. Try another one.", "error")
            elif membership:
                membership.status = "joined"
                membership.joined_at = utcnow()
                flash(f"You are back in {group.name}.", "success")
            else:
                db.session.add(
                    GroupMembership(
                        church_id=church_id, group_id=group.id, person_id=current_user.id
                    )
                )
                flash(f"You joined {group.name}.", "success")
        db.session.commit()
        return redirect(url_for("portal.groups"))

    all_groups = Group.query.filter_by(church_id=church_id).order_by(Group.name).all()
    mine = {m.group_id for m in current_user.group_memberships if m.status == "joined"}
    return render_template("portal/groups.html", groups=all_groups, mine=mine)


@bp.route("/serving", methods=["GET", "POST"])
@login_required
def serving():
    if request.method == "POST":
        assignment = ServiceAssignment.query.filter_by(
            id=request.form.get("assignment_id", type=int), person_id=current_user.id
        ).first_or_404()
        answer = request.form.get("answer")
        if answer in ("confirmed", "declined"):
            assignment.status = answer
            assignment.responded_at = utcnow()
            db.session.commit()
            flash(
                "Thanks, you are confirmed."
                if answer == "confirmed"
                else "Noted. A leader will find cover.",
                "success",
            )
        return redirect(url_for("portal.serving"))

    church = db.session.get(Church, current_user.church_id)
    upcoming = [
        a for a in current_user.assignments if a.service and a.service.starts_at >= utcnow()
    ]
    upcoming.sort(key=lambda a: a.service.starts_at)
    return render_template(
        "portal/serving.html",
        assignments=upcoming,
        teams=[m.team for m in current_user.team_memberships],
        tzname=church.timezone,
    )


@bp.route("/give")
@login_required
def give():
    church = db.session.get(Church, current_user.church_id)
    return render_template("portal/give.html", c=GIVE, give_url=_give_url(church))
