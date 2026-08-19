# journey-dos

Discipleship Operating System. Multi-tenant Flask application, built by
Between Sundays, first tenant The Journey Church, Jackson MO.

**Status: increments 0 through 5 complete.** Foundation, tenancy, identity,
roles, the roster, the stuck engine, the outbox, and the member app. 291 tests
passing. Dashboard, People, and the member app are real screens; the remaining
nav items resolve to placeholders naming the increment they arrive in.

---

## Run it locally

Python 3.12 is what production runs. Check yours with `python3 -V` before
starting; anything older than 3.10 will behave differently from Render.

```bash
cd ~/"coding files/journey-dos"
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into .env

export FLASK_APP=wsgi.py FLASK_ENV=development
flask db upgrade
flask seed-tenants
flask build-error-pages

# Three logins, one per role. Passwords are prompted, never typed as arguments.
flask create-user --church journey --email pastor@thejourneychurchsemo.com --name "Pastor Reed" --role staff
flask create-user --church journey --email dana@thejourneychurchsemo.com --name "Dana Webb" --role leader
flask create-user --church journey --email alicia@thejourneychurchsemo.com --name "Alicia Romero" --role member

# Journey's roster: 54 people across 28 households.
flask import-people --church journey --file sample-data/journey-roster.csv

flask run
```

Then open `http://127.0.0.1:5000/?tenant=journey` and sign in as each of the
three in turn. The navigation changes. The staff account sees eight items, the
leader sees five, the member sees three. Then, as the member, type `/giving/`
into the address bar: the route refuses it with a 403, because hiding a link is
presentation and the page checks the role again before it renders.

Switch tenants to see the branding change from the same code:
`http://127.0.0.1:5000/?tenant=riverbend`.

`localhost` has no subdomain, so development uses the `?tenant=` override.
Production does not; see below.

Run the tests:

```bash
python -m pytest
```

---

## Commands

| Command | What it does |
|---|---|
| `flask db upgrade` | Apply migrations. Run before every deploy. |
| `flask db migrate -m "..."` | Generate a migration after a model change. Read it before committing. |
| `flask seed-tenants` | Insert or update the churches in `app/cli.py`. Idempotent. |
| `flask add-church --slug x --name "X" --accent "#485B38"` | Add one church. No migration. |
| `flask list-churches` | Show every tenant and its branding. |
| `flask build-error-pages` | Regenerate `app/static/500.html` from the brand tokens. |
| `flask check-contrast` | Report every palette against the 4.5:1 floor. |
| `flask create-user --church x --email y --name "Z" --role staff` | Create a login. Password prompted. |
| `flask set-password --church x --email y` | Reset a password. This is the reset path until increment 4. |
| `flask list-users [--church x]` | Every account, its role, and its last sign-in. |
| `flask unlock-user --church x --email y` | Clear a lockout without changing the password. |
| `flask set-domain --church x --domain host` | Point a hostname at a church. |
| `flask routing-check` | Show which hosts resolve to which church. |
| `flask import-people --church x --file roster.csv` | Import a roster. Add `--dry-run` first. |
| `flask people-summary --church x` | Stage counts, the same numbers the rail shows. |
| `flask stuck --church x` | Who is flagged and why. The same answer the dashboard shows. |
| `flask recompute-contact [--church x]` | Rebuild `last_contact_at` from the contact log. |
| `flask send-outbox [--church x]` | Send what is queued. This is the worker. |
| `flask outbox-status [--church x]` | What is in the outbox, by status. |
| `flask release-claims --minutes 15` | Return rows claimed by a worker that died. |
| `flask link-users [--church x]` | Attach logins to roster records by email. |
| `flask assign-pins --church x` | Give every household a check-in PIN. |
| `flask rotate-pin --church x --household "Name"` | Rotate one household's PIN. |

---

## Architecture rules, and where each one is enforced

These are not conventions. Each one has a test that fails the build.

| Rule | Enforced by |
|---|---|
| `church_id` on every table. Adding a church is a row. | `TenantScoped` in `app/models/base.py`; `test_models.py` |
| Brand tokens are the sole theming lever. No color in any template. | `test_brand.py::TestTemplatesCarryNoColors` walks every template and the stylesheet |
| `postgres://` normalized at boot | `app/config.py`; `test_config.py` |
| Aware UTC on both SQLite and Postgres | `UTCDateTime`; `test_models.py::TestUTCDateTime` |
| Production hard-fails without `DATABASE_URL` | `ProductionConfig.init_app`; `test_config.py` |
| The 500 page never touches the database | `app/errors.py` serves a static file; `test_errors.py` |
| Content lives in Python, not markup | `app/content.py` |
| A session from one church is refused at another | `User.get_id`, `load_user`; `test_auth.py::TestCrossTenantIsolation` |
| A hidden nav link is not a permission | Route-level role check; `test_auth.py::test_hiding_a_link_is_not_the_enforcement` |
| The roadmap card cannot claim an unbuilt increment is shipped | `SHIPPED_INCREMENTS`; `test_shell.py::TestRoadmapHonesty` |
| No person is ever loaded by primary key alone | `Person.get_for_church`; `test_people.py::TestTenantIsolation` |
| An import writes all rows or none | `flask import-people`; `test_people.py::TestImport` |

### Three rules that earned their tests the hard way

**Jinja escapes the font stack.** `--font-body:'Inter',...` becomes
`&#39;Inter&#39;` under autoescape, the declaration is invalid, and every
typeface silently falls back to a serif. The page still returns 200 and the
tests still pass. `base.html` uses `|safe`, and `brand.py` sanitizes every
value first, which is what makes `|safe` safe. `accent_hex` is a database
column a staff member can edit, so it is treated as untrusted input.

**Alembic writes application imports into migrations.** Autogenerate emitted
`app.models.base.UTCDateTime(...)`, and the migration died with a `NameError`
because migrations do not import application code. `render_migration_item`
renders it as `sa.DateTime(timezone=True)` instead. Identical DDL, and the
migration still runs if that module is ever refactored.

**`Mapped[str | None]` is evaluated at import, not deferred.** SQLAlchemy
reads the annotation inside `Mapped[...]` at class-definition time, so
`from __future__ import annotations` does not defer it the way it defers
ordinary function annotations. On Python 3.9 that raises "Could not resolve all
types within mapped annotation". Every mapped column now uses
`Optional[...]`, which resolves on any version, so the models do not depend on
which interpreter happens to be on the machine.

**A test harness that holds one app context proves nothing about sessions.**
The first version of `TestCrossTenantIsolation` passed when it should have
failed. The `app` fixture kept a single application context open for the whole
test, and Flask-Login caches the signed-in user on `g._login_user`, so that
cache survived from one request to the next and the user loader was never
consulted at all. The defense was correct the entire time; the test was
measuring nothing. The fixture now releases the context so each request builds
its own, exactly as in production. A passing suite is not evidence unless the
harness resembles the thing it claims to test.

---

## Identity and roles

Three roles, ordered: `member`, `leader`, `staff`. `at_least("leader")` answers
whether a role reaches another. The database rejects any value outside the
three, so a typo in a script cannot invent a fourth.

Email is unique **per church**, not globally. A person can attend two churches,
and a Between Sundays staff member may hold an account at several. Login always
happens inside an already-resolved tenant, so the scoped constraint is both
correct and invisible.

### Cross-tenant sessions: three defenses, all required

Flask-Login hands the user loader whatever `get_id()` put in the cookie and
nothing else. The single-tenant pattern, a primary key lookup, is a
cross-tenant session replay here.

1. `User.get_id()` returns `church_id:user_id`, so a mismatch is detectable.
2. `load_user` compares that church id to the host-resolved church and returns
   `None` on any disagreement.
3. `SESSION_COOKIE_DOMAIN` is never set, so the browser scopes the cookie to
   the exact issuing host. `assert_cookie_scope_is_safe` fails the boot if it
   ever appears.

Remove any one and the other two still hold. That is deliberate.

### Smaller decisions

- **One failure message for every failure.** Unknown address, wrong password,
  and deactivated account return identical text and status, and a miss still
  runs a hash against a decoy so timing cannot be used either. A form that
  distinguishes them tells an outsider who attends the church, which in a
  60-person congregation is a real disclosure.
- **Lockout after 10 failed attempts, 15 minutes.** Short, because there is no
  self-serve reset until the outbox ships at increment 4.
- **Logout is POST only.** A GET logout fires from any image tag on any page.
- **`?next=` is validated.** Anything with a scheme or a host is discarded, or
  the login page becomes an open redirect.
- **Email validation is syntax only.** `check_deliverability` does a live DNS
  lookup on every submit, making the login form exactly as fast and as
  available as the resolver.
- **`User` is not `Person`.** Increment 2 introduces `Person` and the nullable
  `person_id` that joins them. A secretary who logs in daily may never be
  someone the stuck engine should flag, and a guest with no login still needs a
  full pastoral record from the moment they fill out a connect card.

---

## Tenant resolution

Order, first match wins, in `app/tenancy.py`:

1. **Custom domain.** A row's `custom_domain` matches the host exactly.
2. **Platform subdomain.** `journey.<PLATFORM_DOMAIN>` resolves slug `journey`.
   Reserved labels (`www`, `app`, `api`, `admin`) never resolve a church.
3. **Query override**, `?tenant=journey`. Development and tests only.
4. **`DEFAULT_TENANT_SLUG`**. Development only.

Anything unresolved in production returns 404. It fails closed on purpose:
serving one church's roster to another because a host was misconfigured is the
worst failure this system can have.

### The domain decision, still open

Option 2 needs a `PLATFORM_DOMAIN` you own. Nothing is blocked meanwhile,
because a tenant can be reached by custom domain today, and locally by the
query override.

| Approach | Cost | Tradeoff |
|---|---|---|
| Platform domain plus wildcard DNS | ~$12/yr, plus a Render plan that issues wildcard certs | Clean, scales to every future client, matches the spec |
| One service per church | $7/mo each | Breaks "adding a church is a row" |
| Path prefix, `/journey/` | $0 | Works now, changes every URL later, reads as a shared app to a pastor |


---

## People, households, and stages

### Stages are Python, not rows

`app/stages.py` holds seven stages in order. `Person.stage` stores the code and
a check constraint rejects anything else, so a typo in an import file fails at
write time rather than producing a person at a stage that does not exist.

Customizable stages are part of the pitch against Planning Center. When one
church wants six stages with different names, this becomes a per-church table.
That change is contained because every read already goes through
`stages_for(church)`, which today ignores its argument. Nothing indexes into
`STAGES` directly.

### `stage_since` is the load-bearing column

Increment 3's stuck engine measures time in the current stage. Two consequences
that are easy to get wrong and expensive to fix later:

- **A stage move resets it.** Otherwise someone who just advanced would
  immediately read as stuck.
- **Import sets it from `first_seen_on`, not from the import timestamp.**
  Defaulting to "now" would tell a pastor that all 54 of his people arrived
  this morning, and would leave the stuck engine blind for months.

### Tenant isolation

Every id in this increment arrives from a URL, and an id is only a number:
nothing about it says which church it belongs to. There is no
`db.session.get(Person, id)` anywhere in the codebase. `Person.get_for_church`
takes both and puts the church in the WHERE clause, so the mistake is not
available to make.

Cross-church access returns **404, not 403**. A 403 confirms the id exists
somewhere, which is itself a disclosure.

`PersonEvent.record` takes the church from the person rather than as an
argument, so an event cannot be filed against the wrong tenant by passing the
wrong number.

### Importing a roster

```bash
flask import-people --church journey --file sample-data/journey-roster.csv --dry-run
flask import-people --church journey --file sample-data/journey-roster.csv
flask people-summary --church journey
```

Columns, header row required:

```
first_name, last_name, email, phone, stage, household, first_seen_on
```

Every row is validated before a single row is written. A file with one bad
stage value fails entirely, because importing 340 people and leaving a church
to work out which 12 are missing is worse than importing nothing.

`sample-data/journey-roster.csv` is a realistic church-plant roster: 54 people
across 28 households, children without email addresses, and a stage
distribution shaped like a plant rather than a mature church.


---

## The stuck engine

### Two conditions, not one

Someone is flagged only when **both** are true:

1. They are past their stage's expected time, and
2. Nobody has logged contact in `CONTACT_WINDOW_DAYS` (21).

Either alone is not a problem. Silence for three weeks is normal for someone
who is exactly where they should be. Time in a stage is meaningless without
knowing whether anyone has tried.

### Only transitional stages can flag

Visitor, Guest, and Attender are places people should be moving out of. Member,
Volunteer, Disciple, and Leader are places people arrive at, and their
`expected_days` is `None`.

This distinction is the difference between a useful flag and an ignored one.
The first version of this engine put an expectation on all seven stages. Run
against Journey's actual roster it flagged **39 of 54 people**, because a
Member of three years read as overdue against a 365 day expectation. Nobody
would open that list twice. With destinations excluded and real contact history
imported, the same roster produces **5 flags**, and all five are people a
pastor would genuinely want to call.

### Computed, never stored

`Person.is_stuck` is a property and `Person.stuck()` is the same logic in SQL.
A stored flag would be wrong the moment someone logs a call, and a nightly job
to fix that would mean a pastor sees yesterday's answer.
`TestTheQueryMatchesTheProperty` asserts the two agree on every person, because
a dashboard that disagrees with the record it links to is worse than no
dashboard.

The SQL is an OR over the three transitional stages rather than a CASE
expression, because that shape is what the `(church_id, stage, stage_since)`
index can actually serve.

### Logging contact clears the flag. A note does not.

This is the hard stop from the architecture rules, and the distinction is
load-bearing. Writing "should call Marcus" in the timeline is not calling
Marcus. A system that treats them the same stops flagging the people it exists
to find, and does so silently.

### `last_contact_at` is denormalized on purpose

It duplicates `MAX(contact_log.occurred_at)`. The dashboard asks "who has
nobody talked to" on every load, and answering it with a join and a group by
means the database cannot use an index to skip anyone. With the column the
whole question is a range scan.

Denormalized data drifts, so `flask recompute-contact` rebuilds it from the
log and two tests assert the rebuild is correct. It only ever moves forward:
backfilling an older conversation must not make someone look more recently
contacted than they are.

### Importing contact history matters

`import-people` accepts an optional `last_contact_on` column. Without it every
imported person reads as never contacted and the engine flags most of the
roster on day one. A church migrating off Planning Center has this date. Bring
it across.


---

## The outbox

### Nothing sends inside a web request

A request that calls a mail provider is exactly as slow and as reliable as
that provider, and a failure after the database has committed loses the message
with nobody aware of it. Queuing means the request writes a row and returns.
A worker does the sending, where a failure is visible, retryable, and recorded.

The worker is a **cron job on the same image**, not a background thread in the
web service. A thread dies with the process on every deploy and every restart,
taking whatever it had claimed with it. `render.yaml` runs it every five
minutes, followed by `release-claims`.

### Resend over HTTPS, never SMTP

Port 587 is blocked outbound on Render and most managed hosts. Finding that out
at deploy time after building against SMTP is a rewrite, not a config change.

Three transports behind one interface: `ResendTransport` in production,
`ConsoleTransport` in development so the whole path can be exercised without a
real key or a real recipient, and `MemoryTransport` in tests, which can be told
to fail on demand. Retry handling needs a way to fail that does not involve
the network.

Production with `MAIL_TRANSPORT=resend` and no `RESEND_API_KEY` **refuses to
boot**. A church that believes it sent a welcome email and did not is worse off
than one whose deploy failed loudly.

### Transactional mail ignores opt-out, and that is not a loophole

`app/categories.py` declares `is_transactional` per category. Password resets,
kids check-in codes, and giving receipts always send. Treating them as
marketing means someone who unsubscribed from the weekly digest can no longer
get back into their own account.

Everything else can be turned off, per category or globally.

### Suppression is checked at send time

Someone can unsubscribe in the hour between a message being queued and being
sent, and the answer that matters is the one at the moment of sending. A
suppressed message is recorded as suppressed with its reason, not deleted.

### Claiming is atomic on both databases

A worker takes rows with a conditional UPDATE that stamps a random token, then
reads back only what carries that token. Two workers running at once cannot
claim the same row: the second UPDATE matches nothing. This behaves identically
on SQLite and Postgres, unlike `SELECT ... FOR UPDATE SKIP LOCKED`.

A worker that dies between claiming and sending leaves a stranded row.
`flask release-claims` returns anything claimed more than fifteen minutes ago.

### Failure handling

A temporary failure goes back to the queue and is retried up to five times.
A **permanent** failure, meaning a 4xx that is not 429, is not retried at all:
sending to a rejected address repeatedly damages a sending reputation that
every church on this platform shares.

A message that runs out of attempts is kept as `failed` with its last error.
Silently dropping mail is how a church finds out in March that nobody got the
February newsletter.

### The unsubscribe link

The only route a signed-out stranger may use to change stored data. The token
is 32 random bytes per person, minted the first time a message is queued rather
than at person creation, because an unused secret is a liability. It is scoped
to the church resolved from the host, so one tenant's link is inert on another.

Confirming is a **POST**. Mail clients and security scanners fetch every link
in a message, and a GET that unsubscribes would mean a corporate spam filter
quietly opting people out of their own church's email.

### The API key

Set `RESEND_API_KEY` in the Render dashboard, never in `render.yaml`. Use a
sending-only key, not a full-access one. A key committed to git is a rotated
key.


---

## The member app

`/me/` and `/me/you/`. The same database and the same brand tokens as the staff
view, read by a different person.

Phone-shaped on a desktop so staff can see exactly what a member sees, and
full-bleed below 700px where a phone frame drawn on a phone would be absurd.
One template, one stylesheet.

### A member cannot see anyone else, structurally

No route in the member blueprint accepts a person id. Not one. Every view loads
`current_user.person` and nothing else, so there is no id to tamper with and no
scoping check to forget. A test asserts this by walking the URL map and failing
if any `member.*` rule has arguments.

`User.person` is a tenant-scoped lookup rather than a SQLAlchemy relationship. A
`person_id` pointing at another church would be a data error, and a relationship
would happily load it.

### Staff preview, not impersonation

Staff and leaders can open the member app and see **their own** record, with a
banner saying so. There is deliberately no way to view it as somebody else.
Reading a member's private screen through their eyes would mean a staff account
seeing a private view with no audit trail, and nothing in this increment needs
it.

Members are redirected from `/` to `/me/` rather than shown a stripped-down
dashboard. One dashboard to maintain instead of two that drift apart.

### The check-in PIN

Per spec v3 section B, generated rather than derived from a phone number. With
a uniqueness constraint and a retry loop, two households cannot share a code, so
the disambiguation screen the phone-derived design required is not in the build
at all.

**The PIN is identification, not authorization.** It answers which family you
are at a kiosk. It must never authorize a pickup; the pickup code, generated per
household per session, is the actual control and arrives with Kids at increment
11. Anyone tempted to reuse `checkin_pin` as a credential should read
`app/checkin_pin.py` first.

Blocked on generation: all repeats, all ascending and descending runs in both
directions, and the church's own street number, which is on the building and on
every piece of mail they send.

Four digits is 10,000 codes less the blocklist. At Journey's projected 150 to
250 households that is around 2 percent occupancy. The column is `VARCHAR(6)`
so widening past 2,500 households is a config change, not a migration. Running
out of attempts **raises** rather than returning a duplicate: two families with
one code at a kiosk is a child-safety problem, not an inconvenience.

The PIN is minted on first view of the You tab, not at household creation. Most
households never open that screen, and an unused secret is one more thing to
look after for no benefit.

### Linking a login to a roster record

A login and a pastoral record are separate rows. `flask create-user` attaches
them by matching email on the way in, and `flask link-users` does it in bulk
after an import. Matching on email is imperfect, which is why it is used only
here: the worst case is a member seeing an empty Home screen until staff link
them by hand.

---

## Deploying to Render

`render.yaml` creates all three resources: the static client demo, the Flask
app, and Postgres. In Render choose **New**, then **Blueprint**, then point at
this repo. Do not fill in build or start commands by hand; a value typed into
the dashboard silently overrides this file and lives in a browser tab nobody
else can see.

### The step that is easy to miss

A fresh deploy resolves no tenant and therefore 404s every request, while the
health check keeps passing because it bypasses tenancy. The service looks green
and is unusable. There are two ways out and you need one of them:

**Now, before a platform domain exists.** Point the Render URL at Journey:

```bash
flask set-domain --church journey --domain journey-dos-app.onrender.com
```

**Later, once you own a platform domain.** Set `PLATFORM_DOMAIN` in the
blueprint, add wildcard DNS, and every church is reachable at
`<slug>.<platform domain>` with no further per-church setup.

`flask routing-check` prints exactly which hosts resolve to which church and
says so plainly when the answer is none.

### First deploy, in order

Everything below runs in the Render **Shell** for `journey-dos-app`, not on
your machine. Your local database is SQLite and has no bearing on production.

```bash
flask db upgrade          # only if preDeployCommand did not run
flask seed-tenants
flask set-domain --church journey --domain journey-dos-app.onrender.com
flask routing-check
flask create-user --church journey --email pastor@thejourneychurchsemo.com --name Reed --role staff
flask list-users
```

Accounts are deliberately not in the repo, so production logins are created
once, here, and never committed.

### Two things to check before you start

1. **`preDeployCommand` requires a paid instance type.** The blueprint
   specifies `starter`. On free, run `flask db upgrade` from the shell after
   the first deploy instead.
2. **Verify the current Postgres free-tier retention terms before choosing a
   plan.** Free databases have historically been deleted after a fixed window.
   A database that disappears with a church's roster in it is not a survivable
   failure, which is why the blueprint specifies a paid plan.

The client demo deploys as a separate free static site from `./public`, so the
link you have already shared keeps working.

---

## What is next

Increment 6, resources, reader, and progress. Write a five day plan, publish it,
read it on the phone, watch completion move. Per spec v3 section D.1.

**Self-serve password reset is now overdue.** The outbox exists and `account`
is a transactional category, so nothing blocks it, but the login page still
tells people it arrives at increment 4. Either build it or change that copy.

Three items in spec section F are still open and none of them block increment
6: the revised Settings cost comparison, copy for three screens, and the
onboarding checklist owner.
