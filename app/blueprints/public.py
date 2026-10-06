"""The pages anybody can reach without signing in.

Three of them read, and one writes. The read-only pages exist because
somebody outside the app needs them: a member who cannot sign in, and an app
store reviewer who has to see that support and a privacy policy exist before
the app is allowed to ship. The fourth is the connect card, which is the only
form in this product that anybody on the internet can post to.

Per church, from the same brand tokens as every other screen, because a page
that says "Between Sundays" to a member of Journey is a page they do not
trust.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    g,
    render_template,
    request,
    session,
    url_for,
)

from app.content import ACCOUNT, COMMUNITY, PRIVACY, SUPPORT, WELCOME
from app.models.guest import HEARD_CHOICES, HEARD_LABELS

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


@bp.get("/community/")
def community():
    """The rules for chat, readable before anybody agrees to them.

    Public because a person deciding whether to join should see the rules
    first, and because an app reviewer has to be able to read them.
    """
    return render_template(
        "public/community.html",
        church=g.church,
        content=COMMUNITY,
    )


# ---------------------------------------------------------------------------
# The connect card
#
# The one page here that writes to the database, and the only form in the
# product that anybody on the internet can post to. Everything below is
# shaped by that: three cheap defences at the door rather than a review queue
# behind it, because a queue is work somebody has to keep doing and these are
# not.
# ---------------------------------------------------------------------------

# A field no human sees and every naive bot fills in. Named like something
# worth filling rather than "honeypot", and left out of the success message
# either way so a bot learns nothing from the response.
TRAP_FIELD = "website"

# Two cards from one phone inside this window is a person pressing submit
# twice, or a script. A welcome desk tablet taking cards one after another
# needs to get through, so this is seconds rather than one-per-session.
COOLDOWN_SECONDS = 15
RECENT_KEY = "guest_card_at"


@bp.get("/welcome/")
def welcome():
    """The card itself, reachable by anybody with the link or a QR code."""
    return render_template(
        "public/welcome.html",
        church=g.church,
        content=WELCOME,
        heard_choices=[(code, HEARD_LABELS[code]) for code in HEARD_CHOICES],
        trap=TRAP_FIELD,
        sent=False,
    )


@bp.post("/welcome/")
def welcome_submit():
    from app.extensions import db
    from app import guests

    def page(error=None, sent=False):
        return render_template(
            "public/welcome.html",
            church=g.church,
            content=WELCOME,
            heard_choices=[(code, HEARD_LABELS[code]) for code in HEARD_CHOICES],
            trap=TRAP_FIELD,
            error=error,
            sent=sent,
            # So a refused card does not make somebody type it all again.
            values=request.form,
        )

    # A filled trap is answered exactly like a good card. Telling a bot it
    # failed is how it learns to stop filling the field.
    if (request.form.get(TRAP_FIELD) or "").strip():
        return page(sent=True)

    now = datetime.now(timezone.utc).timestamp()
    last = session.get(RECENT_KEY)
    if last is not None and now - last < COOLDOWN_SECONDS:
        return page(error=WELCOME["too_fast"])

    try:
        card = guests.submit(
            g.church.id,
            first_name=request.form.get("first_name", ""),
            last_name=request.form.get("last_name", ""),
            email=request.form.get("email", ""),
            phone=request.form.get("phone", ""),
            heard=(request.form.get("heard") or "").strip(),
            note=request.form.get("note", ""),
            wants_contact=bool(request.form.get("wants_contact")),
            wants_account=bool(request.form.get("wants_account")),
        )
    except guests.CardRefused as refused:
        return page(error=WELCOME[refused.reason])

    # Telling staff must never be able to fail the submission. The guest has
    # handed over their details and the row is written; an error page here
    # would tell them their card did not go through when it did, and they
    # would fill it in again.
    try:
        scheme = "https" if request.is_secure else "http"
        path = url_for("people.guests")
        link = url_for("people.guests", _external=True, _scheme=scheme)
        guests.alert_staff(g.church, card, link=link, path=path)
        if card.wants_account:
            guests.request_account(g.church, card, link=link)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("could not alert staff about a card")

    db.session.commit()
    session[RECENT_KEY] = now

    # Rendered rather than redirected, because a guest on a lobby wifi that
    # drops between the two requests would otherwise see a browser error
    # after their card had already been saved.
    return page(sent=True)


# ---------------------------------------------------------------------------
# Requesting an account
#
# This replaces "Create an account" on the sign-in page. The old link handed
# a login to anybody who typed an address; this asks the same person who they
# are and who is in their household, and a human decides.
#
# Same three defences as the connect card, and the same reason: it is a form
# on the open internet. The trap field and the cooldown are shared rather than
# re-invented, because two copies of a spam defence is how one of them gets
# improved and the other does not.
# ---------------------------------------------------------------------------

ACCOUNT_RECENT_KEY = "account_request_at"


def _account_page(error=None, sent=False):
    return render_template(
        "public/request_account.html",
        church=g.church,
        content=ACCOUNT,
        trap=TRAP_FIELD,
        error=error,
        sent=sent,
        values=request.form if request.method == "POST" else None,
    )


@bp.get("/request-account/")
def request_account():
    return _account_page()


@bp.post("/request-account/")
def request_account_submit():
    from app.extensions import db
    from app import accounts_requested

    if (request.form.get(TRAP_FIELD) or "").strip():
        return _account_page(sent=True)

    now = datetime.now(timezone.utc).timestamp()
    last = session.get(ACCOUNT_RECENT_KEY)
    if last is not None and now - last < COOLDOWN_SECONDS:
        return _account_page(error=ACCOUNT["too_fast"])

    try:
        made = accounts_requested.submit(
            g.church,
            first_name=request.form.get("first_name", ""),
            last_name=request.form.get("last_name", ""),
            email=request.form.get("email", ""),
            phone=request.form.get("phone", ""),
            address=request.form.get("address", ""),
            household=request.form.get("household", ""),
        )
    except accounts_requested.Refused as refused:
        return _account_page(error=ACCOUNT[refused.reason])

    # Emailing must never fail the request. The row is written and the person
    # has been told it is in; an error page at this point sends them round the
    # form again and produces a second one.
    try:
        scheme = "https" if request.is_secure else "http"
        accounts_requested.alert(
            g.church, made,
            link=url_for("people.requests", _external=True, _scheme=scheme),
        )
    except Exception:  # noqa: BLE001
        current_app.logger.exception("could not send an account request")

    db.session.commit()
    session[ACCOUNT_RECENT_KEY] = now
    return _account_page(sent=True)
