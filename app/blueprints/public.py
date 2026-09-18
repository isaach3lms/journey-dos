"""The pages anybody can reach without signing in.

Two of them, and both exist because somebody outside the app needs them: a
member who cannot sign in, and an app store reviewer who has to see that
support and a privacy policy exist before the app is allowed to ship.

Per church, from the same brand tokens as every other screen, because a page
that says "Between Sundays" to a member of Journey is a page they do not
trust.
"""

from __future__ import annotations

from flask import Blueprint, g, render_template

from app.content import PRIVACY, SUPPORT

bp = Blueprint("public", __name__)

# Bumped when the policy text changes, so a church can point at a version.
LAST_UPDATED = "17 September 2026"


@bp.get("/privacy/")
def privacy():
    return render_template(
        "public/privacy.html",
        church=g.church,
        content=PRIVACY,
        updated=LAST_UPDATED,
    )


@bp.get("/support/")
def support():
    """Public on purpose.

    The most common reason somebody needs help is that they cannot sign in,
    so a help page behind a sign-in is the one page guaranteed to be useless
    to the people who need it.
    """
    return render_template(
        "public/support.html",
        church=g.church,
        content=SUPPORT,
    )
