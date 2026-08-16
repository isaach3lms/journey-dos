# Discipleship Operating System

Multi-tenant Flask application. First tenant: The Journey Church, Jackson MO.

Spec of record: `dos-v1-spec-journey-church.md`, Section C approved 2026-08-15.

## Increment 0: foundation and tenancy

Five architecture invariants ship in this increment, and each one has a test
that fails loudly if it is ever broken.

| Invariant | Where | Test |
|---|---|---|
| `postgres://` normalized to `postgresql+psycopg2://` at boot | `app/config.py` | `test_config.py` |
| No naive datetimes on any dialect | `app/types.py` | `test_utcdatetime.py` |
| Hard fail at boot when `DATABASE_URL` is missing in production | `app/config.py` | `test_config.py` |
| 500 page renders with the database unreachable | `app/errors.py`, `templates/errors/` | `test_error_pages.py` |
| `app/brand.py` is the only theming lever | `app/brand.py` | `test_brand.py` |

Two of those tests are guardrails rather than feature tests. `test_every_datetime_column_uses_the_decorator`
walks the mapper registry and fails when someone adds a bare `DateTime`.
`test_no_hex_values_hardcoded_in_templates_or_stylesheet` fails the day a hex
code gets pasted into a template. Both are cheap now and save an afternoon later.

## Tenancy

`church_id` on every table. v1 has no global reference data, so there are zero
exceptions. Every child table added from increment 1 onward declares a
composite foreign key:

```python
ForeignKeyConstraint(
    ["church_id", "person_id"], ["person.church_id", "person.id"]
)
```

Use `tenant_table_args()` from `app/models/base.py` so the required
`UNIQUE (church_id, id)` is never forgotten on a parent table.

### Host resolution

Amendment to spec C.2. Journey uses a custom domain, so hosts live in their own
table rather than being derived from a slug.

1. Exact match in `church_domain` (`app.thejourneychurchsemo.com`)
2. Platform subdomain fallback, `<slug>.<PLATFORM_DOMAIN>`
3. Development only: `DEFAULT_CHURCH_SLUG` so `localhost:5000` works

An unresolved host is a 404. It never falls back to the first church in the
table.

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
export APP_ENV=development FLASK_APP=wsgi.py
flask db upgrade
make seed
flask run --debug
```

Then open `http://localhost:5000`. `DEFAULT_CHURCH_SLUG=journey` in `.env`
resolves the tenant without editing `/etc/hosts`.

To exercise real host resolution locally, add to `/etc/hosts`:

```
127.0.0.1 app.thejourneychurchsemo.com
```

## Commands

| Command | Purpose |
|---|---|
| `make test` | Full suite |
| `make upgrade` | Apply migrations |
| `make migrate m="message"` | Generate a migration |
| `make seed` | Create or update the Journey tenant |
| `flask list-churches` | Every tenant and the hosts that reach it |
| `flask add-domain --slug journey --host x --primary` | DNS cutover |
| `flask check-boot` | Verify boot invariants against the running config |

## DNS for Journey

| Record | Host | Points to |
|---|---|---|
| CNAME | `app.thejourneychurchsemo.com` | the Render service hostname |
| CNAME | `journey.dos.betweensundaysconsulting.com` | the same Render service |

Both hosts are seeded, so the platform alias keeps working if the client's
registrar has a problem.

## Not yet built

Increments 1 through 10. Nothing in `app/models/` beyond `church` and
`church_domain` exists yet. `login_manager` has a placeholder user loader that
returns `None` until increment 1 introduces `app_user`.
