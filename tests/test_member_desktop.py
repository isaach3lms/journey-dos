"""The member app on a computer.

Members on a desktop get a full-width layout with the tabs across the top.
Staff previewing the member app keep the phone frame, because that preview
exists to show what most members see.
"""

from pathlib import Path

from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text()


def link_member(db, email):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    user = db.session.scalar(db.select(User).where(User.church_id == church.id, User.email == email))
    person = Person(church_id=church.id, first_name="Alicia", last_name="Romero",
                    email=email, stage="visitor", first_seen_on=utcnow().date())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()


def test_members_get_the_full_width_layout(client, sign_in, db):
    link_member(db, "member@journeychurchsemo.com")
    sign_in("member@journeychurchsemo.com")
    page = client.get("/me/", headers={"Host": JOURNEY_HOST})
    assert page.status_code == 200
    assert b'class="memberbody memberfull"' in page.data
    assert b'class="mbody tab-home"' in page.data


def test_staff_preview_keeps_the_phone_frame(client, sign_in, db):
    link_member(db, "pastor@journeychurchsemo.com")
    sign_in("pastor@journeychurchsemo.com")
    page = client.get("/me/", headers={"Host": JOURNEY_HOST})
    assert page.status_code == 200
    assert b"memberfull" not in page.data


def test_every_member_page_names_its_tab(client, sign_in, db):
    link_member(db, "member@journeychurchsemo.com")
    sign_in("member@journeychurchsemo.com")
    for path, tab in (("/me/you/", "you"), ("/me/read/", "read"), ("/me/groups/", "groups")):
        page = client.get(path, headers={"Host": JOURNEY_HOST})
        assert page.status_code == 200, path
        assert f'class="mbody tab-{tab}"'.encode() in page.data, path


def test_desktop_rules_are_scoped_to_members_only():
    start = CSS.index("@media (min-width:900px){\n  /* Lines the logo")
    block = CSS[start:CSS.index("/* ---------- Reading ---------- */")]
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(".") and "{" in stripped:
            assert stripped.startswith(".memberfull"), stripped


def test_tabs_move_to_the_top_on_desktop():
    assert 'grid-template-areas:"head tabs" "body body"' in CSS
