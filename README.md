# journey-dos

Discipleship Operating System. Multi-tenant Flask application, built by
Between Sundays, first tenant The Journey Church, Jackson MO.

**Status: every numbered increment is complete**, 0 through 15, plus
self-serve password reset and the installable member app. 865 tests passing
across 19 migrations. Increment 8, the Bible, is deferred pending the YouVersion
answer.
Foundation, tenancy, identity, roles, the roster, the stuck engine, the outbox,
the member app, resources, the giving link-out, groups, services, kids
check-in, messaging, the giving mirror, sequences, settings, and the Bible. Dashboard, People, Resources, and the member app are real
screens; the remaining nav items resolve to placeholders naming the increment
they arrive in.

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
| `flask set-timezone --church x --timezone America/Chicago` | Zone meeting times are read in. |
| `flask import-gifts --church x --file gifts.csv` | Import a giving export. Add `--dry-run` first. |
| `flask match-gifts --church x` | Retry matching after a roster import. |
| `flask giving-stopped --church x` | Who had a standing gift and has gone quiet. |
| `flask run-sequences [--church x]` | Queue the sequence steps that have fallen due. |
| `flask sequence-status --church x` | What is running and why things ended. |
| `flask purge-audit [--days n]` | Drop audit entries past the retention window. |
| `flask purge-push` | Delete push subscriptions nobody is behind. |
| `flask vapid-keys` | Generate the VAPID key pair for push. |
| `flask import-bible --file web.json` | Load World English Bible text. |
| `flask bible-status` | What scripture is available. |
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
| `flask purge-reset-tokens --days 7` | Delete spent and expired reset and verification tokens. |

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

## Resources and the reader

### This content is tenant data, not Python

Stages and notification categories live in Python because they are shape: the
same seven stages for everyone until a church asks otherwise. A five day plan
on Psalm 139 is something The Journey Church wrote and owns, so it lives in
rows, on their side of the tenant boundary, and appears in their app under
their name with no other brand on it.

### The renderer escapes before it formats

Everything a pastor types is displayed in somebody else's browser, which makes
it untrusted input even though the author is trusted. A leader account is one
weak password away from an outsider, and stored script in a member's browser is
far worse than a bold tag that fails to render.

`app/markup.py` escapes the whole string first, then re-introduces a fixed
grammar: paragraphs, `#` headings, `>` blockquotes for scripture, `-` lists,
`**bold**`, `*italic*`. Because escaping runs first, the formatting patterns can
only ever match literal text the author typed.

Deliberately **not** Markdown. A Markdown library accepts raw HTML by default,
supports link targets that can carry `javascript:`, and brings a dependency
whose CVEs become this application's problem. There is no link syntax at all,
so there is nothing to abuse.

### Publishing

A resource starts as a draft and is **invisible** to members, not merely
unlinked: `published_for_member` refuses drafts, so guessing an id reveals
nothing.

Publishing an empty resource is refused. A member tapping into a published plan
with no days would see a blank screen and conclude the app is broken. Blocking
it is one line; explaining it to a pastor afterwards is not.

Archiving rather than deleting, because people have completions against it and
deleting would erase a record of what they actually read.

### Progress

One row per person per session completed. Absence means not done.

Append-only on purpose: a completion is a fact about a moment, and it survives
the plan being edited afterwards, which a boolean on a join row would not.
`mark` is idempotent, because a double tap on a phone is normal and a second
row would inflate every count that reads the table.

`started_counts` is one grouped query for every resource, so a screen with
twenty plans is still two queries rather than twenty-one.


---

## Password reset

`/auth/forgot` and `/auth/reset/<token>`. Both public, because somebody who
cannot sign in cannot be asked to sign in first. Both scoped to the church
resolved from the host, so a token minted at one tenant is inert at another.

### The token is never stored

Only a SHA-256 of it. A reset table full of usable links is one database read
away from every account, and database reads leak in ways password hashes are
built to survive: backups, log shipping, a read replica, a support query pasted
into a chat. A stolen table yields nothing.

Plain SHA-256 rather than a KDF is correct here and only here. The token is 32
bytes of `secrets` output, so there is no dictionary to attack and nothing a
slow hash would buy. A user-chosen password is the opposite case, which is why
`User.set_password` uses scrypt.

### Single use, one hour, and requesting again retires the old one

A reset link otherwise sits in an inbox forever, and inboxes get compromised
long after the reset was legitimate. Somebody who clicks the button three times
because nothing seemed to happen should not end up with three live keys.

### The form cannot be used to find out who attends

Unknown address, deactivated account, and a real one all return the same
message and the same status. Only the real one queues an email.

Five requests per address per hour, so the form cannot bombard somebody's inbox
from a church they do not attend.

### A reset signs out every other device

`User.session_version` is part of the session cookie, and `set_password` bumps
it. Every cookie minted under the old password stops resolving, everywhere.

Without this, somebody resetting their password *because a device was stolen*
would find the thief still signed in on that device. It is the single most
common reason a person resets a password, and the least commonly handled.

A reset also clears a lockout, since being locked out is the other reason
somebody arrives at this form.

### It reaches people who unsubscribed

The email goes out under the `account` category, which is transactional. That
is the whole reason `is_transactional` exists in `app/categories.py`: without
it, leaving the newsletter would lock somebody out of their own account.


---

## Giving

A link-out, not a payment integration. Per spec v3 section A, this decision
does three things at once:

- **Deletes the hardest objection in the sale.** No treasurer migrates donor
  history, re-enrolls a recurring giver, or issues statements from two systems
  in one year. The pitch is "keep Tithely, keep your rates, we plug into it."
- **Removes PCI scope entirely.** This application never touches a card number,
  so it is not a payment environment.
- **Cuts roughly a fifth of the build.** Funds, batches, processor webhooks,
  statement generation, and refunds are all out of scope.

Two nullable URLs on the church row, exactly as spec A.3 describes. The
credential table with encrypted API keys arrives at increment 13 with the
read-only sync.

### The URLs are validated, not trusted

A staff member types them and a congregation clicks them, so
`app/giving.py` is doing security work:

- **Scheme must be https.** `javascript:` in an href executes in the clicking
  member's browser with their session.
- **Host must be on an allowlist**, matched exactly or as a subdomain.
  `endswith` alone would accept `nottithe.ly`, which is the whole trick.
- **Userinfo is rejected.** `https://tithe.ly@evil.example.com` reads as
  Tithely to a person and resolves somewhere else entirely.
- **Both links validate before either saves**, so a half-configured church
  cannot exist.

Every rendered link carries `rel="noopener noreferrer"`. Without it, a
`target="_blank"` page gets scripting access to the opener through
`window.opener`.

### The member Give tab appears only when it works

No giving URL, no tab. A tab that leads to an apology is worse than no tab.


---

## Groups, and the first wall-clock time

### Group leadership is not a login role

A group leader is a `GroupMembership` with `role='leader'`. A `User` with the
login role `leader` is a different thing entirely, and conflating them would be
a quiet authorization bug: the woman who hosts the Wednesday women's group
leads *that room* and has no business in the church-wide roster, while a staff
member with the `leader` login may lead no group at all.

Tests assert both directions: leading a group opens neither the roster nor the
groups admin.

### Meetings are the first thing that has to be read, not just compared

Everything until now stored aware UTC and only ever compared it. A duration is
the same length in any zone, so nothing cared where the church was.

"Wednesday 7:00pm" is not a moment in time, it is a moment in a place. A group
in Jackson reading 6:00pm because the server runs on UTC has people arriving an
hour late.

So `church.timezone` is now a real column, and `app/timeutil.py` is the only
place a conversion happens: stored as UTC, converted at the edge where it is
displayed. A staff member types a wall-clock time and it round-trips.

Two deliberate choices:

- **A bad timezone value falls back rather than raising.** It shows the wrong
  hour, which is visible and fixable. An exception takes the page down.
- **A naive datetime is assumed UTC, never local.** Guessing local would shift
  every stored time silently, which is the worst possible failure mode for
  this.

`flask set-timezone` sets it, and validates the name. Onboarding should run it
deliberately, because a wrong value here produces no error at all.

### RSVPs need authorization, not just a valid id

Belonging to the group is what permits an answer. Without that check anyone
signed in could RSVP to a meeting they were never invited to, and the "9 going"
a leader plans catering around would mean nothing.

An RSVP updates rather than appends, unlike a session completion. A completion
is a fact about a moment and stays; an RSVP is a current intention. "She said
yes last Tuesday" is not useful, "she is coming" is.

### The dashboard ratio now has something behind it

`Group.people_in_a_group` is the query behind "Members in a group" on the
health card, which until now had no data under it.


---

## Services, songs, and teams

### No lyrics, no chord charts, ever

Reproducing either requires a CCLI SongSelect licence that the **church** holds,
not the vendor. A platform storing lyrics for every tenant is reproducing
copyrighted work at scale on behalf of people whose licences it cannot verify.

This system stores what a plan actually needs: title, author, the church's own
CCLI number, and a key. The words stay wherever the church already licenses
them. A test asserts the `song` table has no column whose name contains
`lyrics`, `chords`, `chart`, or `sheet`.

### Keys are not arithmetic

`app/music.py` is pure logic with a right answer, which is why it carries the
heaviest tests in the increment.

**Spelling matters.** A♯ and B♭ are the same pitch and are not the same key. A
band handed "A# major" stops and asks, because that key signature has ten
sharps including double sharps. The conventional spelling differs between major
and minor, so both tables exist: `Bbm` not `A#m`, but `C#m` not `Dbm`.

**Intervals take the shorter way.** `interval_between("C", "G")` returns -5, not
+7. A band told "down a fourth" reaches for something different from "up a
fifth" even though the pitch is identical.

**Capos are the point.** A volunteer guitarist who owns five chord shapes can
play anything if told "capo 3, play G". Handing them "Bb" alone is technically
complete and practically useless.

**Lowercase does not mean minor.** Classical notation says "a" is A minor, and
the first version of this module honored that. It is wrong for this audience: a
volunteer typing "g" means G major essentially always, and silently recording G
minor would hand a band the wrong key with nothing on screen to reveal it.
Minor has to be said out loud.

### Three ideas kept apart

- A **Team** is people who serve together. Worship, Kids.
- A **TeamPosition** is a job on it. Acoustic, Drums.
- An **Assignment** is one person, one position, one service, one answer.

`position_name` is copied onto the assignment rather than looked up later, so a
renamed or deleted position cannot turn "Drums, 12 March" into "None, 12 March".

### Sending the plan

Queued through the outbox, never sent inside the request. Deduped per person
per service **per calendar day**: a double-clicked button emails a volunteer
once, and a genuine resend next week after the plan changed still goes out.

Keying the dedupe on `plan_sent_at` was the first attempt and defeats the first
case, because the first send writes that field before the second request reads
it.

Sent under the `group` category, which is opt-out-able but not marketing:
somebody who left the newsletter still needs to know they are playing on Sunday.

### Nobody answers for anybody else

An assignment names a person, so `member.respond_to_assignment` checks the
assignment belongs to the signed-in person before writing. Without it a name
lands on a plan that never agreed to it, and a worship leader finds out on
Sunday morning.


---

## Kids check-in

### Two codes, two jobs, never interchangeable

**The household PIN identifies.** Which family are you. Permanent, printed on
a label, visible in the member app, four digits.

**The pickup code authorizes.** May this person collect this child. Generated
fresh for one household for one session, shared across siblings so a parent
carries one code and not three, four letters.

A permanent code cannot authorize a pickup. If it could, anyone who ever saw a
label, a phone screen, or a sticker on a coat could collect a child weeks
later. Tests assert both directions: a PIN finds nobody at check-out, and a
pickup code opens no family at the kiosk.

Letters, not digits, and no I, O, S, or Z. On a printed label at arm's length
those read as 1, 0, 5, and 2, and a four-digit PIN sitting beside a four-digit
pickup code gets confused by a tired volunteer at 11am. The confusion would run
in the dangerous direction.

### A check-in system with no check-out record is a headcount

The demo omitted the check-out write path. Every `Checkin` row here answers
three questions afterwards: who was present, who collected them, when.

- **`collected_by` is free text.** It is often a grandparent who is not on the
  roster, and a name written down beats a dropdown that cannot express the
  truth.
- **Checking out twice never overwrites the first record.** The first
  collection is the one that happened.
- **Closing a session does not check anyone out.** A child still in a room at
  the end of a service is exactly what staff need to see, not something to
  tidy away.
- **Ids alone cannot check a child out.** The route re-derives what a code
  covers rather than trusting posted ids, or a request could collect a child
  whose code the person at the desk never had.

### The kiosk requires a signed-in volunteer

A tablet in a lobby accepting PIN attempts from anyone who walks past will
eventually enumerate every household code in the church. A volunteer signs the
tablet in once on a Sunday morning; the families using it never see a login.
Attempts are also rate limited per browser session, so an unattended tablet
still cannot be walked through the code space.

### Forgot the code

Spec section B.2 says text it. There is no SMS provider in this system yet, and
adding one means a second vendor, a second set of credentials, and a second
thing to rotate. This emails it instead, through the outbox that already
exists, under the transactional `kids_checkin` category so it reaches a parent
who unsubscribed from everything else.

The kiosk answers identically whether or not the address is on file. A kiosk
that says "no such address" is a device for finding out who attends.


---

## Messaging

Three kinds of conversation, and the differences are authorization rules rather
than cosmetics.

**Announcement.** Church-wide, staff post, everyone reads. There are **no
membership rows**: visibility is "anyone at this church". Writing a row per
person to say "everyone", then maintaining it as people join and leave, is a
synchronization problem with no upside.

Only staff post. A church-wide broadcast anyone can reply to stops being an
announcement and becomes a room nobody chose to join.

**Room.** Invite only. Membership is the authorization, as it is for groups.

**Direct.** Exactly two people, canonicalized as `min:max` on the conversation,
so two people cannot end up with parallel threads depending on who wrote first.

### A private room 404s, it does not 403

Telling somebody a room exists is itself a disclosure about who is talking to
whom. `can_read` and `can_post` are model methods, called by both the staff
blueprint and the member blueprint, so the two views cannot drift into
disagreeing about who may do what.

### Messages are soft deleted

A church needs an account of what was said in its own rooms. A hard delete lets
a leader erase a conversation somebody later needs to reference, and leaves a
gap nobody can see. This clears the words, keeps the row, and shows a visible
hole.

`author_name` is copied onto the message, so a thread still reads correctly
after somebody is archived. "Marcus said" should not become "someone said".

### A chat renderer, not the document one

`render_message` escapes and preserves line breaks and nothing else. A person
typing "- 5" in a conversation means minus five, not a bullet, and "# 1" means
number one. Applying document formatting to chat rewrites what people said.

### Announcements can also go by email

Optional per post, under the opt-out-able `announcement` category. Somebody who
turned church announcements off still sees it in the app; they just do not get
a second copy in their inbox, which is exactly what they asked for.

Room posts never email the church. That is checked, not assumed.


---

## The giving mirror

Read only, in both senses. Nothing writes to Tithely, and nothing here is the
source of truth. Tithely owns the ledger; this is a copy kept so the system can
answer a question Tithely cannot. Not "did revenue drop", which is a budget
report, but "has Chris Vaughn stopped giving", which is usually a discipleship
signal weeks earlier.

### Matching is where the danger is

The failure mode is not a missed match. It is **money attributed to the wrong
person**, which surfaces in a giving statement at year end addressed to
somebody who did not give it.

Three rungs, each weaker than the last: email, phone, name. **Only email
auto-matches, and only when it points at exactly one person.** Phone is a
household line as often as a personal one. A name is not an identifier: two
Chris Vaughns in a church is ordinary.

**Ambiguity never auto-matches.** More than one candidate sends the gift to the
review queue with the candidates attached, and a human decides.

### The review queue does not invite a mistake

When there is no suggestion, the person dropdown defaults to a non-person
option. It previously defaulted to whoever was first alphabetically, sitting
one click from a green confirm button, which is how a company's gift gets
attached to a child. Confirming without choosing now attaches nothing.

### Lapse detection has a grace period, per frequency

A card that failed on the 1st and succeeded on the 4th is a payment system
doing its job, not a person leaving. A standing gift counts as stopped once it
is past **its own rhythm** plus three weeks, so a weekly giver lapses sooner
than a monthly one. Cancelled arrangements never appear: they told the church,
which is a decision rather than a signal.

The SQL and the Python property are asserted to agree on every row, because a
dashboard that disagrees with the record it links to is worse than no
dashboard.

### The credential is encrypted at rest

The stored private key can read every gift a church has ever received. It is
encrypted with a key derived from `SECRET_KEY`, never logged, never rendered,
and absent from `__repr__` because reprs end up in logs.

The tradeoff: rotating `SECRET_KEY` makes stored credentials unreadable. That
is recoverable, because these are third-party keys a church can re-enter, and
it would not be acceptable for anything irreplaceable.

### CSV is not the lesser path

Tithely API access is request-based with no published turnaround.
`flask import-gifts` writes to exactly the same tables the sync will, so
approval upgrades the plumbing without changing anything downstream. Imports
are keyed on `transaction_id`, so overlapping exports do not double a church's
totals, and a re-import never undoes a match a human made: the provider owns
the ledger, the match is ours.

Amounts are parsed as integer cents through string arithmetic, never a float.
19.99 is not representable in binary floating point and a church's totals
should not drift by a cent a year.

---

## A constraint that was wrong for churches

`person.email` was unique per church until increment 13. A married couple
sharing one address is the normal case in a church, not an edge case, and that
constraint meant the second spouse could not be entered at all: a wall a church
hits on its first afternoon of data entry.

It is now indexed and not unique. The cost is that email is no longer an
identifier, so every lookup through it decides what to do with more than one
row. They all refuse rather than guess. `User.link_person_by_email` returns
False on an ambiguous address, because linking a login to the wrong spouse
would show one person the other's record.

The tell that the constraint was wrong: the matching module was already written
to handle a shared address, defending against a situation the schema forbade.


---

## Sequences and automations

Sequences are **Python**, enrollments are **rows**. Same reasoning as stages and
notification categories: a sequence is shape, and the shape is the same for
every church until one asks otherwise. In the database it would mean a
migration to change a sentence, a UI nobody uses, and no way to review a
wording change in a diff.

### Two hard stops, and they are the whole design

1. **A human logs real contact.**
2. **The person reaches the target stage.**

The first matters most. The promise of this product is that the system notices
people and then gets out of the way when a human steps in. A welcome series
that keeps emailing somebody the pastor already phoned is worse than no
automation, because it tells that person nobody is paying attention.

A **note does not stop a sequence.** Only a logged conversation does, which is
the distinction increment 3 exists to keep.

### Both stops are checked twice, on purpose

Once when the event happens, so the enrollment is visibly stopped on the
person's record, and again inside the worker immediately before sending.
Somebody can be phoned in the hour between a step falling due and the worker
waking up. Checking only at the event leaves a race; checking only at send
leaves a pastor looking at a record that still claims a sequence is running.

### Offsets, not dates, with a catch-up limit

Steps are day 0, day 3, day 7 **from enrollment**, so a worker down for a day
does not push the whole series a day later. A step more than five days overdue
is skipped rather than sent: if the worker was off for a month, the person on
day 2 of a welcome series does not want the day 2 email in September, they want
to be left alone.

### Editing a sequence cannot strand an enrollment

An enrollment stores the code and a step index, not a copy of the step, so
edited wording takes effect on the next send. A sequence that gets shorter, or
is removed entirely, completes the enrollment rather than erroring in a cron
job where nobody would see it.

### Staff always overrule it

There is a Stop button on the person record, so nobody has to fake a phone call
to end a sequence.


---

## Settings, support, and the audit surface

### The cost comparison is data, not markup

`REPLACES` and `DOS_PRICE_CENTS` in `app/content.py`. Changing the number is one
edit, and a test asserts the arithmetic: **$294 replaced, $100 ours, $194 a
month saved.** Spec section F item 1, closed.

Two deliberate omissions, both tested:

- **The Bible is not counted as a saving.** It is listed as included. Most
  churches already use a free app, and claiming it is the kind of overstatement
  a pastor checks and remembers.
- **Giving fees show as unchanged.** The pitch is "keep Tithely and your
  rates", so claiming a saving there would contradict the giving screen.

### The accent guard finally has a form

`assert_accent_readable` was written in increment 0 and has been unused since.
It is now wired to the Settings colour field, so a pastor pasting Journey gold
is stopped at the form rather than discovered by a volunteer squinting at a
button in a lobby.

### The audit log is append only

No update path, no delete route, no `is_deleted` column, and tests asserting
all three. A log a leader can edit is a story.

`actor_name` is copied onto the entry, so it still names who did it after an
account is removed. "Someone changed the giving keys" is not an audit trail.

**It never holds a secret.** Not an API key, not a reset token, not a pickup
code, and not the address somebody typed into a failed sign-in, because a log
of attempted addresses is a list of who an outsider thinks attends the church.
`scrub` is the second line; the first is that no call site passes one. A test
walks every recorded action to check.

**It is deliberately not a log of everything.** A record of every page view
buries the twelve entries that matter under thousands that do not. What is
recorded: sign-ins, failed sign-ins, password resets, branding changes,
provider keys, matched gifts, deleted messages, sequences stopped by staff, and
every child collected.

Kept for 400 days, purged by the same cron job that runs the outbox.


---

## The Bible

### The floor is public domain

The World English Bible is out of copyright. It is stored in this system's own
database, served with no network call, and available to every church with **no
registration, no key, and nobody's permission**. A member can always read the
passage in a plan.

`flask import-bible` loads it. `sample-data/web-bible-sample.json` ships a small
subset so the reader works out of the box.

### A licensed translation runs on the church's own registration

Per spec v3 section C.5, each church registers with YouVersion Platform and we
operate the app on their behalf, which is why the credential is per church and
lives in `integration_credential` alongside the giving keys.

### Licensed text is never stored

Not in a table, not in a cache, not in a column added later for performance.
`bible_verse` has **no `translation` column**, precisely so there is nowhere
for NIV to accumulate. A licensed translation sitting in this database,
replicated across every backup, is a violation that grows quietly and gets
discovered by somebody else.

Two tests hold that line: one asserts the column does not exist, one fetches a
licensed passage through a stubbed provider and checks nothing was written.

`bible_verse` is also **global rather than tenant scoped**, the only table in
the system besides its index without a `church_id`. Scripture is not a church's
data, and copying 31,000 verses per tenant would be absurd.

### Failure falls back rather than failing

A missing key, an expired key, a rejected request, or a service simply down all
produce the World English Bible plus a line on the page saying which
translation is being shown. A member opening a reading plan on a Sunday morning
should never see an error where a psalm was meant to be, and should never be
misled about which translation they are reading.

### Reference parsing is its own module

`app/bible/reference.py`, pure functions, no database. Two ambiguities handled
explicitly rather than guessed at:

- **A leading number belongs to the book.** "1 John 4" is the first epistle,
  chapter 4. Parsing left to right without knowing that produces confident
  nonsense.
- **Psalm and Psalms are one book**, as are Song of Songs and Song of Solomon,
  and Revelation and Revelations, which is wrong and universal.

An unparseable reference returns None rather than raising, so a typo in a
reading plan shows one apologetic card instead of taking down the page.


---

## Installing the member app

A manifest, a service worker, and an offline page. No app store, no build
tooling, no second codebase: a member adds it to their home screen and it opens
without browser chrome, under their church's name and colour.

### The manifest is per church

Generated from the same brand tokens as every screen, so an installed icon says
"Journey", not "Between Sundays". A member should experience it as their
church's app, because it is.

`short_name` is what appears under the icon, and home screens truncate hard.
Taking the first word produced **"The"**, which is wrong for the majority of
church names because most start with an article. It now skips articles and
generic words: "The Journey Church" becomes "Journey", "Church of the Redeemer"
becomes "Redeemer".

### The caching rule that matters

A church tablet in a lobby and a family iPad are both shared devices. A cache
that outlives a session shows the next person somebody else's giving history,
household PIN, or conversations. So there are two caches:

- **`-assets`**: CSS and icons. No data about anybody, cached hard.
- **`-pages`**: anything under `/me/`. Deleted the instant somebody signs out,
  via a message the sign-out form posts to the worker.

Anything under `/auth/` is never cached at all. A cached reset link is a
security problem, not a convenience.

Pages are **network first**, so a member sees current data whenever they can.
The cache is a fallback for a tunnel or a bad signal, not the default.

The worker only handles GET, only handles its own origin, and deletes every
cache from an older version on activate, which is how a deploy reaches a phone
that already has the old files.

### One head, five shells

The staff app, the member app, the signed-out pages, the login page, and the
kiosk each had their own `<head>` and had drifted. The login page ended up
without a manifest link, which is the one screen most people are looking at
when they decide to install. All five now include `partials/head_meta.html`,
and a test fails the build if any of them grows its own copy again.


---

## Self-registration

Off by default, per church, toggled in Settings by staff and audited when it
changes.

**The direction of that default is the decision.** A church that has not
thought about it should not discover that strangers can read its
announcements. Turning it on is one deliberate click by somebody who
understands what it opens.

### The rule that carries the risk

**A new account is never attached to an existing person until the address is
confirmed.**

A linked record holds a household check-in PIN, giving history, and contact
details. Linking on an unconfirmed address would let anyone who knows a
member's email address claim that member, and the PIN is what identifies a
family at a kids kiosk. Controlling the inbox is the same bar the password
reset flow already sets, which is why confirming is enough and nothing less is.

Somebody with no roster match becomes a **new person at Visitor**, appearing on
the dashboard the same way anyone who walked in on a Sunday would, and entering
the welcome sequence. A shared household address resolves to neither spouse: it
creates a new record rather than guessing.

### What signing up does not get you

Member role, nothing more. Rooms, giving, kids check-in, and the roster all
stay closed. A stranger who registers can read church-wide announcements, which
is inherent to letting anyone join, and the Settings copy says so plainly
rather than burying it.

The form is also not a way to find out who already has an account: an existing
address and a new one produce the same response, and an existing account is
never overwritten.

### The verification default was wrong first

The first version defaulted accounts to unverified, and the test fixtures found
it immediately: every account not created through the CLI was locked out.

Every path that creates a user except self-registration involves a staff member
vouching for the address, which is a stronger signal than a click in an inbox.
So the default is **verified**, and `auth.join` clears it, rather than every
other path having to remember to set it. The migration backfills existing
users, since they all predate the column.

That clear happens **after** the flush, because a column `default=` fires at
INSERT and would overwrite a None set at construction. Same trap as sequence
enrollments in increment 14.


---

## Creating your own account

Off unless the church turns it on. `church.allow_self_signup` defaults to
false, and **the default is the decision**: a church that has not thought about
it should not discover that strangers can read its announcements. The toggle is
in Settings, staff only, audited, with the tradeoff stated on the screen next
to it.

### Two rules do the safety work

**An account is useless until the address is confirmed.** Sign-in is refused
with a plain explanation and a resend button, so a stranger who guesses
somebody's email achieves nothing.

**A new account is never linked to a roster record at creation.** Linking
happens only on verification, and only when the address matches exactly one
person. A linked record carries a household check-in PIN, giving history, and
contact details; handing that to whoever typed the address first would be
account takeover, not convenience. Controlling the inbox is the same bar the
password reset flow already sets, which is why confirming is enough and nothing
less is.

A shared household address matches two people and links to neither, reusing the
refusal built for `User.link_person_by_email`.

### A confirmed stranger becomes a Visitor

If nobody on the roster holds the address, verification creates a Person at
stage Visitor with today as `first_seen_on`, and enrolls them in the welcome
sequence.

An account with no pastoral record is invisible to the stuck engine, the rail,
and every sequence, which would mean the one person who actively raised their
hand is the one nobody follows up.

### The verification flag is fail-closed

`email_verified_at` is null by default. Defaulting to verified would mean any
future code path that creates a user and forgets to clear the flag hands out a
confirmed account, and that failure is silent. The cost is that every place
which legitimately vouches for an address says so: `flask create-user`, the
seeds, and the test fixtures all call `mark_verified`. A staff member typing an
address is a stronger signal than a click in an inbox.

**The migration backfills existing accounts.** Without it the deploy locks out
every user at every church, including the person who would have to fix it. Any
account predating this feature was created by a staff member, which is exactly
what the column records, so it is set to `created_at`. A test asserts the
backfill is present in the migration file.

The form answers identically whether or not the address already has an account,
so it cannot be used to find out who attends.


---

## Deleting your own account

Required by both app stores for any app that lets people create an account, and
right regardless. It is in the app, on the You screen, and it does not route
through an email to support.

**What it deletes is the thing the person created: the login.** The pastoral
record is the church's, in the same way a paper roll would be, and deleting it
would take a child's check-in history and matched giving with it. The screen
says exactly that rather than implying the church forgets them.

The password is re-entered, not just clicked. A phone left unlocked on a table
is the normal case, and this is not undoable.

**The last active staff account cannot delete itself.** It would lock the
church out of its own data with nobody able to undo it.

Every deletion is audited, and the person's push subscriptions go with it.

---

## The privacy policy

`/privacy/`, public, per church, generated from what the application actually
does. Both stores require a URL, and Apple's questionnaire asks you to declare
each data type and whether it is linked to identity, so a bought template does
not survive review.

It declares children's check-in records and giving explicitly, because the app
holds both. It states what is never collected: card numbers, location, device
contacts, advertising identifiers. Those are claims the rest of the codebase is
built to keep, and there are tests asserting the page still says so.

**It is part of every future increment.** If something starts collecting a new
kind of data, this page changes in the same commit.

---

## Push notifications

Web Push, chosen over a native-only service on purpose: it works in the
installed PWA on both platforms today and keeps working unchanged inside a
Capacitor wrapper later. One transport, two delivery targets, no rewrite when
the store path arrives.

**Push is a second transport beside Resend, not a second system.** The opt-out
check is the same one email makes, at the same moment, immediately before
sending. Somebody who turned off "Next steps" turned off next steps, not email
specifically, and honouring that in one channel and ignoring it in the other
has not honoured it. Transactional categories still send, for the same reason
they do in email.

**A subscription belongs to a device.** One member has a phone, a tablet, and a
laptop, each a separate endpoint with its own keys. Turning notifications off
on one must not silence the others. Re-subscribing the same browser updates the
row rather than adding one, because the endpoint is the identity.

**A revoked endpoint is deleted, not retried.** A push service answers 404 or
410 forever for a subscription behind a browser that no longer exists.
Retrying accumulates garbage until the worker spends its time talking to
nothing. `flask purge-push` runs on the same cron as the outbox.

**A payload says little.** Lock screens are readable by whoever is standing
nearby, so a notification carries a title and one short line: enough to get
somebody to open the app, which is the only thing it needs to do. No giving
amounts, nothing from a private conversation, no child's name. Notifications
carry a tag so a repeat replaces rather than stacks, because three copies of
the same reminder is how somebody turns them off for good.

**Setup.** `flask vapid-keys` generates the pair. One pair covers every church:
VAPID identifies this application to the push services, not the tenant.
Rotating it invalidates every subscription everywhere, which is why it lives in
config and in a deploy rather than in a form. Production with
`PUSH_TRANSPORT=webpush` and no private key refuses to boot.


---

## The Services redesign

The first version let you build a plan. This version stops you rebuilding it
every week, which is the actual job.

### A service type holds the shape

Sunday Morning, Wednesday Youth, Christmas Eve. Each holds the running order
that rarely changes and the staffing it usually needs, so a new service arrives
as a real plan rather than an empty page.

**It is copied, never referenced.** Editing this week cannot rewrite the
template, and editing the template cannot rewrite a service that already went
out.

### The plan is a run sheet

Every item shows the time it starts, computed from the service start rather
than stored. A stored time goes stale the first time somebody adds two minutes
to the welcome; a computed one reflows the whole plan, including when the
service itself moves.

**Sections group what follows and take no time of their own.** Pre-service,
Worship, Word, Response: the way every worship plan on paper has always been
written.

Items move up and down, and the plan renumbers after every change so positions
stay 1..n. A gap makes the next insert land somewhere surprising.

### Staffing answers the question a leader actually has

Not "who is on this" but "who is still missing." Each position shows filled
against wanted, counting accepted separately: somebody who has not answered yet
is not a gap, and treating them alike either panics a leader or hides a real
hole. A decline reopens the slot.

### Copying a previous week

Replaces rather than appends, because "copy last week" means this week looks
like last week, not like both stacked.

**Assignments are never copied.** Last week's team is not this week's, and a
plan that arrives pre-filled with names nobody asked is how a volunteer finds
out they are playing by reading it on Sunday.

### A capo of zero is not a capo

The row already names the key. "Key G, capo 0, play G" is noise on the one
screen a musician reads while setting up.


---

## Reporting, blocking, and the community standards

App Store Guideline 1.2 asks four things of any app where people post content
others read. All four are here, and each is tested as behaviour.

**People agree before chat opens.** `/community/` is public and says there is
no tolerance for objectionable content. A member sees the standards the first
time they open Chat and cannot post until they tap I agree.

**A filter stops the obvious at the keyboard.** `app/moderation.py`, a short
list written for a church: "hell" and "damn" are not on it, because a filter
that rejects Matthew 10 is one people learn to route around. A refused message
is never stored, and nothing is logged with its text.

**Anybody can report a message.** A report asks a human to look; it does not
remove the message, because one unhappy reader could otherwise silence anybody.
Every staff member is emailed at once, without the words of the message in the
email, and the report waits in Messages, Reported messages.

**Anybody can block the person who wrote it.** It acts immediately and needs
nobody's permission. Their messages disappear for the person who blocked them,
stop counting as unread, and a report is filed so staff know. Undo it on the
You screen. No route takes a person id: a block is made from a message.

**The migration also replaces two CHECK constraints.** Adding the "moderation"
notification category changed the model but not the database, and every staff
alert failed with an IntegrityError in a running app while all tests passed.
`test_config.py` now builds the schema from migrations and fails if any model
CHECK constraint is missing from it, across every table.

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

The numbered build plan is finished, and the member app is installable. What
remains is not code.

**Load the full World English Bible.** The sample covers three books. The full
public domain text is a single `flask import-bible` run.

**The YouVersion answer.** NIV needs a written reply to two questions from spec
C.5: whether a vendor-operated app under a church's own registration is
permitted under the non-commercial terms, and whether NIV is enabled for such a
key. Nothing is blocked, because the WEB path is complete and needs nobody's
permission. But that answer affects every client ever onboarded, not one
module, and it should be in writing before a pastor is promised the translation
he preaches from.

**Tithely API access.** The CSV importer covers it today and writes to the same
tables, so approval is an upgrade rather than a prerequisite.

**No SMS provider exists.** Anywhere the spec says "text", this emails,
including the kiosk forgot-code flow where it matters most. A parent standing
at a tablet in a lobby will not check email. This is the largest remaining gap
between the product and the spec, and it is a vendor decision rather than a
build one.

**App Store listings** need a Capacitor wrapper and, under Apple's rule on
templated apps, submission from each church's own developer account. Roughly
two to three weeks for the first, a day each after. Worth piloting with Journey
before promising it to a church you have not sold yet.

Increment 8, the Bible, is deferred until YouVersion answers. Spec section F
item 2a is closed: the kiosk "I forgot my code" flow shipped with this
increment.

SMS remains unbuilt. Anywhere the spec says "text", this emails instead.

**Send the YouVersion email before starting it.** Spec v3 section C.5 has the
paragraph verbatim. It ships on the WEB fallback regardless, but a wrong
assumption about the non-commercial terms affects every client ever onboarded,
not one module, and the answer is worth having in writing before promising a
pastor the translation he preaches from.

Self-serve password reset shipped alongside increment 6 and is documented
above. The outbox exists and `account`
is a transactional category, so nothing blocks it, but the login page still
tells people it arrives at increment 4. Either build it or change that copy.

Three items in spec section F are still open and none of them block increment
6: the revised Settings cost comparison, copy for three screens, and the
onboarding checklist owner.
