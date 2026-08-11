import os

from flask import Flask, render_template

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
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
    )
    if os.environ.get("RENDER"):
        app.config["SESSION_COOKIE_SECURE"] = True

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.Person, int(user_id))

    from .blueprints.auth import bp as auth_bp
    from .blueprints.portal import bp as portal_bp
    from .blueprints.public import bp as public_bp
    from .blueprints.staff import bp as staff_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(staff_bp)

    @app.context_processor
    def inject_brand():
        return {"brand": BRAND, "brand_css": css_variables()}

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
