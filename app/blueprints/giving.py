"""Giving.

A link-out, not a payment integration. This application never sees a card
number, which means it is not a payment environment and carries no PCI scope.
Increment 13 adds a read-only mirror of the giving ledger so the system can say
"Chris Vaughn stopped giving in March"; nothing here writes to Tithely and
nothing here reads from it.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.content import GIVING
from app.extensions import db
from app.giving import PROVIDER_TITHELY, PROVIDERS, InvalidGivingURL, validate_giving_url
from app.audit import record
from app.matching import suggest_for_gift
from app.models.audit import CREDENTIAL_CHANGED, GIFT_MATCHED
from app.models import (
    ExternalGift,
    ExternalRecurringGift,
    IntegrationCredential,
    Person,
)
from app.models.giving_mirror import PROVIDER_TITHELY, STATUS_ACTIVE
from app.security import min_role

bp = Blueprint("giving", __name__, url_prefix="/giving")


@bp.get("/")
@login_required
@min_role("staff")
def index():
    credential = IntegrationCredential.for_provider(g.church.id, PROVIDER_TITHELY)
    return render_template(
        "giving/index.html",
        church=g.church,
        content=GIVING,
        providers=PROVIDERS,
        credential=credential,
        stopped=db.session.scalars(
            ExternalRecurringGift.stopped(g.church.id, limit=8)
        ).all(),
        stopped_count=ExternalRecurringGift.stopped_count(g.church.id),
        unmatched_count=ExternalGift.unmatched_count(g.church.id),
        mtd_cents=ExternalGift.month_to_date_cents(g.church.id),
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


# ---------------------------------------------------------------------------
# Increment 13: the read-only mirror
# ---------------------------------------------------------------------------

@bp.post("/keys/")
@login_required
@min_role("staff")
def save_keys():
    """Store the provider keys. The private one is encrypted before it lands.

    Staff only, not leaders. These keys can read every gift the church has ever
    received, which is a different level of trust from planning a service.
    """
    credential = IntegrationCredential.for_provider(g.church.id, PROVIDER_TITHELY)
    if credential is None:
        credential = IntegrationCredential(
            church_id=g.church.id, provider=PROVIDER_TITHELY
        )
        db.session.add(credential)

    credential.public_key = (request.form.get("public_key") or "").strip()[:200] or None
    credential.organization_ref = (
        request.form.get("organization_ref") or ""
    ).strip()[:120] or None

    # An empty box means "leave it alone", not "delete it". Otherwise loading
    # the page and saving an unrelated field would silently wipe the key.
    private = (request.form.get("private_key") or "").strip()
    if private:
        credential.set_private_key(private, current_app.config["SECRET_KEY"])

    credential.status = STATUS_ACTIVE if credential.is_usable else "pending_setup"
    # The key itself is never in the entry. Recording what changed would take
    # a secret out of the one column that protects it.
    record(
        CREDENTIAL_CHANGED,
        f"Provider keys changed for {PROVIDER_TITHELY}",
        actor=current_user,
        subject_type="integration_credential",
        subject_id=credential.id,
        detail="A private key was replaced." if private else "Public settings only.",
    )
    db.session.commit()

    flash(GIVING["keys_saved"], "notice")
    return redirect(url_for("giving.index"))


@bp.get("/unmatched/")
@login_required
@min_role("staff")
def unmatched():
    gifts = db.session.scalars(ExternalGift.unmatched(g.church.id)).all()
    return render_template(
        "giving/unmatched.html",
        church=g.church,
        content=GIVING,
        gifts=gifts,
        suggestions={gift.id: suggest_for_gift(gift) for gift in gifts},
        people=db.session.scalars(Person.for_church(g.church.id)).all(),
        active="giving",
    )


@bp.post("/unmatched/<int:gift_id>/attach/")
@login_required
@min_role("staff")
def attach_gift(gift_id: int):
    gift = ExternalGift.get_for_church(g.church.id, gift_id)
    if gift is None:
        abort(404)

    person_id = request.form.get("person_id", type=int)
    if not person_id:
        # Refused rather than defaulted. Attaching somebody's gift to whoever
        # happened to be first in a dropdown is worse than doing nothing.
        flash(GIVING["queue_choose_first"], "error")
        return redirect(url_for("giving.unmatched"))

    person = Person.get_for_church(g.church.id, person_id)
    if person is None:
        abort(400)

    gift.attach(person, user=current_user)
    record(
        GIFT_MATCHED,
        f"{gift.amount_display} matched to {person.full_name}",
        actor=current_user,
        subject_type="external_gift",
        subject_id=gift.id,
        subject_label=person.full_name,
        detail=f"Gift received {gift.received_on:%B %-d, %Y}.",
    )
    db.session.commit()

    flash(
        GIVING["queue_attached"].format(
            amount=gift.amount_display, name=person.full_name
        ),
        "notice",
    )
    return redirect(url_for("giving.unmatched"))


@bp.post("/unmatched/<int:gift_id>/ignore/")
@login_required
@min_role("staff")
def ignore_gift(gift_id: int):
    """Not a person at this church: a business, an anonymous gift, a test."""
    gift = ExternalGift.get_for_church(g.church.id, gift_id)
    if gift is None:
        abort(404)

    gift.set_aside(user=current_user)
    db.session.commit()

    flash(GIVING["queue_ignored"], "notice")
    return redirect(url_for("giving.unmatched"))
