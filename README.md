# journey-dos

Discipleship Operating System. Multi-tenant Flask application, built by
Between Sundays, first tenant The Journey Church, Jackson MO.

**Status: increments 0 and 1 complete.** Foundation, tenancy, identity, and
roles. 118 tests passing. No feature screens yet; every nav item resolves to a
placeholder naming the increment it arrives in.

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

Increment 2, people, households, and stages. Journey's roster, the Journey rail
with real counts, and the person drawer with a real timeline, per spec v3
section D.1.

Three items in spec section F are still open and none of them block increment
2: the revised Settings cost comparison, copy for three screens, and the
onboarding checklist owner.
