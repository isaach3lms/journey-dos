"""Giving.

A link-out, not a payment integration. This application never sees a card
number, which means it is not a payment environment and carries no PCI scope.
Increment 13 adds a read-only mirror of the giving ledger so the system can say
"Chris Vaughn stopped giving in March"; nothing here writes to Tithely and
nothing here reads from it.
"""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import login_required

from app.content import GIVING
from app.extensions import db
from app.giving import PROVIDER_TITHELY, PROVIDERS, InvalidGivingURL, validate_giving_url
from app.security import min_role

bp = Blueprint("giving", __name__, url_prefix="/giving")


@bp.get("/")
@login_required
@min_role("staff")
def index():
    return render_template(
        "giving/index.html",
        church=g.church,
        content=GIVING,
        providers=PROVIDERS,
        active="giving",
    )


@bp.post("/")
@login_required
@min_role("staff")
def configure():
    provider = (request.form.get("provider") or PROVIDER_TITHELY).strip()
    if provider not in PROVIDERS:
        provider = PROVIDER_TITHELY

    try:
        admin_url = validate_giving_url(request.form.get("admin_url"), provider)
        form_url = validate_giving_url(request.form.get("form_url"), provider)
    except InvalidGivingURL as exc:
        # The message names the actual problem, because a staff member pasting
        # a link needs to know which link and why, not that something failed.
        flash(str(exc), "error")
        return redirect(url_for("giving.index"))

    g.church.giving_provider = provider
    g.church.giving_admin_url = admin_url
    g.church.giving_form_url = form_url
    db.session.commit()

    flash(GIVING["saved"] if (admin_url or form_url) else GIVING["cleared"], "notice")
    return redirect(url_for("giving.index"))
