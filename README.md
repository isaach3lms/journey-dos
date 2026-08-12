# The Journey Church — Discipleship Operating System

The back end only. The church's public website is built and hosted elsewhere;
this app is the operating system behind it. Flask + SQLAlchemy + Postgres on
Render. Multi-tenant schema, single-tenant deployment.

Deploy it on its own host, `app.thejourneychurchsemo.com`, so the existing site
is untouched. Hitting the root of this host redirects to sign in, because this
host is not the website.

## What is in Phase 1

| Area | Status |
|---|---|
| Intake API at `/api/intake` for the existing website's forms | Built |
| Embeddable connect form at `/embed/connect` for an iframe | Built |
| Honeypot plus timing gate on both paths | Built |
| Every submission creates a Person and enters the journey | Built |
| Tithely giving, embedded on the public site and inside the member app | Built, needs the form URL |
| Member app: account claim, my journey, give. Installable PWA | Built |
| Staff dashboard: journey rail, stuck report, never contacted, people list, person detail | Built |
| Stage moves and contact logging with full history | Built |
| Automated follow up sequences, enrollment on form submit | Built |
| Weekly staff digest: who is stuck, who has never been contacted | Built |
| Tithely CSV import, matched to people, deduped by transaction id | Built |
| Staff giving view and per person giving history | Built |
| Manual person entry | Built |
| Test suite, 21 tests | Built |
| Services: run sheet with live times, volunteer scheduling, attendance counts | Built |
| Serving teams with background check tracking on kids teams | Built |
| Kids check in kiosk with security codes | Built |
| Groups: staff management, self serve join and leave in the member app | Built |
| Messages: audience targeted announcements, app plus optional email | Built |
| Account security: emailed single use links, no password set by form | Built |
| Password reset, rate limiting on login and intake | Built |
| Test suite, 49 tests | Built |
| Native giving form inside the app, live Tithely sync | Next, needs Tithely API keys |
| SMS | Not built. Needs a Twilio or similar account and a 10DLC registration |

## Local run

```bash
cd ~/"coding files/journey-dos"
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in SECRET_KEY at minimum
python devseed.py             # creates journey.db with demo people
python wsgi.py
```

Open http://127.0.0.1:5000. Staff sign in at `/account/login` with
`admin@example.com` / `journey1234`. Delete `devseed.py` before the first
production deploy or leave it, it is never wired into the app.

## Connecting the existing website

The DOS needs people in it. There are three ways to get them there, and you
should pick one and only one per form so the same person does not arrive twice.

### Option 1: the site relays server side (preferred, and what Journey uses)

The Journey Church public site is Flask. That means the relay happens in its
form handler, not in the browser, which removes every downside of the other
options: the token stays in the site's environment, there is no CORS, no iframe,
and no cross site cookie problem.

Copy `integrations/dos_relay.py` into the public site next to `app.py`, set two
environment variables on that site in Render, and add one line to each form
handler:

```python
from dos_relay import send_to_dos, LAUNCH_TEAM

@app.route("/launch-team", methods=["GET", "POST"])
def launch_team():
    if request.method == "POST":
        # ... existing honeypot, timing gate, and validation ...
        send_to_dos(request.form, LAUNCH_TEAM)
        return redirect(url_for("thanks"))
    return render_template("launch_team.html")
```

`dos_relay.py` normalizes field names, so it works whether the site's form calls
it `first_name`, `fname`, or a single `name`. It never raises and never blocks:
if the DOS is down or mid deploy, the visitor still gets the thank you page.

### Who sends what

The DOS owns intake email. The public site sends nothing on submit. This is
deliberate: the DOS holds the record, decides the stage, and runs the follow up,
so it is the only thing that knows what to say and to whom.

| Email | Sent by | When |
|---|---|---|
| Staff alert, with a link to the record | DOS | Immediately on submit |
| Visitor confirmation, day zero of the sequence | DOS | Immediately on submit |
| Day 3 and day 10 follow up | DOS | Nightly cron |
| "Unrecorded submission, add manually" | Public site | Only when the relay fails |

The day zero step is sent inline at enrollment rather than by the cron. Waiting
for the nightly run would mean a visitor fills out a connect card and hears
nothing for up to a day, which reads as a broken form.

The last row is the safety net. Since the site no longer emails on every
submission, a failed relay would otherwise mean the submission is gone. On
failure only, `dos_relay.py` emails the raw submission to `NOTIFY_TO` so staff
can enter the person by hand. Nobody gets two emails in normal operation.

### Removing the site's own notification

On the public site, in each form handler:

1. Delete the Resend call that emails staff on submit.
2. Delete the confirmation email to the submitter. The DOS day zero step
   replaces it, and the sequence copy lives in `app/sequences.py` here.
3. Keep the honeypot, keep the timing gate, keep the thank you page. The relay
   runs after that validation, so bots never reach the DOS.
4. Keep `RESEND_API_KEY`, `MAIL_FROM`, and `NOTIFY_TO` set on the site. They are
   now only used by the fallback, and removing them turns the safety net off.
5. Set `NOTIFY_TO` on the DOS to whoever should receive intake alerts.

Environment variables on the **public site**:

```
DOS_INTAKE_URL=https://app.thejourneychurchsemo.com/api/intake
DOS_INTAKE_TOKEN=<the INTAKE_TOKEN value from the DOS dashboard>
```

### Option 1b: a non Flask site posts from the browser

If the site cannot post server side, give its developer the `INTAKE_TOKEN` and
this snippet. The token is visible in page source in this case, which is why the
honeypot and timing gate still matter.

```html
<form id="connect-form">
  <input name="first_name" placeholder="First name" required>
  <input name="last_name" placeholder="Last name">
  <input name="email" type="email" placeholder="Email" required>
  <input name="phone" type="tel" placeholder="Phone">
  <textarea name="message" placeholder="Anything you want us to know"></textarea>
  <button type="submit">Send it</button>
</form>

<script>
document.getElementById("connect-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.form = "connect card"; // or "launch team", "serve interest", "prayer request"
  const response = await fetch("https://app.thejourneychurchsemo.com/api/intake", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Intake-Token": "PASTE_TOKEN_HERE" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (result.ok) { window.location.href = "/thank-you"; }
});
</script>
```

Accepted fields: `first_name`, `last_name`, `email`, `phone`, `message`, `form`.
A single `name` field is accepted and split on the first space. `email` and a
name are required; everything else is optional.

The `form` value routes the submission to the right stage and the right
automated sequence:

| `form` | Lands in stage | Sequence |
|---|---|---|
| `connect card` | Interested | New connect card welcome |
| `launch team` | Launch team | Launch team onboarding |
| `serve interest` | Connected | none |
| `prayer request` | Interested | New connect card welcome |

Re-submitting the same email updates that person rather than creating a second
record, and never moves anyone backward on the journey.

Note that the token ships in the page source if the post happens in the browser.
It stops drive-by posting, not a determined attacker. That is what the honeypot
and timing gate are for. If the site's platform can post server side, send the
token as the `X-Intake-Token` header from there instead.

### Option 2: iframe the hosted form

Zero code beyond one tag. Useful when the site's platform will not let them add
JavaScript.

```html
<iframe src="https://app.thejourneychurchsemo.com/embed/connect?form=launch%20team"
        style="width:100%;border:0;min-height:620px" title="Get connected"></iframe>
```

The embedded form has a transparent background and uses the Journey palette and
typefaces, so it inherits the host page.

Two things are deliberate in the embed and should not be "fixed" later:

- CSRF protection is off on `/embed/connect`. In a cross site iframe the
  session cookie is not sent, so a CSRF token could never validate. The honeypot
  and the timing gate carry the load, and the worst case for a forged post is a
  junk person record rather than a state change on an account.
- Only `PUBLIC_SITE_URL` may frame it. `/embed/*` responds with
  `Content-Security-Policy: frame-ancestors`, and every other route in the DOS
  responds with `X-Frame-Options: DENY`.

### Option 3: staff enter people by hand

Staff > People > Add a person. Always available, and the right answer for
someone met in a room rather than on a form.

## Automations

Sequences live in `app/sequences.py` as plain data. Editing a sequence is
editing that file. Enrollment happens the moment a form is submitted, based on
where the person came from.

Two rules keep this safe for a church:

1. A sequence stops the moment a staff member logs a real contact. Automation
   never talks over a pastor.
2. A sequence stops when the person reaches the stage it was moving them toward.

Run manually while testing:

```bash
flask --app wsgi run-automations
flask --app wsgi send-digest
```

In production, `render.yaml` provisions two cron services: follow up daily at
9:00 am Central, staff digest Monday at 8:00 am Central. The runner is
idempotent, so a double run sends nothing twice.

## Tests

```bash
python -m pytest
```

21 tests covering the stuck calculation, stage history, both automation stop
rules, the delay schedule, honeypot and timing gates, CSV parsing and dedupe,
and staff route access control.

## Accounts

A person record existing in the database is not proof that whoever is typing is
that person. Church member emails are guessable, so the DOS never lets anyone
set a password by submitting an email address to a form.

- `/account/claim` asks for an email and sends a single use link to that
  address. It shows the same neutral message either way, so the form cannot be
  used to find out who attends this church.
- The link expires in 48 hours, works exactly once, and requesting a new one
  retires the old one.
- Only the SHA-256 hash of each token is stored. If the database leaks, the
  links in it are already dead.
- A GET on the link does not spend it, so a mail client that prefetches URLs
  cannot burn someone's only chance to set a password.
- The same route handles forgotten passwords. There is no separate flow.
- Staff can trigger a link from Staff > People > the person, under Member app
  access. It always goes to the person's email, never to the screen.

Rate limits, per IP: 10 login attempts per 15 minutes, 6 link requests per 15
minutes, 30 intake submissions per hour. The limiter is in process, which is
honest for a single Render instance. If this ever scales past one web instance,
move it to the database or Redis.

## Kids check in

The kiosk is at `/kiosk` and is deliberately not behind a staff login. A
volunteer tablet should never hold an admin session. It unlocks with
`KIOSK_PIN`, held in that device's session until someone hits `/kiosk/lock`.

How it works on a Sunday:

1. A parent types the last four digits of their phone.
2. They tap which children are here and check in.
3. The screen shows a three digit code. Same code for every child in that
   household, so a parent with three kids carries one code, not three.
4. At pickup, the code must match or the child is not released. Staff can
   override from Staff > Kids, and the override is recorded as an override.

Allergies show on the check in screen in red before anyone taps anything.
Volunteers on a team marked `requires_clearance` show as uncleared on the Kids
page until a background check date is recorded.

Set a real `KIOSK_PIN` before launch and change it whenever a volunteer leaves.

## Services

Creating a service seeds a standard seven item run sheet so nobody starts from a
blank page on a Saturday night. Clock times are computed from the start time on
every render, so reordering one item re-times the whole service. Nothing is
stored as a fixed clock time.

Scheduling someone to serve moves them to the Serving stage on the journey
automatically. Volunteers confirm or decline in the member app under Serving.

## Messages

Audience targeted announcements: everyone, launch team and beyond, people on a
serving team, or people in a group. Posts appear in the member app immediately.
Email is a separate checkbox, because not everything is worth an email.

This is broadcast, not chat. Internal staff chat is a solved problem your church
already has in a group text or Slack, and building a second inbox nobody checks
is how church software gets abandoned.

## Tithely wiring

1. Tithely > Giving > Giving Form. Copy the unique Giving Form URL.
2. Set `TITHELY_GIVE_URL` to that URL. The give page and the member app both read it.
3. Style the form inside Tithely so it matches the forest and gold palette before launch.

The iframe processes gifts but returns no donor data. Until Tithely issues API
keys, giving comes into the DOS through Staff > Giving > Import from Tithely.
Export from Tithely > Giving > Transactions with a date range applied, then
upload the CSV. Gifts match to people by email and dedupe on transaction id, so
re-importing an overlapping range is safe.

Request API access at support@tithe.ly. The endpoints that replace the manual
import are list all charges and list all recurring charges.

## Render deploy

```bash
cd ~/"coding files/journey-dos"
git init
git add -A
git commit -m "Journey Church DOS phase 1"
gh repo create journey-dos --private --source=. --remote=origin --push
```

Then in Render: New > Blueprint > select the repo. `render.yaml` provisions the
web service and the Postgres database. Set these in the Render dashboard:

- `TITHELY_GIVE_URL`
- `RESEND_API_KEY`
- `NOTIFY_TO`
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` for the first login, then delete both

After the first deploy, open the Render shell and run:

```bash
flask --app wsgi init-db
```

DNS at the registrar: `thejourneychurchsemo.com` A record and `www` CNAME to the
Render targets. Do not touch MX, DKIM, or SPF records.

## Standing up the next church

1. Copy the repo.
2. Replace `app/brand.py` with the new palette, type, and mission.
3. Replace `app/content.py` with their copy.
4. Drop the new logo at `app/static/img/` and regenerate the PWA icons.
5. Edit the `STAGES` list in `app/seed.py` to match their discipleship path.
6. Deploy. No template or CSS edits required.

Every color and typeface in the CSS reads from `brand.py` through CSS variables.
That is the templating lever. Keep it that way.
