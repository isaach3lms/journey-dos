# Deploy notes

## One time only: stamp the existing database

The phase 1 schema on Render was created by `db.create_all()`, so those
tables exist but Alembic has never seen them. There is no `alembic_version`
row, which means Alembic thinks the database is empty.

If you run `flask db upgrade` against it, the baseline migration will try to
`CREATE TABLE churches` and fail with "relation already exists".

Run this **once**, against the live database, before the first deploy that
includes migrations:

```bash
flask db stamp head
```

That writes the `alembic_version` row and tells Alembic "this database is
already at the baseline revision". It changes no tables and touches no data.

Verify:

```bash
flask db current    # should print the baseline revision, not "None"
```

After that, every deploy runs `flask db upgrade` normally.

## Fresh database, local or a new tenant

```bash
flask db upgrade    # builds the schema from migrations
flask init-db       # runs upgrade, then seeds the church and stages
```

## Why the schema moved to migrations

`db.create_all()` creates tables that do not exist. It never alters a table
that does. Every column added from here on would have appeared locally
against a fresh SQLite file and silently never appeared on the deployed
Postgres. The failure mode is a route that works on a laptop and 500s in
production with `column people.x does not exist`.

## Adding a column, from now on

```bash
# edit the model, then
flask db migrate -m "what changed"
# READ the generated file in migrations/versions/ before applying it
flask db upgrade
```

Always read the generated migration. Autogenerate is good at additions and
unreliable about renames: it will usually emit a drop plus an add, which
throws the column's data away.

## Render

Add the migration step to the deploy so it runs before the new instance
takes traffic, and a failed migration blocks the deploy instead of half
applying under load:

```yaml
preDeployCommand: "flask db upgrade"
```

## Environment

`DATABASE_URL` is now required whenever `RENDER` is set. The app refuses to
boot without it rather than falling back to SQLite on ephemeral disk, which
would accept a day of writes and lose them on the next deploy.
