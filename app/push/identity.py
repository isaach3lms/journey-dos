"""Handing the app the id it needs to receive notifications.

The provider holds the devices and addresses people by an external id that the
app sets when somebody signs in. The app is a wrapper around this site, so the
id has to reach it from a page.

**Minted here rather than at sign-in.** A token created during login would be
missing for everybody who was already signed in when this shipped, and the
only way to fix that would be a backfill or a forced sign-out of the whole
church. Minting it the first time a signed-in person loads a page covers
everybody, costs one write per account ever, and needs no migration step
anybody has to remember to run.
"""

from __future__ import annotations

from flask_login import current_user

from app.extensions import db


def register_push_identity(app) -> None:
    @app.context_processor
    def _push_identity():
        if not getattr(current_user, "is_authenticated", False):
            return {"push_external_id": None}

        existing = getattr(current_user, "push_external_id", None)
        if existing:
            return {"push_external_id": existing}

        # First page this account has loaded since notifications existed.
        try:
            minted = current_user.ensure_push_external_id()
            db.session.commit()
        except Exception:  # noqa: BLE001
            # A read-only replica, a rolled-back request, a column that has
            # not been migrated yet. None of those is a reason to fail a page
            # render; the worst case is notifications waiting for the next
            # load, and the email is going either way.
            db.session.rollback()
            app.logger.exception("could not mint a push id")
            return {"push_external_id": None}

        return {"push_external_id": minted}
