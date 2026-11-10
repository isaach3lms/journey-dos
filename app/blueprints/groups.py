"""Groups.

A group leader here is a `GroupMembership` with `role='leader'`, which is a
different thing from a `User` whose login role is `leader`. Only the login role
grants access to this screen; group leadership is a fact about a room, not a
permission.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import login_required

from app.content import GROUPS
from app.extensions import db
from app.models import (
    GROUP_ROLES,
    ROLE_LEADER,
    ROLE_MEMBER,
    RSVP_CHOICES,
    Group,
    GroupMeeting,
    GroupMembership,
    Person,
)
from app.security import min_role
from app.timeutil import from_local

bp = Blueprint("groups", __name__, url_prefix="/groups")


@bp.get("/")
@login_required
@min_role("leader")
def index():
    groups = db.session.scalars(Group.for_church(g.church.id)).all()
    return render_template(
        "groups/index.html",
        church=g.church,
        content=GROUPS,
        groups=groups,
        in_a_group=Group.people_in_a_group(g.church.id),
        total_people=Person.total_for_church(g.church.id),
        active="groups",
    )


@bp.post("/")
@login_required
@min_role("leader")
def create():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(GROUPS["name_required"], "error")
        return redirect(url_for("groups.index"))

    group = Group(
        church_id=g.church.id,
        name=name[:160],
        meeting_pattern=(request.form.get("pattern") or "").strip() or None,
        location=(request.form.get("location") or "").strip() or None,
    )
    db.session.add(group)
    db.session.commit()

    flash(GROUPS["created"].format(name=group.name), "notice")
    return redirect(url_for("groups.detail", group_id=group.id))


@bp.get("/<int:group_id>/")
@login_required
@min_role("leader")
def detail(group_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    if group is None:
        abort(404)

    in_group = {m.person_id for m in group.memberships}
    candidates = [
        person
        for person in db.session.scalars(Person.for_church(g.church.id))
        if person.id not in in_group
    ]

    return render_template(
        "groups/detail.html",
        church=g.church,
        content=GROUPS,
        group=group,
        candidates=candidates,
        active="groups",
    )


@bp.post("/<int:group_id>/members/")
@login_required
@min_role("leader")
def add_member(group_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    if group is None:
        abort(404)

    person_id = request.form.get("person_id", type=int)
    # Scoped lookup. A person id from another church must not attach.
    person = Person.get_for_church(g.church.id, person_id) if person_id else None
    if person is None:
        abort(400)

    if group.has_person(person.id):
        flash(GROUPS["already_in"].format(name=person.full_name), "error")
        return redirect(url_for("groups.detail", group_id=group.id))

    role = (request.form.get("role") or ROLE_MEMBER).strip()
    if role not in GROUP_ROLES:
        role = ROLE_MEMBER

    db.session.add(
        GroupMembership(
            church_id=g.church.id, group_id=group.id, person_id=person.id, role=role
        )
    )
    db.session.commit()

    flash(
        GROUPS["added"].format(name=person.full_name, group=group.name), "notice"
    )
    return redirect(url_for("groups.detail", group_id=group.id))


@bp.post("/<int:group_id>/members/<int:membership_id>/role/")
@login_required
@min_role("leader")
def set_role(group_id: int, membership_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    if group is None:
        abort(404)

    membership = db.session.scalar(
        db.select(GroupMembership).where(
            GroupMembership.id == membership_id,
            GroupMembership.church_id == g.church.id,
            GroupMembership.group_id == group.id,
        )
    )
    if membership is None:
        abort(404)

    membership.role = ROLE_MEMBER if membership.is_leader else ROLE_LEADER
    db.session.commit()

    flash(
        GROUPS["role_changed"].format(
            name=membership.person.full_name, role=membership.role
        ),
        "notice",
    )
    return redirect(url_for("groups.detail", group_id=group.id))


@bp.post("/<int:group_id>/members/<int:membership_id>/remove/")
@login_required
@min_role("leader")
def remove_member(group_id: int, membership_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    if group is None:
        abort(404)

    membership = db.session.scalar(
        db.select(GroupMembership).where(
            GroupMembership.id == membership_id,
            GroupMembership.church_id == g.church.id,
            GroupMembership.group_id == group.id,
        )
    )
    if membership is None:
        abort(404)

    name = membership.person.full_name
    db.session.delete(membership)
    db.session.commit()

    flash(GROUPS["removed"].format(name=name), "notice")
    return redirect(url_for("groups.detail", group_id=group.id))


@bp.post("/<int:group_id>/meetings/")
@login_required
@min_role("leader")
def add_meeting(group_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    if group is None:
        abort(404)

    raw = (request.form.get("meets_at") or "").strip()
    try:
        # A staff member types a wall-clock time. Stored as UTC, rendered back
        # in the church's zone. The conversion happens here and nowhere else.
        naive = datetime.fromisoformat(raw)
    except ValueError:
        flash(GROUPS["meeting_bad_time"], "error")
        return redirect(url_for("groups.detail", group_id=group.id))

    db.session.add(
        GroupMeeting(
            church_id=g.church.id,
            group_id=group.id,
            meets_at=from_local(naive, g.church),
            location=(request.form.get("location") or "").strip() or group.location,
            notes=(request.form.get("notes") or "").strip() or None,
        )
    )
    db.session.commit()

    flash(GROUPS["meeting_saved"], "notice")
    return redirect(url_for("groups.detail", group_id=group.id))


@bp.post("/<int:group_id>/meetings/<int:meeting_id>/delete/")
@login_required
@min_role("leader")
def delete_meeting(group_id: int, meeting_id: int):
    group = Group.get_for_church(g.church.id, group_id)
    meeting = GroupMeeting.get_for_church(g.church.id, meeting_id)
    if group is None or meeting is None or meeting.group_id != group.id:
        abort(404)

    db.session.delete(meeting)
    db.session.commit()

    flash(GROUPS["meeting_deleted"], "notice")
    return redirect(url_for("groups.detail", group_id=group.id))
