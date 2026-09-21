"""Boot rules: URL normalization and the production hard fail."""

import pytest

from app.config import MissingDatabaseURL, ProductionConfig, normalize_database_url


class TestNormalizeDatabaseURL:
    def test_render_scheme_gets_a_driver(self):
        assert normalize_database_url("postgres://u:p@h:5432/d") == \
            "postgresql+psycopg2://u:p@h:5432/d"

    def test_bare_postgresql_gets_a_driver(self):
        assert normalize_database_url("postgresql://u:p@h/d") == \
            "postgresql+psycopg2://u:p@h/d"

    def test_already_normalized_is_untouched(self):
        url = "postgresql+psycopg2://u:p@h/d"
        assert normalize_database_url(url) == url

    def test_sqlite_is_untouched(self):
        assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"

    def test_none_survives(self):
        assert normalize_database_url(None) is None

    def test_credentials_containing_the_scheme_are_not_mangled(self):
        url = "postgres://user:postgres://@host/db"
        assert normalize_database_url(url).startswith("postgresql+psycopg2://user:")


class TestProductionHardFail:
    def test_missing_database_url_refuses_to_boot(self):
        class FakeApp:
            config = {"SQLALCHEMY_DATABASE_URI": None, "SECRET_KEY": "real-secret"}

        with pytest.raises(MissingDatabaseURL):
            ProductionConfig.init_app(FakeApp())

    def test_default_secret_key_refuses_to_boot(self):
        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "dev-only-not-for-production",
            }

        with pytest.raises(RuntimeError):
            ProductionConfig.init_app(FakeApp())

    def test_valid_production_config_boots(self):
        class FakeApp:
            config = {
                "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u@h/d",
                "SECRET_KEY": "real-secret",
            }

        ProductionConfig.init_app(FakeApp())


class TestMigrationsAreSafeOnPopulatedTables:
    """Local migrations run against an empty database. Production does not.

    A NOT NULL column added with no server default succeeds on an empty table
    and fails on one with rows, so this class of bug is invisible in
    development and fatal at deploy time. That happened once.
    """

    def test_every_not_null_column_added_to_an_existing_table_has_a_default(self):
        import re
        from pathlib import Path

        versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
        offenders = []

        for path in sorted(versions.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            # add_column means the table already exists; create_table does not.
            for match in re.finditer(
                r"add_column\(\s*sa\.Column\((.*?)\)\s*\)", source, re.S
            ):
                column = match.group(1)
                if "nullable=False" in column and "server_default" not in column:
                    offenders.append(f"{path.name}: {column[:90]}")

        assert not offenders, (
            "These add a NOT NULL column to a table that already has rows, "
            "with nothing to put in them:\n" + "\n".join(offenders)
        )


class TestMigrationsCompileOnPostgresToo:
    """Local migrations run on SQLite. Production runs on Postgres.

    SQLite has no boolean type and accepts `DEFAULT 0` on a BOOLEAN column.
    Postgres refuses an integer default on a boolean, so a migration that
    passes locally fails at deploy time. That happened once.
    """

    def test_no_boolean_column_uses_an_integer_default(self):
        import re
        from pathlib import Path

        versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
        offenders = []

        for path in sorted(versions.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            # Any `sa.text('0')` or `sa.text('1')` default anywhere near a
            # Boolean. The first version of this test only matched when the
            # default followed the type on the same line, and autogenerate
            # produces both shapes, so it missed a real one.
            for match in re.finditer(
                r"sa\.Column\(((?:[^()]|\([^()]*\))*)\)", source, re.S
            ):
                block = match.group(1)
                if "sa.Boolean" not in block:
                    continue
                if re.search(r"server_default\s*=\s*sa\.text\(\s*['\"][01]['\"]", block):
                    offenders.append(f"{path.name}: {' '.join(block.split())[:90]}")

        assert not offenders, (
            "These set an integer default on a boolean column, which Postgres "
            "refuses:\n" + "\n".join(offenders)
        )

    def test_the_church_model_agrees(self):
        from app.models import Church

        column = Church.__table__.c.allow_self_signup
        assert column.server_default is not None
        rendered = str(column.server_default.arg).lower()
        assert rendered not in ("0", "1"), rendered


class TestTheBlueprintCanActuallyBoot:
    """A blueprint that only works if a human already knew something is a
    broken blueprint.

    `render.yaml` turned push on while marking the key `sync: false`, so the
    service refused to boot on a step documented nowhere. The boot guard was
    right; the blueprint was wrong.
    """

    def _render(self):
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "render.yaml").read_text()

    def test_push_is_not_enabled_without_keys_in_the_blueprint(self):
        render = self._render()
        # Any service that sets webpush must also set a private key value in
        # the file, which it never should. So webpush must not appear.
        assert "value: webpush" not in render

    def test_no_secret_is_committed_in_the_blueprint(self):
        """Keys belong in the dashboard. A key in git is a rotated key."""
        render = self._render()
        for secret in ("VAPID_PRIVATE_KEY", "RESEND_API_KEY", "SECRET_KEY"):
            for line in render.splitlines():
                if line.strip().startswith(f"- key: {secret}"):
                    continue
            assert f"{secret}=" not in render

    def test_every_secret_env_var_is_sync_false_or_generated(self):
        import re

        render = self._render()
        secrets = ("VAPID_PRIVATE_KEY", "RESEND_API_KEY")
        for secret in secrets:
            for match in re.finditer(
                rf"- key: {secret}\n(.*?)(?=\n      - key:|\n  - type:|\Z)",
                render,
                re.S,
            ):
                block = match.group(1)
                assert "sync: false" in block or "generateValue" in block, secret


class TestAMigratedSchemaMatchesTheModels:
    """Tests build the schema with `create_all`; production builds it with
    migrations. The two can quietly disagree.

    Adding 'header' to the item kinds changed the model but not the CHECK
    constraint already in the database, and Alembic does not diff check
    constraints. Every test passed and the seeded demo blew up.
    """

    def _migrated_session(self, tmp_path):
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        uri = f"sqlite:///{tmp_path / 'migrated.sqlite'}"
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "FLASK_APP": "wsgi.py",
            "FLASK_ENV": "development",
            "DATABASE_URL": uri,
        }
        result = subprocess.run(
            [sys.executable, "-m", "flask", "db", "upgrade"],
            cwd=root, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr[-1500:]
        return uri

    def test_every_check_constraint_accepts_every_value_the_models_allow(
        self, tmp_path
    ):
        import sqlalchemy as sa

        from app.models.service import ITEM_KINDS

        uri = self._migrated_session(tmp_path)
        engine = sa.create_engine(uri)

        with engine.begin() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='service_item'"
                )
            ).scalar()

        # Every kind the model permits must appear in the constraint the
        # migration created, or inserts fail in production only.
        for kind in ITEM_KINDS:
            assert f"'{kind}'" in rows, (
                f"The migrated CHECK constraint on service_item does not allow "
                f"{kind!r}, but the model does."
            )


class TestEveryCheckConstraintSurvivesMigration:
    """Tests build the schema with `create_all` from the models. Production
    builds it with migrations. Alembic does not diff CHECK constraints, so the
    two drift silently whenever an allowed value is added.

    It has happened twice: section headers in service plans, then a new
    notification category that made every staff report alert fail with an
    IntegrityError. Both passed the whole suite and failed in a running app.
    The earlier guard checked only the constraint that had bitten; this one
    checks all of them.
    """

    def test_the_migrated_schema_carries_every_model_constraint(self, tmp_path):
        import re
        import subprocess
        import sys
        from pathlib import Path

        import sqlalchemy as sa

        from app.extensions import db

        root = Path(__file__).resolve().parent.parent
        uri = f"sqlite:///{tmp_path / 'migrated.sqlite'}"
        result = subprocess.run(
            [sys.executable, "-m", "flask", "db", "upgrade"],
            cwd=root,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "FLASK_APP": "wsgi.py",
                 "FLASK_ENV": "development", "DATABASE_URL": uri},
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr[-1500:]

        with sa.create_engine(uri).connect() as connection:
            ddl = {
                name: sql for name, sql in connection.execute(
                    sa.text("SELECT name, sql FROM sqlite_master WHERE type='table'")
                )
            }

        def norm(text) -> str:
            return re.sub(r"[\s\"'()]", "", str(text)).lower()

        drifted = [
            f"{table.name}: {str(constraint.sqltext)[:80]}"
            for table in db.metadata.sorted_tables
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
            and norm(constraint.sqltext) not in norm(ddl.get(table.name, ""))
        ]
        assert not drifted, (
            "The models allow values the migrated database refuses. Replace "
            "these constraints in a migration:\n" + "\n".join(drifted)
        )
