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

    def send(self, subscription, message: PushMessage) -> None:
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


@dataclass
class MemoryPushTransport(PushTransport):
    """Records what it was asked to send. Used by the tests."""

    name: str = "memory"
    sent: list = field(default_factory=list)
    fail_with: Exception | None = None

    def send(self, subscription, message: PushMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((subscription.endpoint, message))

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
    if name == "webpush":
        return WebPushTransport(
            config.get("VAPID_PRIVATE_KEY", ""),
            config.get("VAPID_SUBJECT", "mailto:isaac@betweensundaysconsulting.com"),
        )
    if name == "memory":
        return MemoryPushTransport()
    return NullPushTransport()
