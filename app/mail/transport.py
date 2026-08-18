"""Sending email.

Resend over HTTPS on port 443, never SMTP. Port 587 is blocked outbound on
Render and on most managed hosts, and discovering that at deploy time after
building against SMTP is a rewrite rather than a config change.

Three transports behind one interface:

- `ResendTransport` in production.
- `ConsoleTransport` in development, which prints the message and pretends to
  succeed, so the whole outbox path can be exercised without a real API key or
  a real person receiving test mail.
- `MemoryTransport` in tests, which records what it was asked to send and can
  be told to fail on demand. Retry and failure handling need a way to fail that
  does not involve the network.

`send` returns a provider message id on success and raises `SendFailed` on
failure. It never swallows an error, because the outbox is what decides
whether something is retried, and it can only decide that if it is told.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_TIMEOUT = 15


class SendFailed(Exception):
    """The provider did not accept the message.

    `permanent` distinguishes "this address does not exist" from "the API was
    briefly down". Retrying the first forever is how a sending reputation is
    destroyed; not retrying the second loses mail for no reason.
    """

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class SentMessage:
    to_email: str
    subject: str
    body_text: str
    category: str
    provider_message_id: str


class Transport:
    name = "base"

    def send(self, *, to_email, to_name, subject, body_text, body_html, from_address):
        raise NotImplementedError


class ResendTransport(Transport):
    name = "resend"

    def __init__(self, api_key: str, timeout: int = DEFAULT_TIMEOUT):
        if not api_key:
            raise ValueError(
                "RESEND_API_KEY is empty. Refusing to construct a transport "
                "that cannot send, because it would fail once per message "
                "instead of once at boot."
            )
        self.api_key = api_key
        self.timeout = timeout

    def send(self, *, to_email, to_name, subject, body_text, body_html, from_address):
        recipient = f"{to_name} <{to_email}>" if to_name else to_email
        payload = {
            "from": from_address,
            "to": [recipient],
            "subject": subject,
            "text": body_text,
        }
        if body_html:
            payload["html"] = body_html

        request = urllib.request.Request(
            RESEND_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
                return body.get("id") or "accepted"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            # 4xx other than 429 means the request itself is wrong. Sending it
            # again unchanged will fail again.
            permanent = 400 <= exc.code < 500 and exc.code != 429
            raise SendFailed(f"HTTP {exc.code}: {detail}", permanent=permanent) from exc
        except urllib.error.URLError as exc:
            raise SendFailed(f"Network error: {exc.reason}", permanent=False) from exc
        except Exception as exc:  # noqa: BLE001
            raise SendFailed(f"Unexpected: {exc}", permanent=False) from exc


class ConsoleTransport(Transport):
    """Prints instead of sending. The default in development."""

    name = "console"

    def send(self, *, to_email, to_name, subject, body_text, body_html, from_address):
        import sys

        print(
            f"\n--- outbox ({self.name}) ---\n"
            f"From:    {from_address}\n"
            f"To:      {to_name + ' <' + to_email + '>' if to_name else to_email}\n"
            f"Subject: {subject}\n\n{body_text}\n"
            f"--- end ---\n",
            file=sys.stderr,
        )
        return "console"


@dataclass
class MemoryTransport(Transport):
    """Records what it was asked to send. Used by the tests."""

    name: str = "memory"
    sent: list = field(default_factory=list)
    fail_with: SendFailed | None = None

    def send(self, *, to_email, to_name, subject, body_text, body_html, from_address):
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(
            SentMessage(
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                category="",
                provider_message_id=f"mem-{len(self.sent) + 1}",
            )
        )
        return self.sent[-1].provider_message_id

    def reset(self) -> None:
        self.sent.clear()
        self.fail_with = None


def build_transport(config) -> Transport:
    """Choose a transport from configuration.

    Production with no API key is a hard failure rather than a silent fallback
    to the console. A church that thinks it sent a welcome email and did not is
    worse off than one whose deploy refused to start.
    """
    name = (config.get("MAIL_TRANSPORT") or "console").lower()

    if name == "resend":
        return ResendTransport(
            config.get("RESEND_API_KEY", ""),
            timeout=config.get("MAIL_TIMEOUT", DEFAULT_TIMEOUT),
        )
    if name == "memory":
        return MemoryTransport()
    return ConsoleTransport()
