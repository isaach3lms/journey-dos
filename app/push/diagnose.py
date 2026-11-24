"""Ask the notification service what it actually has.

**Why this exists.** Every other way of answering "why is this phone quiet"
asks the phone. The phone can only report what it did: that it got permission,
that it handed over a token. It cannot see whether the service kept that
token, whether the service holds the Apple credentials needed to deliver to
it, or whether Apple is dropping the message after the service accepted it.

So three rounds of debugging went: read this screen, tell me what it says,
still nothing. Each round cost a day and moved nothing, because the screen
was reporting the half of the system that was working.

This module asks the other half. The service has an API, the server already
holds the key for it, and the two questions that actually separate the causes
are answerable from it in one request each:

  * Does the service hold an Apple push credential for this app at all, and
    is it pointed at the same environment our tokens come from? Without it
    every send is accepted and nothing is delivered, which is exactly the
    symptom that looks like a bug in our code.
  * Does the service have a push subscription for this person, is it enabled,
    and does it carry a token? That separates "the phone never registered"
    from "it registered and the service dropped it".

**No secrets leave the server.** The key is read from config where it already
lives, used for the request, and never rendered. What reaches the screen is
findings, and tokens are shortened to their last few characters so two
subscriptions can be told apart without printing a credential.

**Read-only.** Nothing here changes anything at the service. A diagnostic
that can also reconfigure is a diagnostic nobody dares run.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

BASE = "https://api.onesignal.com"

# The push channels the service names for Apple devices. An app wrapped with
# Capacitor reports iOSPush; Safari web push is a different channel and a
# different credential, so it is listed and not conflated.
APPLE_PUSH = ("iOSPush",)
SAFARI_PUSH = ("SafariPush",)
PUSH_TYPES = (
    "iOSPush", "AndroidPush", "HuaweiPush", "FireOSPush",
    "ChromePush", "FirefoxPush", "SafariPush",
)

# What the service's Apple setup adds up to.
APPLE_NO_CREDENTIAL = "apple_no_credential"
APPLE_SANDBOX = "apple_sandbox"
APPLE_READY = "apple_ready"
APPLE_UNKNOWN = "apple_unknown"

# What it has for one person.
PERSON_UNKNOWN = "person_unknown"
PERSON_NOT_FOUND = "person_not_found"
PERSON_NO_PUSH = "person_no_push"
PERSON_NO_APPLE = "person_no_apple"
PERSON_APPLE_OFF = "person_apple_off"
PERSON_APPLE_NO_TOKEN = "person_apple_no_token"
PERSON_READY = "person_ready"


class ServiceUnreachable(Exception):
    """The service could not be asked. Distinct from an answer we did not
    like: "we could not look" and "we looked and it is broken" send somebody
    to two different places."""


def _get(path: str, api_key: str, timeout: int = 10):
    """One read against the service.

    Returns the parsed body, or raises `ServiceUnreachable`. A 404 comes back
    as None rather than an error, because "no such user" is an answer to the
    question we asked and one of the states we report.
    """
    request = urllib.request.Request(
        f"{BASE}{path}",
        headers={
            "Authorization": f"Key {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise ServiceUnreachable(
            f"The service answered {error.code}. {detail}".strip()
        ) from error
    except Exception as error:  # noqa: BLE001
        raise ServiceUnreachable(str(error)) from error

    try:
        return json.loads(body)
    except ValueError as error:
        raise ServiceUnreachable(
            "The service answered with something that was not JSON."
        ) from error


def fetch_app(app_id: str, api_key: str, timeout: int = 10):
    """The app's own configuration, which is where the Apple credential is."""
    return _get(f"/apps/{app_id}", api_key, timeout=timeout)


def fetch_person(app_id: str, api_key: str, external_id: str,
                 timeout: int = 10):
    """One person's record, looked up by the id the server uses for them.

    None means the service has never heard of them, which on its own is the
    answer: nothing this person's phone did ever reached it.
    """
    return _get(
        f"/apps/{app_id}/users/by/external_id/{external_id}",
        api_key, timeout=timeout,
    )


def apple_state(app: dict | None) -> str:
    """Whether the service can deliver to an iPhone for this app.

    A credential can be held two ways, a p8 key or a p12 certificate, and the
    fields differ. Either counts. Neither means no iPhone can be reached, no
    matter what any phone or any send receipt says.
    """
    if not isinstance(app, dict):
        return APPLE_UNKNOWN

    has_p8 = bool((app.get("apns_key_id") or "").strip())
    has_p12 = bool((app.get("apns_certificates") or "").strip())
    if not (has_p8 or has_p12):
        return APPLE_NO_CREDENTIAL

    # Our Apple registrations go in as production. A service pointed at
    # sandbox will take the token and drop every message, with the same
    # silence as having no credential at all and none of the same cause.
    if (app.get("apns_env") or "").strip().lower() == "sandbox":
        return APPLE_SANDBOX

    return APPLE_READY


def subscriptions(person: dict | None) -> list[dict]:
    """This person's push subscriptions, flattened for display.

    Email and SMS subscriptions live in the same list and are dropped: they
    are not why a phone is quiet, and leaving them in makes a screen somebody
    reads under pressure longer than it needs to be.
    """
    if not isinstance(person, dict):
        return []

    out = []
    for entry in person.get("subscriptions") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind not in PUSH_TYPES:
            continue

        token = (entry.get("token") or "").strip()
        out.append({
            "type": kind,
            "apple": kind in APPLE_PUSH,
            "safari": kind in SAFARI_PUSH,
            "enabled": bool(entry.get("enabled")),
            "has_token": bool(token),
            # Enough to tell two subscriptions apart and useless to anybody
            # who sees the screen. Never the whole token.
            "token_tail": token[-6:] if token else "",
            "device": (entry.get("device_model") or "").strip(),
            "os": (entry.get("device_os") or "").strip(),
            # Printed as given. The service documents this as "additional
            # details" without saying what the values mean, so inventing an
            # interpretation here would be guessing in the one place somebody
            # is relying on us not to.
            "status_detail": entry.get("notification_types"),
        })
    return out


def person_state(person: dict | None, subs: list[dict] | None = None) -> str:
    """What the service has for this person, as one answer."""
    if person is None:
        return PERSON_NOT_FOUND
    if not isinstance(person, dict):
        return PERSON_UNKNOWN

    subs = subscriptions(person) if subs is None else subs
    if not subs:
        return PERSON_NO_PUSH

    apple = [s for s in subs if s["apple"]]
    if not apple:
        return PERSON_NO_APPLE
    if not any(s["has_token"] for s in apple):
        return PERSON_APPLE_NO_TOKEN
    if not any(s["enabled"] and s["has_token"] for s in apple):
        return PERSON_APPLE_OFF

    return PERSON_READY


def diagnose(app_id: str, api_key: str, external_id: str | None,
             timeout: int = 10) -> dict:
    """Both questions, in one answer the template can walk.

    Either lookup failing is reported rather than raised. This runs because
    something is already broken, so a screen that says what it managed to
    find out beats a screen that says nothing because the second request
    timed out.
    """
    result = {
        "asked": True,
        "apple": APPLE_UNKNOWN,
        "person": PERSON_UNKNOWN,
        "subscriptions": [],
        "errors": [],
        "bundle_id": "",
        "env": "",
        "messageable": None,
    }

    if not app_id or not api_key:
        result["asked"] = False
        result["errors"].append(
            "The server has no notification service credentials configured, "
            "so there was nothing to ask."
        )
        return result

    try:
        app = fetch_app(app_id, api_key, timeout=timeout)
    except ServiceUnreachable as error:
        result["errors"].append(f"Could not read the app setup: {error}")
    else:
        if app is None:
            result["errors"].append(
                "The service does not recognise this app id."
            )
        else:
            result["apple"] = apple_state(app)
            result["bundle_id"] = (app.get("apns_bundle_id") or "").strip()
            result["env"] = (app.get("apns_env") or "").strip()
            messageable = app.get("messageable_players")
            if isinstance(messageable, int):
                result["messageable"] = messageable

    if not external_id:
        result["person"] = PERSON_NOT_FOUND
        return result

    try:
        person = fetch_person(app_id, api_key, external_id, timeout=timeout)
    except ServiceUnreachable as error:
        result["errors"].append(f"Could not read your own record: {error}")
        return result

    subs = subscriptions(person)
    result["subscriptions"] = subs
    result["person"] = person_state(person, subs)
    return result
