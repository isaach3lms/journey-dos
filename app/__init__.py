import os

import click
from flask import Flask, render_template, request

from .brand import BRAND, css_variables
from .extensions import csrf, db, login_manager, migrate


class ConfigError(RuntimeError):
    """Raised at boot when configuration would silently lose data."""


def _database_uri() -> str:
    uri = os.environ.get("DATABASE_URL", "")
    if uri.startswith("postgres://"):  # Render hands out the legacy scheme
        uri = uri.replace("postgres://", "postgresql+psycopg2://", 1)
    elif uri.startswith("postgresql://"):
        # Name the driver explicitly. Without it SQLAlchemy picks whatever
        # DBAPI is installed, which changes under us the day psycopg 3 lands
        # in the image.
        uri = uri.replace("postgresql://", "postgresql+psycopg2://", 1)

    if not uri:
        if os.environ.get("RENDER"):
            # The dangerous case. Falling back to SQLite here would accept
            # every person, gift, and check-in written that day onto Render's
            # ephemeral disk, then lose all of it on the next deploy, with no
            # error anywhere and no way to get it back.
            raise ConfigError(
                "DATABASE_URL is not set. Refusing to boot on Render. "
                "A SQLite fallback would accept writes and lose them on the "
                "next deploy. Check the database binding in render.yaml."
            )
        return "sqlite:///journey.db"
    return uri


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=_database_uri(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        CHURCH_SLUG=os.environ.get("CHURCH_SLUG", "journey"),
        # Giving
        TITHELY_FORM_ID=os.environ.get("TITHELY_FORM_ID", ""),
        TITHELY_GIVE_URL=os.environ.get("TITHELY_GIVE_URL", ""),
        # Email
        RESEND_API_KEY=os.environ.get("RESEND_API_KEY", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", "website@thejourneychurchsemo.com"),
        NOTIFY_TO=os.environ.get("NOTIFY_TO", "hello@thejourneychurchsemo.com"),
        SITE_URL=os.environ.get("SITE_URL", "https://thejourneychurchsemo.com"),
        KIOSK_PIN=os.environ.get("KIOSK_PIN", "1012"),
        # The church's existing public website. The DOS links back to it and
        # only accepts intake posts from this origin.
        PUBLIC_SITE_URL=os.environ.get(
            "PUBLIC_SITE_URL", "https://thejourneychurchsemo.com"
        ),
        INTAKE_TOKEN=os.environ.get("INTAKE_TOKEN", ""),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
    )
    if os.environ.get("RENDER"):
        app.config["SESSION_COOKIE_SECURE"] = True

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import ministry, models  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.Person, int(user_id))

    from .blueprints.auth import bp as auth_bp
    from .blueprints.kiosk import bp as kiosk_bp
    from .blueprints.ministry import bp as ministry_bp
    from .blueprints.portal import bp as portal_bp
    from .blueprints.public import bp as public_bp
    from .blueprints.staff import bp as staff_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(ministry_bp)
    app.register_blueprint(kiosk_bp)

    @app.context_processor
    def inject_brand():
        return {"brand": BRAND, "brand_css": css_variables()}

    @app.after_request
    def frame_policy(response):
        """Only the church's own website may frame the embedded form. Every
        other page in the DOS refuses to be framed at all."""
        if request.path.startswith("/embed/"):
            origin = (app.config.get("PUBLIC_SITE_URL") or "").rstrip("/")
            response.headers.pop("X-Frame-Options", None)
            response.headers["Content-Security-Policy"] = (
                f"frame-ancestors 'self' {origin}" if origin else "frame-ancestors 'self'"
            )
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.errorhandler(404)
    def not_found(_):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(error):
        """The one page that has to work when the database does not.

        500.html extends nothing and reads no template context on purpose.
        base.html renders the masthead and pulls `brand` from a context
        processor, and every other page hits the session. If this handler
        rendered any of that, a Postgres outage would raise inside the error
        handler itself and a pastor would get a bare Gunicorn error instead
        of a page that tells them what is happening.

        Do not "tidy this up" by making 500.html extend base.html. There is
        a test that fails if you do.
        """
        app.logger.exception("Unhandled error: %s", error)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001 - the session may already be dead
            pass
        return render_template("500.html"), 500

    @app.cli.command("init-db")
    def init_db():
        """Bring the schema up to date and seed the tenant church and stages.

        Schema now comes from migrations, not db.create_all(). create_all
        creates missing tables but never alters existing ones, so every
        column added from here on would have silently failed to appear on
        the deployed Postgres while passing locally against a fresh SQLite
        file.

        On a database that already has the phase 1 tables but no
        alembic_version row, run `flask db stamp head` once first. See
        DEPLOY.md.
        """
        from flask_migrate import upgrade

        from .seed import seed

        upgrade()
        seed()
        print("Database ready.")

    @app.cli.command("grant-access")
    @click.argument("email")
    @click.option(
        "--role",
        default="admin",
        type=click.Choice(["member", "leader", "staff", "admin", "support"]),
        help="support is the vendor account, hidden from all congregation reports.",
    )
    @click.option("--first", default="", help="First name, for a new record.")
    @click.option("--last", default="", help="Last name, for a new record.")
    def grant_access(email, role, first, last):
        """Create or promote an account and print a one time set password link.

        No password is set here and none is printed. The link is single use and
        expires in 48 hours, same as every other account link in the system.
        """
        from .models import Church, Person, issue_token

        email = email.strip().lower()
        church = Church.query.filter_by(slug=app.config["CHURCH_SLUG"]).first()
        if not church:
            print("No church found. Run init-db first.")
            return

        person = Person.query.filter_by(church_id=church.id, email=email).first()
        if person:
            was = person.role
            person.role = role
            print(f"Updated {person.full_name}: {was} to {role}")
        else:
            person = Person(
                church_id=church.id,
                first_name=first or email.split("@")[0],
                last_name=last,
                email=email,
                role=role,
                source="granted by CLI",
            )
            db.session.add(person)
            db.session.flush()
            print(f"Created {person.full_name} as {role}")

        raw, _ = issue_token(person, "reset" if person.password_hash else "claim")
        db.session.commit()
        base = (app.config.get("SITE_URL") or "").rstrip("/")
        print(f"\nSet a password here, once, within 48 hours:\n{base}/account/set-password/{raw}\n")

    @app.cli.command("run-automations")
    def run_automations():
        """Send every follow up step that is due. Run daily from Render cron."""
        from .automations import run_sequences

        result = run_sequences(app.config["SITE_URL"])
        print(
            f"sent={result['sent']} stopped={result['stopped']} "
            f"completed={result['completed']}"
        )

    @app.cli.command("send-digest")
    def send_digest():
        """Email staff the weekly stuck and never contacted report."""
        from .automations import staff_digest
        from .models import Church

        church = Church.query.filter_by(slug=app.config["CHURCH_SLUG"]).first()
        if not church:
            print("No church found.")
            return
        ok = staff_digest(church.id, app.config["NOTIFY_TO"])
        print("digest sent" if ok else "digest failed, check RESEND_API_KEY")

    return app
