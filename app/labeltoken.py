"""Signed links to a printable tag.

**Why a signed link rather than a signed-in session.** The tags open outside
the app so that a print sheet exists at all, and a browser opened from the app
does not carry the app's cookies: an iOS web view and Safari keep separate
cookie stores, so a `@login_required` PDF would have met a login screen at the
one moment nobody can stop to type a password. The link carries its own
authorization instead.

**What the signature buys.** The token is the church, the session, and the
exact check-in rows, signed with the application secret. It cannot be forged,
cannot be edited to point at another family, and cannot be pivoted to another
church: the church in the payload is checked against the church resolved from
the host, so a token minted at one tenant is inert at another.

**Why it expires quickly.** A tag carries a live pickup code, which is the
credential for collecting a child. Ten minutes is far longer than the gap
between checking a family in and tapping print, and short enough that a URL
left in a browser history is not a standing key to somebody's children.

**What it is not.** It grants one read of one family's tags. There is nothing
to write, no other record it reaches, and no session it creates. Somebody
holding a stolen token for ten minutes learns one pickup code, which is the
same thing they would learn by reading the tag on the child.
"""

from __future__ import annotations

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Its own salt, so a token minted here can never be replayed against another
# signer that happens to share the application secret.
SALT = "kids-name-tags"

# The sample tag gets its own salt and therefore its own signer. A sample
# token must not be loadable as a real one and a real one must not be loadable
# as a sample: the two routes return different things and one of them carries
# a live pickup code. Separate salts make that structural rather than a check
# somebody has to remember to write.
SALT_SAMPLE = "kids-sample-tag"

MAX_AGE_SECONDS = 10 * 60

# Longer than a real tag's, on purpose. A sample carries no pickup code, no
# child's name and nothing about any family, so the reason real tokens expire
# in ten minutes does not apply. A volunteer testing a printer on a Tuesday
# evening should not have the link die while they walk to the desk.
SAMPLE_MAX_AGE_SECONDS = 60 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def _sample_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"], salt=SALT_SAMPLE
    )


def sign_sample(*, church_id: int) -> str:
    """A token for a sample tag, which names no family and no session."""
    return _sample_serializer().dumps({"c": int(church_id)})


def verify_sample(token: str, church_id: int) -> bool:
    """Whether this is a live sample token for this church. Never raises."""
    if not token:
        return False
    try:
        payload = _sample_serializer().loads(
            token, max_age=SAMPLE_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return False
    except Exception:  # noqa: BLE001 - a malformed token is not an error here
        return False
    return isinstance(payload, dict) and payload.get("c") == int(church_id)


def sign(*, church_id: int, session_id: int, checkin_ids) -> str:
    """A token for exactly these check-in rows.

    The rows are named rather than the household, because a staff reprint of
    one child's tag must not widen into the whole family's.
    """
    return _serializer().dumps({
        "c": int(church_id),
        "s": int(session_id),
        "k": sorted(int(i) for i in checkin_ids),
    })


def verify(token: str, church_id: int) -> dict | None:
    """The payload, or None. Never raises on bad input.

    `church_id` is the church resolved from the host, not something from the
    token. Checking the two against each other is what makes a token inert at
    another tenant.
    """
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    except Exception:  # noqa: BLE001 - a malformed token is not an error here
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("c") != int(church_id):
        return None
    if not isinstance(payload.get("k"), list) or not payload["k"]:
        return None
    return payload
