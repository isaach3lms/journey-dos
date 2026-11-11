"""Creating a sign-in account and inviting somebody to set a password.

Shared by Settings and the People roster. Staff never type a password: one a
staff member sets has to be told to somebody over text or in a hallway, and is
then a password two people know, one of whom wrote it down.
"""

from __future__ import annotations

from flask import g, request, url_for

from app.content import SETTINGS
from app.extensions import db
from app.mail import NotQueued, queue
from app.models import PasswordResetToken, User
from app.models.password_reset import LIFETIME_MINUTES
from app.models.user import ROLES


def send_set_password(user, actor) -> bool:
    """Email a one-time link so the person chooses their own password."""
    PasswordResetToken.invalidate_all_for(user)
    _, raw = PasswordResetToken.issue(user)
    db.session.flush()

    link = url_for(
        "auth.reset", token=raw, _external=True,
        _scheme="https" if request.is_secure else "http",
    )
    try:
        queue(
            church_id=g.church.id,
            # Transactional: it is the account itself, not church news.
            category="account",
            subject=SETTINGS["invite_subject"].format(church=g.church.name),
            body_text=SETTINGS["invite_body"].format(
                name=user.name, church=g.church.name,
                actor=getattr(actor, "name", "Someone"),
                link=link, minutes=LIFETIME_MINUTES,
            ),
            to_email=user.email,
            to_name=user.name,
        )
    except NotQueued:
        return False
    return True


def create_login(church_id: int, name: str, email: str, role: str,
                 actor, person=None) -> User | None:
    """Create an account and invite them. Returns None if one already exists.

    Caller commits. Verified on creation, because a staff member typing an
    address vouches for it more strongly than a click in an inbox.
    """
    if role not in ROLES:
        role = "member"

    email = (email or "").strip().lower()
    if not email or User.by_email(church_id, email) is not None:
        return None

    user = User(church_id=church_id, email=email, name=name[:120], role=role)
    user.mark_verified()
    user.set_unusable_password()
    db.session.add(user)
    db.session.flush()

    if person is not None:
        user.person_id = person.id
    else:
        user.link_person_by_email()

    send_set_password(user, actor)
    return user
