"""Command line entry points.

`seed-tenants` is the proof that adding a church is a row. It is idempotent,
so running it twice updates rather than duplicates.

`build-error-pages` writes the static 500 page from the brand tokens. The
served file has to be database free, but its colors should still originate in
`app/brand.py` rather than being pasted into HTML by hand. Generating it keeps
one source of truth without making the error path depend on a query.
"""

from __future__ import annotations

from pathlib import Path

import click
from flask import current_app

from app.brand import PALETTES, assert_accent_readable, brand_css_vars, palette_for
from app.content import ERRORS
from app.extensions import db
from app.models import Church

# The tenants shipped with the repo. A new client is a new entry here, or a
# single `flask add-church` call. Neither is a migration.
SEED_TENANTS = [
    {
        "slug": "journey",
        "name": "The Journey Church",
        "city": "Jackson, MO",
        "palette_key": "journey",
        "accent_hex": "#485B38",
        "logo_reversed_path": "img/journey-logo-white.png",
        "app_name": "The Journey Church",
        "app_domain": "app.thejourneychurchsemo.com",
        "custom_domain": None,
    },
    {
        "slug": "riverbend",
        "name": "Riverbend Fellowship",
        "city": "Aurora, IL",
        "palette_key": "between-sundays",
        "accent_hex": None,
        "logo_reversed_path": None,
        "app_name": "Riverbend Church",
        "app_domain": "app.riverbendchurch.org",
        "custom_domain": None,
    },
]


def _upsert(data: dict) -> tuple[Church, bool]:
    slug = Church.validate_slug(data["slug"])
    if data.get("accent_hex"):
        assert_accent_readable(data["accent_hex"])

    church = db.session.scalar(db.select(Church).where(Church.slug == slug))
    created = church is None
    if created:
        church = Church(slug=slug)
        db.session.add(church)

    for key, value in data.items():
        if key == "slug":
            continue
        setattr(church, key, value)
    return church, created


def register_cli(app) -> None:

    @app.cli.command("init-db")
    def init_db():
        """Create every table. Use migrations in production, this for a fresh start."""
        db.create_all()
        click.echo("Tables created.")

    @app.cli.command("seed-tenants")
    def seed_tenants():
        """Insert or update the churches defined in SEED_TENANTS."""
        for data in SEED_TENANTS:
            church, created = _upsert(data)
            click.echo(f"{'Created' if created else 'Updated'}  {church.slug:12} {church.name}")
        db.session.commit()
        click.echo(f"\n{len(SEED_TENANTS)} churches on file. Adding one more is a row.")

    @app.cli.command("add-church")
    @click.option("--slug", required=True, help="Subdomain label, lowercase.")
    @click.option("--name", required=True)
    @click.option("--city", default=None)
    @click.option("--palette", "palette_key", default="between-sundays",
                  type=click.Choice(sorted(PALETTES)))
    @click.option("--accent", "accent_hex", default=None, help="Hex, must reach 4.5:1 on white.")
    def add_church(slug, name, city, palette_key, accent_hex):
        """Add one church. No migration, no deploy, no branch."""
        church, created = _upsert(
            {
                "slug": slug,
                "name": name,
                "city": city,
                "palette_key": palette_key,
                "accent_hex": accent_hex,
            }
        )
        db.session.commit()
        click.echo(f"{'Created' if created else 'Updated'} {church.slug}: {church.name}")

    @app.cli.command("list-churches")
    def list_churches():
        for church in db.session.scalars(db.select(Church).order_by(Church.slug)):
            click.echo(
                f"{church.slug:12} {church.name:26} "
                f"{church.palette_key:16} {church.accent_hex or '-'}"
            )

    @app.cli.command("build-error-pages")
    @click.option("--palette", "palette_key", default="journey",
                  type=click.Choice(sorted(PALETTES)))
    def build_error_pages(palette_key):
        """Write app/static/500.html from the brand tokens.

        The served page must not touch the database, so it cannot read a
        church row at request time. Generating it here keeps brand.py as the
        single source of the colors without adding a query to the error path.
        """
        palette = PALETTES[palette_key]

        class _Stub:
            palette_key = None
            accent_hex = None
            logo_reversed_path = None

        stub = _Stub()
        stub.palette_key = palette_key
        css = brand_css_vars(stub)

        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{ERRORS['500_title']}</title>
<!--
  Generated by `flask build-error-pages`. Do not edit by hand.
  This page is served as a static file with no template inheritance, no
  context processor, and no database session, because the most common cause
  of a 500 is the database being unreachable.
-->
<style>
:root{{ {css} }}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0}}
body{{
  font-family:{palette.font_body};
  background:var(--bone);color:var(--ink);
  min-height:100vh;display:grid;place-items:center;padding:30px;
}}
.card{{
  background:var(--white);border:1px solid var(--line-soft);
  border-radius:var(--r-lg);box-shadow:var(--shadow-md);
  padding:34px;max-width:460px;
}}
h1{{
  font-family:{palette.font_display};
  font-size:20px;font-weight:600;letter-spacing:-.02em;margin:0 0 10px;
}}
p{{font-size:13.5px;line-height:1.6;color:var(--muted);margin:0 0 20px}}
a{{
  display:inline-block;background:var(--accent);color:#fff;
  font-size:13px;font-weight:500;text-decoration:none;
  padding:9px 15px;border-radius:10px;
}}
</style>
</head>
<body>
  <div class="card">
    <h1>{ERRORS['500_title']}</h1>
    <p>{ERRORS['500_body']}</p>
    <a href="/">Go back</a>
  </div>
</body>
</html>
"""
        target = Path(current_app.static_folder) / "500.html"
        target.write_text(html, encoding="utf-8")
        click.echo(f"Wrote {target} using the {palette_key} palette.")

    @app.cli.command("check-contrast")
    def check_contrast():
        """Report the contrast of every palette against white text."""
        from app.brand import contrast

        for key, p in PALETTES.items():
            accent = contrast(p.green, "#FFFFFF")
            mark = "pass" if accent >= 4.5 else "FAIL"
            click.echo(f"{key:16} accent {p.green} on white {accent:5.2f}:1  {mark}")
            click.echo(f"{'':16} gold   {p.gold} on white {contrast(p.gold, '#FFFFFF'):5.2f}:1  never carries text")
            click.echo(f"{'':16} chrome {p.deep} on white {contrast(p.deep, '#FFFFFF'):5.2f}:1")
