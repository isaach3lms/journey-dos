"""What a tablet in the lobby is allowed to do.

A kiosk account holds the `leader` role, because that is what the check-in
screens already require and inventing a fourth role to sit beside
member/leader/staff would mean revisiting every `at_least` call in the system.
So the role opens the door and this module closes every other one.

**The allowlist is the whole design, and its direction is the point.** A
denylist of screens a kiosk may not see would be wrong within a week: the next
feature anybody adds is reachable from the tablet by default, and nothing
fails, and nobody finds out. Naming the handful of endpoints a tablet needs
means a new screen is unreachable from the lobby until somebody deliberately
decides otherwise. That is the correct direction for a device that sits
unattended in a hallway.

**What this is protecting.** The account the church was using before this
existed was a staff login, which can read the roster, the giving mirror, every
conversation and every pastoral note. The tablet it was signed into is
physically available to anyone who walks past it. Nothing about the check-in
screens required that level of access; it was simply the only account anybody
had.

**Session length is the other half.** A kiosk is useless if it logs out, and a
volunteer who finds a login screen on a Sunday morning will sign in with their
own account to get past it, which puts the problem straight back. So a kiosk
session is deliberately long, and the restriction above is what makes that
safe: a year-long session that can only reach the check-in screens is a much
smaller thing to leave on a table than an eight-hour session that can read
everything.
"""

from __future__ import annotations

from datetime import timedelta

from flask import flash, redirect, request, url_for
from flask_login import current_user

from app.content import KIDS

# A year. The number is chosen so that nobody ever meets a login screen in
# ordinary use: a tablet set up in September is still signed in the following
# August. Revoking is a button in Settings, which works immediately and does
# not depend on this expiring.
KIOSK_SESSION = timedelta(days=365)

# How long a check-in screen sits untouched before it goes back to the start.
#
# Not a logout. The tablet stays signed in; the screen simply stops showing
# the Hollands' four children to whoever walks up next. Ninety seconds is
# longer than a family takes and shorter than the gap between them.
IDLE_RESET_SECONDS = 90

# How long an ORDINARY account may sit on a check-in screen before it is
# signed out. This is the case the church was actually in: a staff member
# signs in to set the tablet up and walks away, and their account is now on a
# table in a hallway. A kiosk account is exempt because being signed in is its
# entire job.
# Thirty minutes rather than something tighter, because a desk running
# check-in from a laptop is on these same screens, and signing a volunteer out
# mid-morning to protect against a tablet left out all week is the wrong trade.
# Once a church has a kiosk account this case should not arise at all.
IDLE_LOGOUT_SECONDS = 30 * 60

# Everything a tablet needs and nothing else.
#
# Deliberately written as endpoint names rather than a URL prefix. A prefix
# would quietly admit anything added under /kids/ later, including the staff
# roster, which lives there.
KIOSK_ENDPOINTS = frozenset({
    # The check-in flow itself.
    "kids.kiosk",
    "kids.kiosk_pin",
    "kids.kiosk_family",
    "kids.kiosk_check_in",
    "kids.kiosk_forgot",
    # The tags. `kiosk_check_in` renders them itself on the way back, so the
    # only separate endpoint here is the printable file.
    "kids.kiosk_labels_pdf",
    # Setting the tablet up, and giving it back.
    "kids.kiosk_setup",
    "auth.logout",
    # Chrome the browser fetches on its own. None of these render anybody.
    "static",
    "health.healthz",
    "health.readyz",
    "pwa.manifest",
    "pwa.service_worker",
    "pwa.offline",
    "public.privacy",
    "public.community",
})


def is_kiosk_session() -> bool:
    return bool(
        getattr(current_user, "is_authenticated", False)
        and getattr(current_user, "is_kiosk", False)
    )


# The screens the idle timer runs on. Deliberately not every screen in the
# app: signing a staff member out after fifteen minutes everywhere would be a
# large change nobody asked for. The lobby tablet is the problem being solved,
# so the lobby tablet is where it applies.
IDLE_ENDPOINTS = frozenset({
    "kids.kiosk",
    "kids.kiosk_pin",
    "kids.kiosk_family",
    "kids.kiosk_check_in",
    "kids.kiosk_forgot",
})


def register_idle_reset(app) -> None:
    """Tell the kiosk templates when to go back to the start."""

    @app.context_processor
    def _idle():
        from flask import url_for

        blank = {"idle_seconds": 0, "idle_target": "", "idle_action": ""}

        if request.endpoint not in IDLE_ENDPOINTS:
            return blank
        if not getattr(current_user, "is_authenticated", False):
            return blank

        if not is_kiosk_session():
            # A person's account on a lobby tablet. Sign it out.
            return {
                "idle_seconds": IDLE_LOGOUT_SECONDS,
                "idle_target": url_for("auth.logout"),
                "idle_action": "signout",
            }

        if request.endpoint == "kids.kiosk":
            # Already at the start, and nothing on screen belongs to anybody.
            # Reloading it every ninety seconds forever buys nothing.
            return blank

        return {
            "idle_seconds": IDLE_RESET_SECONDS,
            "idle_target": url_for("kids.kiosk"),
            "idle_action": "reset",
        }


def register_kiosk_guard(app) -> None:
    """Hold a kiosk account to the check-in screens.

    Registered on the app rather than on the kids blueprint, because the whole
    point is the endpoints it does *not* know about.
    """

    @app.before_request
    def _kiosk_stays_on_the_kiosk():
        if not is_kiosk_session():
            return None
        if request.endpoint in KIOSK_ENDPOINTS:
            return None
        if request.endpoint is None:
            # A 404 on its way to the error handler. Letting it through means
            # a missing page looks like a missing page.
            return None

        # Sent back rather than refused. Whoever tapped this is a volunteer at
        # a desk with a queue in front of them, and a 403 page reads as a
        # broken tablet. The check-in screen reads as the tablet doing its job.
        flash(KIDS["kiosk_only"], "error")
        return redirect(url_for("kids.kiosk"))
