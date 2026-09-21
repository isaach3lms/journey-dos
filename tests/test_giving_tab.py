"""The staff Giving tab opens the church's giving platform directly."""

import subprocess
import sys

from app.models import Church
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
TITHELY = "https://auth.tithely.com/login"


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def test_tab_opens_tithely_in_a_new_tab(client, sign_in, db):
    journey(db).giving_admin_url = TITHELY
    db.session.commit()
    sign_in("pastor@journeychurchsemo.com")
    page = client.get("/settings/", headers=H).data.decode()
    link = page[page.index(f'href="{TITHELY}"') - 40: page.index(f'href="{TITHELY}"') + 200]
    assert 'class="nav"' in link
    assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link


def test_without_a_link_the_tab_stays_inside_the_app(client, sign_in, db):
    journey(db).giving_admin_url = None
    db.session.commit()
    sign_in("pastor@journeychurchsemo.com")
    page = client.get("/settings/", headers=H).data.decode()
    assert 'href="/giving/"' in page
    assert TITHELY not in page


def test_setup_page_is_still_reachable_from_settings(client, sign_in, db):
    journey(db).giving_admin_url = TITHELY
    db.session.commit()
    sign_in("pastor@journeychurchsemo.com")
    page = client.get("/settings/", headers=H).data
    assert b"Giving setup and reports" in page
    assert client.get("/giving/", headers=H).status_code == 200


def test_the_link_passes_the_tithely_check():
    from app.giving import validate_giving_url

    assert validate_giving_url(TITHELY) == TITHELY


def test_other_churches_keep_their_own(client, sign_in, db):
    other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
    assert other.giving_admin_url in (None, "")


def test_the_migration_sets_journey(tmp_path):
    import os
    import sqlite3

    database = tmp_path / "m.sqlite"
    env = dict(os.environ, FLASK_APP="wsgi.py", FLASK_ENV="development",
               DATABASE_URL=f"sqlite:///{database}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = lambda *a: subprocess.run([sys.executable, "-m", "flask", *a], cwd=root, env=env,
                                    capture_output=True, text=True, check=True)
    run("db", "upgrade", "6dad82908131")
    con = sqlite3.connect(database)
    con.execute("INSERT INTO church (slug, name, palette_key, is_active, allow_self_signup, "
                "members_can_announce, created_at, updated_at, giving_admin_url) VALUES "
                "('journey','The Journey Church','journey',1,0,1,'2026-01-01','2026-01-01','https://tithe.ly/old'),"
                "('riverbend','Riverbend','journey',1,0,1,'2026-01-01','2026-01-01',NULL)")
    con.commit()
    con.close()
    run("db", "upgrade")
    con = sqlite3.connect(database)
    rows = dict(con.execute("SELECT slug, giving_admin_url FROM church"))
    assert rows == {"journey": TITHELY, "riverbend": None}
