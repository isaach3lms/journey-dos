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


def notify_new_person(person, form_kind: str, created: bool = True) -> None:
    """The staff alert for a new submission.

    The DOS is the only thing that emails on intake now, so this has to carry
    what the public site's old alert carried and more: who they are, how they
    came in, where they landed on the journey, and a direct link to the record.
    Reply-To is the person, so hitting reply in the inbox starts the follow up.
    """
    from flask import url_for

    site_url = (current_app.config.get("SITE_URL") or "").rstrip("/")
    try:
        record_link = url_for("staff.person", person_id=person.id, _external=True)
    except RuntimeError:  # outside a request, for example from a cron job
        record_link = f"{site_url}/staff/people/{person.id}" if site_url else ""

    headline = "New" if created else "Returning"
    stage = person.stage.name if person.stage else "not staged"
    rows = [
        ("Name", person.full_name),
        ("Email", f"<a href='mailto:{person.email}'>{person.email}</a>"),
        ("Phone", person.phone or "not given"),
        ("Came in through", form_kind),
        ("Now at stage", stage),
    ]
    body = "".join(
        f"<p style='margin:6px 0'><strong>{label}:</strong> {value}</p>"
        for label, value in rows
    )
    if person.notes:
        body += (
            "<p style='margin:14px 0 4px'><strong>What they wrote</strong></p>"
            f"<p style='margin:0;padding:12px 14px;background:#F2F0E7;border-radius:8px;"
            f"white-space:pre-wrap'>{person.notes}</p>"
        )
    if record_link:
        body += (
            f"<p style='margin:18px 0 0'><a href='{record_link}' "
            f"style='background:#F6C14B;color:#2F3E24;font-weight:700;"
            f"text-decoration:none;padding:12px 22px;border-radius:999px;"
            f"display:inline-block'>Open their record</a></p>"
        )
    body += (
        "<p style='margin:18px 0 0;font-size:13px;color:#666'>"
        "Reply to this email to write to them directly. Automated follow up is "
        "already running and stops the moment you log a real contact.</p>"
    )

    send_email(
        to=current_app.config["NOTIFY_TO"],
        subject=f"{headline} {form_kind}: {person.full_name}",
        html=(
            "<div style='font-family:Inter,Arial,sans-serif;max-width:560px;"
            f"color:#1A1A1A'>{body}</div>"
        ),
        reply_to=person.email,
    )
