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
from app.models import Church, User
from app.models.user import ROLES

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

    # -- users --------------------------------------------------------------

    @app.cli.command("create-user")
    @click.option("--church", "church_slug", required=True)
    @click.option("--email", required=True)
    @click.option("--name", required=True)
    @click.option("--role", default="member", type=click.Choice(ROLES))
    @click.password_option("--password", confirmation_prompt=True)
    def create_user(church_slug, email, name, role, password):
        """Create a login. Passwords are prompted, never passed as an argument.

        A password on the command line lands in shell history and in the
        process list, where any other user on the machine can read it.
        """
        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        email = email.strip().lower()
        if User.by_email(church.id, email) is not None:
            raise click.ClickException(
                f"{email} already has an account at {church.name}. "
                f"Use `flask set-password` to change it."
            )

        user = User(church_id=church.id, email=email, name=name, role=role)
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {role} {email} at {church.name}.")

    @app.cli.command("set-password")
    @click.option("--church", "church_slug", required=True)
    @click.option("--email", required=True)
    @click.password_option("--password", confirmation_prompt=True)
    def set_password(church_slug, email, password):
        """Reset one user's password.

        This is how a password gets reset until increment 4 ships the outbox
        and self-serve reset by email becomes possible.
        """
        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        user = User.by_email(church.id, email)
        if user is None:
            raise click.ClickException(f"No account for {email} at {church.name}.")

        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        db.session.commit()
        click.echo(f"Password reset for {email}. The lockout counter is cleared.")

    @app.cli.command("list-users")
    @click.option("--church", "church_slug", default=None)
    def list_users(church_slug):
        query = db.select(User).join(Church).order_by(Church.slug, User.role, User.email)
        if church_slug:
            query = query.where(Church.slug == church_slug)
        for user in db.session.scalars(query):
            state = "active" if user.is_active_account else "deactivated"
            lock = " LOCKED" if user.is_locked else ""
            last = user.last_login_at.strftime("%Y-%m-%d") if user.last_login_at else "never"
            click.echo(
                f"{user.church.slug:12} {user.role:8} {user.email:36} "
                f"{state:12} last login {last}{lock}"
            )

    @app.cli.command("unlock-user")
    @click.option("--church", "church_slug", required=True)
    @click.option("--email", required=True)
    def unlock_user(church_slug, email):
        """Clear a lockout without changing the password."""
        church = Church.by_slug(church_slug)
        user = User.by_email(church.id, email) if church else None
        if user is None:
            raise click.ClickException(f"No account for {email} at {church_slug}.")
        user.failed_login_count = 0
        user.locked_until = None
        db.session.commit()
        click.echo(f"Unlocked {email}.")

    @app.cli.command("set-domain")
    @click.option("--church", "church_slug", required=True)
    @click.option("--domain", required=True,
                  help="Bare host, no scheme and no path. e.g. app.example.org")
    def set_domain(church_slug, domain):
        """Point a hostname at a church.

        Until a platform domain exists there are no tenant subdomains to read,
        so production reaches a church by exact host. This is what makes the
        Render URL resolve to Journey instead of returning 404.
        """
        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        host = domain.strip().lower()
        for bad in ("http://", "https://", "/"):
            if bad in host:
                raise click.ClickException(
                    f"{domain!r} is not a bare host. Drop the scheme and any path."
                )

        taken = Church.by_custom_domain(host)
        if taken is not None and taken.id != church.id:
            raise click.ClickException(
                f"{host} already points at {taken.name}. One host, one church."
            )

        church.custom_domain = host
        db.session.commit()
        click.echo(f"{host} now resolves to {church.name}.")

    @app.cli.command("routing-check")
    def routing_check():
        """Show exactly which hosts resolve to a church, and which do not."""
        platform = app.config.get("PLATFORM_DOMAIN") or ""
        churches = list(db.session.scalars(db.select(Church).order_by(Church.slug)))

        click.echo(f"PLATFORM_DOMAIN: {platform or '(not set)'}")
        click.echo(f"Query override:  {'on' if app.config.get('ALLOW_TENANT_QUERY_OVERRIDE') else 'off'}")
        click.echo("")

        reachable = 0
        for church in churches:
            hosts = []
            if church.custom_domain:
                hosts.append(church.custom_domain)
            if platform:
                hosts.append(f"{church.slug}.{platform}")
            if hosts:
                reachable += 1
            click.echo(f"{church.slug:12} {' , '.join(hosts) or 'NO HOST RESOLVES TO THIS CHURCH'}")

        if reachable == 0:
            click.echo("")
            click.echo(
                "Nothing is reachable. Every request will 404. Fix with either:\n"
                "  flask set-domain --church journey --domain <your-host>\n"
                "or by setting PLATFORM_DOMAIN and adding wildcard DNS."
            )

    # -- people -------------------------------------------------------------

    @app.cli.command("import-people")
    @click.option("--church", "church_slug", required=True)
    @click.option("--file", "path", required=True, type=click.Path(exists=True))
    @click.option("--dry-run", is_flag=True, help="Report what would happen, write nothing.")
    def import_people(church_slug, path, dry_run):
        """Import a roster from CSV.

        Expected columns, header row required:
          first_name, last_name, email, phone, stage, household, first_seen_on
        Optional:
          last_contact_on

        `last_contact_on` matters more than it looks. Without it every imported
        person reads as never contacted, and on day one the stuck engine flags
        most of the roster. A church migrating off Planning Center has this
        date; bring it across.

        Every row is validated before a single row is written. A file with one
        bad stage value fails entirely rather than importing 340 people and
        leaving a church to work out which 12 are missing.
        """
        import csv
        from datetime import date as _date
        from datetime import datetime, time, timezone

        from app.models import KIND_IMPORTED, Household, Person, PersonEvent
        from app.stages import FIRST_STAGE, STAGE_CODES

        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))

        if not rows:
            raise click.ClickException("That file has no rows.")

        problems, staged = [], []
        seen_emails = set()

        for line, row in enumerate(rows, start=2):
            first = (row.get("first_name") or "").strip()
            last = (row.get("last_name") or "").strip()
            if not first or not last:
                problems.append(f"line {line}: needs both a first and last name")
                continue

            stage = (row.get("stage") or FIRST_STAGE).strip().lower()
            if stage not in STAGE_CODES:
                problems.append(
                    f"line {line}: {stage!r} is not a stage. "
                    f"Use one of: {', '.join(STAGE_CODES)}"
                )
                continue

            email = (row.get("email") or "").strip().lower() or None
            if email:
                if email in seen_emails:
                    problems.append(f"line {line}: {email} appears twice in this file")
                    continue
                seen_emails.add(email)

            first_seen = None
            raw_date = (row.get("first_seen_on") or "").strip()
            if raw_date:
                try:
                    first_seen = _date.fromisoformat(raw_date)
                except ValueError:
                    problems.append(f"line {line}: {raw_date!r} is not a date, use YYYY-MM-DD")
                    continue

            last_contact = None
            raw_contact = (row.get("last_contact_on") or "").strip()
            if raw_contact:
                try:
                    last_contact = _date.fromisoformat(raw_contact)
                except ValueError:
                    problems.append(
                        f"line {line}: {raw_contact!r} is not a date, use YYYY-MM-DD"
                    )
                    continue

            staged.append(
                {
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "phone": (row.get("phone") or "").strip() or None,
                    "stage": stage,
                    "household": (row.get("household") or "").strip() or None,
                    "first_seen_on": first_seen,
                    "last_contact_on": last_contact,
                }
            )

        if problems:
            click.echo(f"{len(problems)} problems. Nothing was written.\n")
            for problem in problems[:25]:
                click.echo(f"  {problem}")
            if len(problems) > 25:
                click.echo(f"  ... and {len(problems) - 25} more")
            raise click.ClickException("Fix the file and run it again.")

        created = updated = 0
        for record in staged:
            household = None
            if record["household"]:
                household = Household.find_or_create(church.id, record["household"])
                db.session.flush()

            existing = None
            if record["email"]:
                existing = db.session.scalar(
                    db.select(Person).where(
                        Person.church_id == church.id,
                        Person.email == record["email"],
                    )
                )

            if existing is not None:
                existing.first_name = record["first_name"]
                existing.last_name = record["last_name"]
                existing.phone = record["phone"] or existing.phone
                if household is not None:
                    existing.household_id = household.id
                if record["last_contact_on"]:
                    imported_contact = datetime.combine(
                        record["last_contact_on"], time(12, 0), tzinfo=timezone.utc
                    )
                    # Only ever forward, so a re-import with stale dates cannot
                    # make someone look less recently contacted than they are.
                    if (
                        existing.last_contact_at is None
                        or imported_contact > existing.last_contact_at
                    ):
                        existing.last_contact_at = imported_contact
                updated += 1
                continue

            # An imported roster carries no stage history, so first_seen_on is
            # the best available proxy for when someone entered the stage they
            # are in now. Defaulting to the import timestamp instead would tell
            # every pastor that all 54 of their people arrived this morning,
            # and would make increment 3's stuck engine blind for months.
            stage_since = None
            if record["first_seen_on"]:
                stage_since = datetime.combine(
                    record["first_seen_on"], time(12, 0), tzinfo=timezone.utc
                )

            person = Person(
                church_id=church.id,
                first_name=record["first_name"],
                last_name=record["last_name"],
                email=record["email"],
                phone=record["phone"],
                stage=record["stage"],
                first_seen_on=record["first_seen_on"],
                household_id=household.id if household is not None else None,
                **({"stage_since": stage_since} if stage_since else {}),
                **(
                    {
                        "last_contact_at": datetime.combine(
                            record["last_contact_on"], time(12, 0), tzinfo=timezone.utc
                        )
                    }
                    if record["last_contact_on"]
                    else {}
                ),
            )
            db.session.add(person)
            db.session.flush()
            PersonEvent.record(
                person, KIND_IMPORTED, f"Imported at {person.stage_label}"
            )
            created += 1

        if dry_run:
            db.session.rollback()
            click.echo(f"Dry run. Would create {created} and update {updated}. Nothing written.")
            return

        db.session.commit()
        click.echo(f"Created {created}, updated {updated}, at {church.name}.")

    @app.cli.command("people-summary")
    @click.option("--church", "church_slug", required=True)
    def people_summary(church_slug):
        """Stage counts for one church, the same numbers the rail shows."""
        from app.models import Person
        from app.stages import stages_for

        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        counts = Person.stage_counts(church.id)
        total = Person.total_for_church(church.id)
        click.echo(f"{church.name}: {total} people\n")
        for stage in stages_for(church):
            count = counts.get(stage.code, 0)
            share = f"{count / total * 100:4.1f}%" if total else "   -"
            click.echo(f"  {stage.label:11} {count:4}  {share}")

    # -- increment 3 --------------------------------------------------------

    @app.cli.command("stuck")
    @click.option("--church", "church_slug", required=True)
    def stuck(church_slug):
        """Who is stuck, and why. The same answer the dashboard shows."""
        from app.models import Person
        from app.stages import CONTACT_WINDOW_DAYS

        church = Church.by_slug(church_slug)
        if church is None:
            raise click.ClickException(f"No church with slug {church_slug!r}.")

        people = db.session.scalars(Person.stuck(church.id)).all()
        if not people:
            click.echo(
                f"Nobody is stuck at {church.name}. Everyone past their stage's "
                f"expected time has been contacted within {CONTACT_WINDOW_DAYS} days."
            )
            return

        click.echo(f"{len(people)} flagged at {church.name}\n")
        for person in people:
            owner = person.owner_name or "no owner"
            click.echo(f"  {person.full_name:26} {person.stuck_reason}  ({owner})")

    @app.cli.command("recompute-contact")
    @click.option("--church", "church_slug", default=None)
    def recompute_contact(church_slug):
        """Rebuild person.last_contact_at from the contact log.

        The column is denormalized so the stuck query stays a range scan
        instead of a join and a group by. Denormalized data drifts, so the
        rebuild path exists and is cheap. Run it after any bulk import or
        direct database edit.
        """
        from sqlalchemy import func as sa_func

        from app.models import ContactLog, Person

        query = db.select(Person)
        if church_slug:
            church = Church.by_slug(church_slug)
            if church is None:
                raise click.ClickException(f"No church with slug {church_slug!r}.")
            query = query.where(Person.church_id == church.id)

        changed = 0
        for person in db.session.scalars(query):
            latest = db.session.scalar(
                db.select(sa_func.max(ContactLog.occurred_at)).where(
                    ContactLog.church_id == person.church_id,
                    ContactLog.person_id == person.id,
                )
            )
            if person.last_contact_at != latest:
                person.last_contact_at = latest
                changed += 1

        db.session.commit()
        click.echo(f"Rebuilt {changed} rows from the contact log.")

    # -- increment 4 --------------------------------------------------------

    @app.cli.command("send-outbox")
    @click.option("--church", "church_slug", default=None)
    @click.option("--limit", default=None, type=int)
    def send_outbox(church_slug, limit):
        """Send what is queued. This is the worker.

        Run it on a schedule. On Render that is a Cron Job hitting the same
        image, not a thread inside the web service: a thread dies with the
        process and takes the queue's progress with it.
        """
        from app.mail import send_pending

        church_id = None
        if church_slug:
            church = Church.by_slug(church_slug)
            if church is None:
                raise click.ClickException(f"No church with slug {church_slug!r}.")
            church_id = church.id

        counts = send_pending(
            limit=limit or current_app.config.get("OUTBOX_BATCH_SIZE", 50),
            church_id=church_id,
        )
        click.echo(
            f"sent {counts['sent']}, "
            f"opted out {counts['suppressed']}, "
            f"retrying {counts['retrying']}, "
            f"failed {counts['failed']}"
        )

    @app.cli.command("outbox-status")
    @click.option("--church", "church_slug", default=None)
    def outbox_status(church_slug):
        """What is in the outbox, by status."""
        from sqlalchemy import func as sa_func

        from app.models import OutboxMessage

        query = db.select(
            OutboxMessage.status, sa_func.count(OutboxMessage.id)
        ).group_by(OutboxMessage.status)
        if church_slug:
            church = Church.by_slug(church_slug)
            if church is None:
                raise click.ClickException(f"No church with slug {church_slug!r}.")
            query = query.where(OutboxMessage.church_id == church.id)

        rows = db.session.execute(query).all()
        if not rows:
            click.echo("The outbox is empty.")
            return
        for status, count in sorted(rows):
            click.echo(f"  {status:12} {count}")

        stuck = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.status == "failed").limit(5)
        ).all()
        if stuck:
            click.echo("\nGave up on these. They are kept, not deleted:")
            for message in stuck:
                click.echo(f"  {message.to_email:34} {(message.last_error or '')[:70]}")

    @app.cli.command("release-claims")
    @click.option("--minutes", default=15, help="Older than this many minutes.")
    def release_claims(minutes):
        """Return rows claimed by a worker that died before finishing.

        A crash between claiming and sending leaves a row stamped with a token
        and no worker to act on it. Without this it sits queued forever and
        nobody is told.
        """
        from datetime import timedelta

        from app.models import STATUS_QUEUED, OutboxMessage
        from app.models.base import utcnow

        cutoff = utcnow() - timedelta(minutes=minutes)
        released = db.session.execute(
            db.update(OutboxMessage)
            .where(
                OutboxMessage.status == STATUS_QUEUED,
                OutboxMessage.claim_token.is_not(None),
                OutboxMessage.claimed_at < cutoff,
            )
            .values(claim_token=None, claimed_at=None)
        ).rowcount
        db.session.commit()
        click.echo(f"Released {released} abandoned claims.")
