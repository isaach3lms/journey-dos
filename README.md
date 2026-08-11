# The Journey Church — Discipleship Operating System

First live DOS build. Flask + SQLAlchemy + Postgres on Render. Multi-tenant schema,
single-tenant deployment.

## What is in Phase 1

| Area | Status |
|---|---|
| Public site (home, about, launch team, connect, give, 404) | Built |
| Connect card and launch team intake, honeypot plus timing gate | Built |
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
| Native giving form inside the app, live Tithely sync | Phase 3, needs Tithely API keys |
| Services, kids check-in, groups | Phase 4, not needed before launch |

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
