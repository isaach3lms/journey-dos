"""The privacy policy.

Public, per church, and generated from what the application actually does
rather than from a template somebody bought. Both app stores require a policy
URL, and Apple's privacy questionnaire asks you to declare each data type and
whether it is linked to identity, so a generic page does not survive contact
with review.

It is also the only page in the system whose accuracy is a promise rather than
a feature. If a future increment starts collecting something, this page is part
of that increment.
"""

from __future__ import annotations

from flask import Blueprint, g, render_template

from app.content import PRIVACY

bp = Blueprint("privacy", __name__)

# Bumped when the policy text changes, so a church can point at a version.
LAST_UPDATED = "8 September 2026"


@bp.get("/privacy/")
def policy():
    return render_template(
        "privacy/policy.html",
        church=g.church,
        content=PRIVACY,
        updated=LAST_UPDATED,
    )
