"""Resend over HTTPS. Port 587 SMTP is unreliable on Render."""

import json
import urllib.error
import urllib.request

from flask import current_app

RESEND_ENDPOINT = "https://api.resend.com/emails"


def send_email(to, subject, html, reply_to=None) -> bool:
    api_key = current_app.config.get("RESEND_API_KEY")
    if not api_key:
        current_app.logger.warning("RESEND_API_KEY missing, email not sent: %s", subject)
        return False

    payload = {
        "from": current_app.config["MAIL_FROM"],
        "to": to if isinstance(to, list) else [to],
        "subject": subject,
        "html": html,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    request = urllib.request.Request(
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        current_app.logger.error("Resend error %s: %s", exc.code, exc.read()[:400])
    except Exception as exc:  # network, DNS, timeout
        current_app.logger.error("Resend failed: %s", exc)
    return False


def notify_new_person(person, form_kind: str) -> None:
    rows = [
        ("Name", person.full_name),
        ("Email", person.email),
        ("Phone", person.phone or "not given"),
        ("Source", form_kind),
        ("Notes", person.notes or ""),
    ]
    body = "".join(
        f"<p style='margin:4px 0'><strong>{label}:</strong> {value}</p>" for label, value in rows
    )
    send_email(
        to=current_app.config["NOTIFY_TO"],
        subject=f"New {form_kind}: {person.full_name}",
        html=f"<div style='font-family:Inter,Arial,sans-serif'>{body}</div>",
        reply_to=person.email,
    )
