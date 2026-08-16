"""Flask CLI commands.

``seed-church`` is the whole of tenant onboarding in increment 0. Adding a
church is a row plus its hosts. There is no migration and no code change.
"""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext

from app.extensions import db
from app.models.church import Church, ChurchDomain
from app.tenancy import normalize_host
from app.types import utcnow


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_church)
    app.cli.add_command(add_domain)
    app.cli.add_command(list_churches)
    app.cli.add_command(check_boot)


@click.command("seed-church")
@click.option("--slug", required=True, help="Subdomain label, e.g. journey")
@click.option("--name", required=True)
@click.option("--city", default=None)
@click.option("--state", default=None)
@click.option("--timezone", "tz", default="America/Chicago")
@click.option("--accent", default="#2563FF")
@click.option("--host", "hosts", multiple=True, help="Repeatable. First is primary.")
@with_appcontext
def seed_church(slug, name, city, state, tz, accent, hosts):
    """Create a tenant and its hosts. Idempotent on slug."""
    church = db.session.query(Church).filter(Church.slug == slug).one_or_none()
    if church is None:
        church = Church(slug=slug, name=name)
        db.session.add(church)
        click.echo(f"Creating church {slug}")
    else:
        click.echo(f"Updating church {slug}")

    church.name = name
    church.city = city
    church.state = state
    church.timezone = tz
    church.accent_hex = accent
    db.session.flush()

    for index, raw in enumerate(hosts):
        host = normalize_host(raw)
        existing = (
            db.session.query(ChurchDomain).filter(ChurchDomain.host == host).one_or_none()
        )
        if existing and existing.church_id != church.id:
            raise click.ClickException(
                f"Host {host} already resolves to a different church. Refusing."
            )
        if existing is None:
            db.session.add(
                ChurchDomain(
                    church_id=church.id,
                    host=host,
                    is_primary=(index == 0),
                    verified_at=utcnow(),
                )
            )
            click.echo(f"  host {host}{' (primary)' if index == 0 else ''}")
        else:
            existing.is_primary = index == 0

    db.session.commit()
    click.echo(f"Done. {church.name} resolves on {len(hosts)} host(s).")


@click.command("add-domain")
@click.option("--slug", required=True)
@click.option("--host", required=True)
@click.option("--primary", is_flag=True, default=False)
@with_appcontext
def add_domain(slug, host, primary):
    """Attach another host to an existing tenant. Used during DNS cutover."""
    church = db.session.query(Church).filter(Church.slug == slug).one_or_none()
    if church is None:
        raise click.ClickException(f"No church with slug {slug}")
    normalized = normalize_host(host)
    if db.session.query(ChurchDomain).filter(ChurchDomain.host == normalized).first():
        raise click.ClickException(f"Host {normalized} is already claimed.")
    if primary:
        for domain in church.domains:
            domain.is_primary = False
    db.session.add(
        ChurchDomain(
            church_id=church.id,
            host=normalized,
            is_primary=primary,
            verified_at=utcnow(),
        )
    )
    db.session.commit()
    click.echo(f"{normalized} now resolves to {church.name}")


@click.command("list-churches")
@with_appcontext
def list_churches():
    """Show every tenant and the hosts that reach it."""
    churches = db.session.query(Church).order_by(Church.slug).all()
    if not churches:
        click.echo("No churches yet. Run: flask seed-church")
        return
    for church in churches:
        click.echo(f"{church.slug:12} {church.name:32} {church.status:9} {church.accent_hex}")
        for domain in church.domains:
            flag = "primary" if domain.is_primary else "alias"
            click.echo(f"  {flag:8} {domain.host}")


@click.command("check-boot")
@with_appcontext
def check_boot():
    """Verify the five increment 0 invariants against the running config."""
    from flask import current_app
    from sqlalchemy import text

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"] or ""
    checks = [
        ("database URL scheme normalized", not uri.startswith("postgres://")),
        ("app env resolved", bool(current_app.config.get("APP_ENV"))),
        ("platform domain set", bool(current_app.config.get("PLATFORM_DOMAIN"))),
    ]
    try:
        db.session.execute(text("SELECT 1"))
        checks.append(("database reachable", True))
    except Exception:  # noqa: BLE001
        checks.append(("database reachable", False))

    ok = True
    for label, passed in checks:
        click.echo(f"[{'ok ' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    if not ok:
        raise SystemExit(1)
