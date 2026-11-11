"""Sending a push notification.

Deliberately shaped like `app/mail/transport.py`, because push is a second
transport beside Resend rather than a second notification system. The outbox
already knows how to queue, retry, dedupe, and respect a category opt-out, and
none of that is worth rewriting because the delivery mechanism changed.

**A dead endpoint is not a failure to retry.** A push service answers 404 or
410 for a subscription that has been revoked, and it will answer that forever.
`SubscriptionGone` is raised for those so the caller deletes the row instead of
queueing another attempt.

**A payload is small and says little.** Push notifications appear on a lock
screen, which is a surface anybody standing nearby can read. A title and a
short line are enough to get somebody to open the app, which is the only thing
a notification needs to do. Nothing about giving amounts, nothing from a
private conversation, no child's name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


class PushFailed(Exception):
    """The push service refused, but the subscription may still be good."""


class SubscriptionGone(Exception):
    """The endpoint is revoked. Delete the row rather than retrying it."""


# The characters a lock screen has room for before it truncates. Writing to
# this rather than being truncated by it keeps the important part visible.
MAX_TITLE = 60
MAX_BODY = 120


@dataclass
class PushMessage:
    title: str
    body: str
    url: str = "/"
    tag: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title[:MAX_TITLE],
                "body": self.body[:MAX_BODY],
                "url": self.url,
                # A tag lets a second notification replace the first rather
                # than stacking. Three copies of "you are on the plan" is how
                # somebody turns notifications off.
                "tag": self.tag or "dos",
            }
        )


class PushTransport:
    name = "base"

    # Can this transport be handed a person, or does the caller have to fan
    # out over the device rows the system stored for them?
    #
    # Web push is the second kind: the server holds one endpoint per browser
    # and talks to each. A provider like OneSignal is the first: it holds the
    # devices, the server names the person, and there is nothing here to store
    # or purge. The two are different enough that pretending otherwise would
    # mean inventing a fake subscription row to pass to a provider that has no
    # use for it.
    addresses_people = False

    def send(self, subscription, message: PushMessage) -> None:
        raise NotImplementedError

    def send_to_person(self, external_id: str, message: PushMessage) -> None:
        """Only implemented by transports where `addresses_people` is True."""
        raise NotImplementedError


class WebPushTransport(PushTransport):
    """Real delivery, via VAPID and RFC 8291 payload encryption."""

    name = "webpush"

    def __init__(self, private_key: str, subject: str):
        if not private_key:
            raise ValueError(
                "VAPID_PRIVATE_KEY is empty. Refusing to build a transport "
                "that cannot send, because it would fail once per device "
                "instead of once at boot."
            )
        self.private_key = private_key
        self.subject = subject

    def send(self, subscription, message: PushMessage) -> None:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=subscription.as_payload(),
                data=message.to_json(),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                raise SubscriptionGone(f"Endpoint gone ({status}).") from exc
            raise PushFailed(f"Push refused ({status or exc}).") from exc
        except Exception as exc:  # noqa: BLE001
            raise PushFailed(f"Push failed ({exc}).") from exc


class OneSignalTransport(PushTransport):
    """Real delivery on iOS and Android, through OneSignal.

    This exists because the transport beside it cannot reach the app the
    church actually uses. Web Push on iOS works only for a site installed to
    the Home Screen from Safari; inside the wrapper the app ships as, Apple
    exposes no push at all. Reaching an iPhone means Apple's own push service,
    and reaching that needs a developer account, a signing key, and a server
    that speaks it. OneSignal is that server.

    **The server stores no devices.** Notifications are addressed to an
    external id the app sets when somebody signs in, so a phone being wiped,
    replaced, or added is the provider's problem rather than a table here that
    slowly fills with endpoints nobody can prove are dead.

    **The key is not the App ID.** The App ID identifies the application and is
    compiled into every copy of it; it is public by construction. The REST key
    is the credential that can send to the whole church and is read from the
    environment, never from a column, a template, or this file.
    """

    name = "onesignal"
    addresses_people = True

    ENDPOINT = "https://api.onesignal.com/notifications"

    def __init__(self, app_id: str, api_key: str, timeout: int = 10):
        if not app_id or not api_key:
            raise ValueError(
                "ONESIGNAL_APP_ID and ONESIGNAL_API_KEY must both be set. "
                "Refusing to build a transport that cannot send, because it "
                "would fail once per notification instead of once at boot."
            )
        self.app_id = app_id
        self.api_key = api_key
        self.timeout = timeout

    def send_to_person(self, external_id: str, message: PushMessage) -> None:
        import urllib.error
        import urllib.request

        if not external_id:
            raise PushFailed("No external id to send to.")

        payload = json.dumps({
            "app_id": self.app_id,
            "target_channel": "push",
            "include_aliases": {"external_id": [external_id]},
            "headings": {"en": message.title[:MAX_TITLE]},
            "contents": {"en": message.body[:MAX_BODY]},
            # Where tapping it lands. A path, resolved by the app against its
            # own origin, because a push payload is delivered by a third party
            # and is not the place to teach a phone to trust a hostname.
            "data": {"path": message.url, "tag": message.tag or "dos"},
            # Collapsing replaces an earlier notification rather than stacking
            # a second one. Three copies of "you are on the plan" is how
            # somebody turns notifications off.
            "collapse_id": (message.tag or "dos")[:64],
        }).encode("utf-8")

        request = urllib.request.Request(
            self.ENDPOINT,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Key {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            # 400 with no recipients is the ordinary case of somebody who has
            # the app but never allowed notifications. It is not a failure
            # worth recording against them, and it must never be retried.
            if exc.code == 400 and "no subscribers" in detail.lower():
                raise SubscriptionGone(detail) from exc
            raise PushFailed(f"OneSignal refused ({exc.code}): {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise PushFailed(f"OneSignal unreachable ({exc}).") from exc

        # A 200 with zero recipients means the same thing as the 400 above;
        # which one comes back has changed between versions of their API.
        try:
            parsed = json.loads(body)
        except ValueError:
            return
        if isinstance(parsed, dict) and parsed.get("errors"):
            raise PushFailed(f"OneSignal refused: {str(parsed['errors'])[:300]}")


@dataclass
class MemoryPushTransport(PushTransport):
    """Records what it was asked to send. Used by the tests.

    Answers to both shapes, so a test can drive either the device fan-out or
    the address-a-person path without a second fake.
    """

    name: str = "memory"
    sent: list = field(default_factory=list)
    fail_with: Exception | None = None
    addresses_people: bool = False

    def send(self, subscription, message: PushMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((subscription.endpoint, message))

    def send_to_person(self, external_id: str, message: PushMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((external_id, message))

    def reset(self) -> None:
        self.sent.clear()
        self.fail_with = None


class NullPushTransport(PushTransport):
    """Development default. Prints instead of sending."""

    name = "null"

    def send(self, subscription, message: PushMessage) -> None:
        import sys

        print(
            f"\n--- push ({self.name}) ---\n"
            f"To:    {subscription.label or subscription.endpoint[:40]}\n"
            f"Title: {message.title}\n"
            f"Body:  {message.body}\n"
            f"Opens: {message.url}\n--- end ---\n",
            file=sys.stderr,
        )


def build_push_transport(config) -> PushTransport:
    name = (config.get("PUSH_TRANSPORT") or "null").lower()
    if name == "onesignal":
        return OneSignalTransport(
            config.get("ONESIGNAL_APP_ID", ""),
            config.get("ONESIGNAL_API_KEY", ""),
        )
    if name == "webpush":
        return WebPushTransport(
            config.get("VAPID_PRIVATE_KEY", ""),
            config.get("VAPID_SUBJECT", "mailto:isaac@betweensundaysconsulting.com"),
        )
    if name == "memory":
        return MemoryPushTransport()
    return NullPushTransport()
