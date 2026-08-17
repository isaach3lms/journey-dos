# journey-dos

Discipleship Operating System. Multi-tenant Flask application, built by
Between Sundays, first tenant The Journey Church, Jackson MO.

**Status: increment 0 complete.** Foundation and tenancy. 70 tests passing.
No feature screens yet; every nav item resolves to a placeholder naming the
increment it arrives in.

---

## Run it locally

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
flask run
```

Then open, in order:

- `http://127.0.0.1:5000/?tenant=journey` and note the green chrome
- `http://127.0.0.1:5000/?tenant=riverbend` and note it is a different brand
  from the same code, the same templates, and the same stylesheet

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

### Two rules that earned their tests the hard way

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

## Deploying

`render.yaml` creates all three resources. In Render: **New**, then
**Blueprint**, then point at this repo.

Two things to check before you do:

1. **`preDeployCommand` requires a paid instance type.** On free, run
   `flask db upgrade` from the Render shell after the first deploy instead.
2. **Verify the current Postgres free-tier terms before choosing a plan.**
   Free databases have historically been deleted after a fixed window. A
   database that disappears with a church's roster in it is not a survivable
   failure, which is why the blueprint specifies a paid plan.

The client demo deploys as a separate free static site from `./public`, so the
link you have already shared keeps working.

---

## What is next

Increment 1, identity and roles. Staff, leader, and member logins with
different navigation, per spec v3 section D.1.

Three items in spec section F are still open and none of them block increment
1: the revised Settings cost comparison, copy for three screens, and the
onboarding checklist owner.
