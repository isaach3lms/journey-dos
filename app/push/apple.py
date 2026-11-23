"""Registering an iPhone with the provider using Apple's own device token.

**Why this exists at all.** A church's iOS app can be built around Apple's
push plugin rather than the provider's SDK. The app then holds a real APNs
device token and is perfectly capable of receiving notifications, while the
provider has no record of the phone, because nothing in the app ever told it.
From the church's side every screen looks healthy: the account works, the
server has its keys, the device is known. Nobody is reachable.

That is not a hypothetical. A church ran an App Store app in exactly that
state and reached two people out of a hundred, and the two were artefacts of
an earlier build.

**The alternative was worse.** The obvious fix is to rebuild the app around
the provider's SDK, which means a Mac, a store review, and then every member
taking an update before any of them can be reached. Apple's token is the same
token the provider's SDK would have obtained, so handing it over directly
reaches an app that is already installed, today, with nothing for the member
to do but allow notifications.

**It registers, it does not send.** Delivery stays with the transport in
`transport.py`, addressed by external id exactly as before. All this does is
make sure the provider has a phone to address, which is the one link that was
missing. Nothing downstream changes.

**Tokens are not secrets, and are not stable.** A device token identifies one
install of one app on one phone and is useless without the certificate that
signs the push. It also changes: a reinstall, a restore from backup, or a
system update can issue a new one. So the app reports its token on every
launch and this runs again, which the provider handles by moving the
subscription rather than making a second one.
"""

from __future__ import annotations

import json
import re

from flask import current_app

# What Apple hands back, and the only shape the provider accepts: 64
# hexadecimal characters. Worth checking here rather than posting whatever
# arrived, because a phone that reports a token in another format is a phone
# whose notifications would silently never arrive, and the reason would sit in
# a third party's logs rather than anywhere a church could see it.
TOKEN = re.compile(r"\A[0-9a-f]{64}\Z")

ENDPOINT = "https://api.onesignal.com/apps/{app_id}/users"

# Which of Apple's two push servers issued this token. Sandbox tokens come
# from an Xcode or ad-hoc install and are rejected by the production server
# and vice versa, with the notification simply never arriving either way.
# App Store and TestFlight builds are both production, which is every member.
PRODUCTION = 0
SANDBOX = 1


class AppleRegistrationFailed(Exception):
    """The provider would not take this token, and the phone stays unreachable."""


def normalise(token: str) -> str | None:
    """Apple's token as the provider wants it, or None if it is not one.

    Apple's own APIs have historically returned this as `<32 bytes of hex>`
    with angle brackets and spaces, and the plugin layers on top of it have
    not always agreed on stripping them. Normalising here rather than trusting
    the shape means one phone with an older plugin does not go quietly
    unreachable.
    """
    if not token:
        return None

    tidy = re.sub(r"[^0-9a-fA-F]", "", str(token)).lower()
    return tidy if TOKEN.match(tidy) else None


def register(external_id: str, token: str, *, sandbox: bool = False,
             timeout: int = 10) -> str:
    """Tell the provider this person has this iPhone. Returns what happened.

    "created" for a person the provider had not seen, "updated" for one it
    had. Both are success: the endpoint takes the same call either way, which
    is what makes it safe to run on every launch.

    Raises `AppleRegistrationFailed` for anything else, including the conflict
    the provider reports when the external id is already bound to a different
    user there. That one cannot be fixed by retrying and wants a human.
    """
    import urllib.error
    import urllib.request

    if not external_id:
        raise AppleRegistrationFailed("No external id to register against.")

    tidy = normalise(token)
    if tidy is None:
        raise AppleRegistrationFailed(
            "That does not look like an Apple device token."
        )

    app_id = current_app.config.get("ONESIGNAL_APP_ID") or ""
    api_key = current_app.config.get("ONESIGNAL_API_KEY") or ""
    if not (app_id and api_key):
        raise AppleRegistrationFailed(
            "ONESIGNAL_APP_ID and ONESIGNAL_API_KEY must both be set."
        )

    subscription = {"type": "iOSPush", "token": tidy}
    if sandbox:
        # Omitted entirely for production rather than sent as 0, because that
        # is what their documentation describes as the default and the fewer
        # fields sent on the common path the fewer there are to be wrong.
        subscription["test_type"] = SANDBOX

    payload = json.dumps({
        "identity": {"external_id": external_id},
        "subscriptions": [subscription],
    }).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT.format(app_id=app_id),
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Key {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 409:
            raise AppleRegistrationFailed(
                f"This person is already registered under a different "
                f"identity: {detail}"
            ) from exc
        raise AppleRegistrationFailed(
            f"Registration refused ({exc.code}): {detail}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppleRegistrationFailed(f"Provider unreachable ({exc}).") from exc

    # 200 is a new user there, 202 an existing one whose subscriptions were
    # modified. Treating only 200 as success would make every launch after the
    # first look like a failure.
    return "created" if code == 200 else "updated"
