import os

import click
from flask import Flask, render_template, request

from .brand import BRAND, css_variables
from .extensions import csrf, db, login_manager


def _database_uri() -> str:
    uri = os.environ.get("DATABASE_URL", "")
    if uri.startswith("postgres://"):  # Render hands out the legacy scheme
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri or "sqlite:///journey.db"


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

    @app.cli.command("init-db")
    def init_db():
        """Create tables and seed the tenant church and journey stages."""
        from .seed import seed

        db.create_all()
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
