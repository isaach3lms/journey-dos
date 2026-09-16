

class TestReadyzReportsPendingMigrations:
    """Pre-deploy is temporarily off, so nothing forces migrations to run
    before traffic arrives. A half-deployed state has to be visible somewhere
    rather than waiting to surface on the one screen that needs the missing
    column.
    """

    HOST = "journey.dos.test"

    def test_a_schema_built_directly_is_unknown_not_pending(self, client):
        """The test harness and `flask init-db` build tables directly, so
        there is no revision to compare. Reporting that as behind would cry
        wolf often enough that nobody would trust the real signal."""
        response = client.get("/readyz", headers={"Host": self.HOST})
        assert response.status_code == 200
        assert response.get_json()["migrations"]["pending"] is None

    def test_it_degrades_when_a_migration_is_pending(self, app, db, client):
        from sqlalchemy import text

        db.session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        db.session.execute(
            text("INSERT INTO alembic_version VALUES ('875f26d70811')")
        )
        db.session.commit()

        response = client.get("/readyz", headers={"Host": self.HOST})
        assert response.status_code == 503
        payload = response.get_json()
        assert payload["migrations"]["pending"] is True
        assert "flask db upgrade" in payload["fix"]

    def test_the_render_health_check_does_not_use_readyz(self):
        """`/readyz` returning 503 must not fail a deploy. Render checks
        `/healthz`, which answers even when the database is down."""
        from pathlib import Path

        render = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()
        assert "healthCheckPath: /healthz" in render
        assert "healthCheckPath: /readyz" not in render

    def test_healthz_still_answers_regardless(self, client):
        """Render checks this one, and it must not depend on the database."""
        assert client.get("/healthz", headers={"Host": self.HOST}).status_code == 200


class TestMigrationsRunOnDeploy:
    """Pre-deploy was off for a week, and in that week two deploys shipped
    code whose schema had not been applied.

    Both looked like a 500 on one tab with nothing to connect it to a schema
    change. This asserts the blueprint does not quietly leave it off again.
    """

    def test_the_web_service_runs_migrations_before_traffic(self):
        from pathlib import Path

        render = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()
        active = [
            line for line in render.splitlines()
            if "preDeployCommand" in line and not line.strip().startswith("#")
        ]
        assert active, "preDeployCommand is commented out"
        assert "flask db upgrade" in active[0]
