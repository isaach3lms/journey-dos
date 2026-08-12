"""
dos_relay.py

Drop this file into the existing Journey Church Flask site, next to app.py.

It posts form submissions from the public site into the Discipleship Operating
System, server side. Server side matters: the intake token stays in the site's
environment and never appears in page source, and there is no CORS, no iframe,
and no cross site cookie problem to work around.

Two environment variables on the public site:

    DOS_INTAKE_URL=https://app.thejourneychurchsemo.com/api/intake
    DOS_INTAKE_TOKEN=<the INTAKE_TOKEN value from the DOS Render dashboard>

Wire it into an existing form handler in one line:

    from dos_relay import send_to_dos

    @app.route("/connect", methods=["GET", "POST"])
    def connect():
        if request.method == "POST":
            # ... existing honeypot, timing gate, and validation ...
            send_to_dos(request.form, form="connect card")
            return redirect(url_for("thanks"))
        return render_template("connect.html")

Design decision: this never raises and never blocks. If the DOS is down, mid
deploy, or misconfigured, the visitor still sees the thank you page. A form that
throws a 500 at a first time guest is not acceptable.

Because the site no longer emails on every submission, a failed relay would
otherwise mean the submission is simply gone. So a failed relay falls back to
emailing the raw submission to NOTIFY_TO through Resend. That path only fires
when the DOS could not be reached, so nobody gets two emails in normal
operation, and nothing is lost in abnormal operation.

Optional environment variables for the fallback, which the site already has if
it was sending its own alerts:

    RESEND_API_KEY=...
    MAIL_FROM=website@thejourneychurchsemo.com
    NOTIFY_TO=hello@thejourneychurchsemo.com
"""

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 4

# Which DOS stage and follow up sequence a form maps to. Use these exact
# strings; anything unrecognized is treated as a connect card.
CONNECT_CARD = "connect card"
LAUNCH_TEAM = "launch team"
SERVE_INTEREST = "serve interest"
PRAYER_REQUEST = "prayer request"

FIELD_MAP = {
    # DOS field: the field names this site might use for it
    "first_name": ("first_name", "firstname", "fname", "first"),
    "last_name": ("last_name", "lastname", "lname", "last"),
    "name": ("name", "full_name", "fullname"),
    "email": ("email", "email_address"),
    "phone": ("phone", "phone_number", "tel"),
    "message": ("message", "notes", "comments", "prayer", "request"),
}


def _pull(form, candidates):
    for key in candidates:
        value = (form.get(key) or "").strip()
        if value:
            return value
    return ""


def build_payload(form, form_kind=CONNECT_CARD) -> dict:
    """Normalize whatever the site's form calls its fields into what the DOS
    expects. Exposed separately so it can be unit tested without a network."""
    payload = {key: _pull(form, names) for key, names in FIELD_MAP.items()}
    payload = {key: value for key, value in payload.items() if value}
    payload["form"] = form_kind
    # The DOS splits a single name on the first space, so send whichever the
    # site actually collected rather than guessing.
    if "first_name" in payload:
        payload.pop("name", None)
    return payload


def _fallback_email(payload, form_kind: str) -> bool:
    """Last resort when the DOS cannot be reached. Plain, ugly, and reliable.
    Staff can retype the person into the DOS from this email."""
    api_key = os.environ.get("RESEND_API_KEY")
    notify_to = os.environ.get("NOTIFY_TO")
    mail_from = os.environ.get("MAIL_FROM")
    if not (api_key and notify_to and mail_from):
        log.error("DOS relay failed and no fallback email configured: %s", payload)
        return False

    rows = "".join(
        f"<p style='margin:4px 0'><strong>{key}:</strong> {value}</p>"
        for key, value in payload.items()
    )
    body = (
        "<div style='font-family:Arial,sans-serif'>"
        "<p><strong>The DOS could not be reached, so this submission was not "
        "recorded automatically.</strong> Add this person manually at "
        "Staff &gt; People &gt; Add a person.</p>"
        f"{rows}</div>"
    )
    request_object = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(
            {
                "from": mail_from,
                "to": [notify_to],
                "subject": f"ACTION NEEDED, unrecorded {form_kind}: {payload.get('email')}",
                "html": body,
                "reply_to": payload.get("email"),
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_object, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        log.error("DOS relay fallback email failed too: %s", exc)
        return False


def send_to_dos(form, form_kind=CONNECT_CARD) -> bool:
    """Relay one submission. Returns True on success, False on any failure.
    Never raises. On failure, emails the submission so it is not lost."""
    payload = build_payload(form, form_kind)
    if not payload.get("email") or not (payload.get("first_name") or payload.get("name")):
        log.info("DOS relay skipped, no name or email in submission")
        return False

    url = os.environ.get("DOS_INTAKE_URL")
    token = os.environ.get("DOS_INTAKE_TOKEN")
    if not url or not token:
        log.warning("DOS relay not configured")
        _fallback_email(payload, form_kind)
        return False

    request_object = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Intake-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_object, timeout=TIMEOUT_SECONDS) as response:
            if 200 <= response.status < 300:
                return True
            log.error("DOS relay returned %s", response.status)
    except urllib.error.HTTPError as exc:
        log.error("DOS relay HTTP %s: %s", exc.code, exc.read()[:300])
    except Exception as exc:  # timeout, DNS, connection refused
        log.error("DOS relay failed: %s", exc)

    _fallback_email(payload, form_kind)
    return False
