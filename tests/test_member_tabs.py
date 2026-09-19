"""Member tabs match the demo: Home, Give, Serve, Grow, Chat, You."""

import re

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def member_in(client, sign_in, db):
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    p = Person(church_id=user.church_id, first_name="A", last_name="R", email=user.email, approved_at=utcnow())
    db.session.add(p)
    db.session.flush()
    user.person_id = p.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client


def tab_labels(html: str) -> list[str]:
    nav = html[html.index('<nav class="mtabs"'):html.index("</nav>", html.index('<nav class="mtabs"'))]
    return re.findall(r"<span>([^<]+)</span>", nav)


def test_order_matches_the_demo_when_giving_is_set(member_in, db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.giving_form_url = "https://tithe.ly/give?c=1"
    db.session.commit()
    html = member_in.get("/me/", headers=H).data.decode()
    assert tab_labels(html) == ["Home", "Give", "Serve", "Grow", "Chat", "You"]
    assert html.count('class="mti"') == 6


def test_give_hides_until_the_church_sets_a_giving_link(member_in):
    html = member_in.get("/me/", headers=H).data.decode()
    assert tab_labels(html) == ["Home", "Serve", "Grow", "Chat", "You"]


@pytest.mark.parametrize("path", ["/me/read/", "/me/groups/"])
def test_grow_covers_reading_and_groups(member_in, path):
    html = member_in.get(path, headers=H).data.decode()
    nav = html[html.index('<nav class="mtabs"'):]
    grow = nav[:nav.index("<span>Grow</span>")]
    assert 'aria-current="page"' in grow[grow.rindex("<a "):]
    assert "Reading plans" in html and "My groups" in html


def test_home_is_marked_current(member_in):
    html = member_in.get("/me/", headers=H).data.decode()
    nav = html[html.index('<nav class="mtabs"'):]
    first = nav[nav.index("<a "):nav.index("</a>")]
    assert 'aria-current="page"' in first
