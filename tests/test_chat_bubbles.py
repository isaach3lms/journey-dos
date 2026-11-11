"""Member chat reads like a messaging app: mine on the right, theirs on the left."""

from datetime import timedelta

import pytest

from app.models import Church, Conversation, ConversationMember, Message, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def chat(client, sign_in, db):
    c = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    me = Person(church_id=c.id, first_name="Alicia", last_name="Romero", email=user.email, approved_at=utcnow())
    ben = Person(church_id=c.id, first_name="Ben", last_name="Cole", approved_at=utcnow())
    db.session.add_all([me, ben])
    db.session.flush()
    user.person_id = me.id
    room = Conversation(church_id=c.id, kind="room", title="Worship Team")
    db.session.add(room)
    db.session.flush()
    for p in (me, ben):
        db.session.add(ConversationMember(church_id=c.id, conversation_id=room.id, person_id=p.id))
    t = utcnow() - timedelta(hours=2)
    def post(person, body, minutes):
        m = Message.post(room, person, body)
        m.sent_at = t + timedelta(minutes=minutes)
    post(ben, "Rehearsal at 6?", 0)
    post(ben, "Bring the capo", 1)
    post(me, "Sounds good", 2)
    post(ben, "Next day thought", 60)
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client, room


def page(client, room):
    return client.get(f"/me/chat/{room.id}/", headers=H).data.decode()


def test_mine_and_theirs_are_marked(chat):
    client, room = chat
    html = page(client, room)
    assert html.count('class="bmsg theirs') == 3
    assert html.count('class="bmsg mine') == 1
    mine = html[html.index('class="bmsg mine'):]
    assert "Sounds good" in mine[:mine.index("</li>")]


def test_name_shows_once_per_run(chat):
    client, room = chat
    html = page(client, room)
    # Ben's first two messages are one run; the one after a long pause starts
    # a new run and names him again.
    assert html.count('<div class="bname">Ben Cole</div>') == 2


def test_timestamps_only_after_a_pause(chat):
    client, room = chat
    assert page(client, room).count('class="bstamp"') == 2


def test_report_and_block_live_in_the_menu_on_their_messages_only(chat):
    client, room = chat
    html = page(client, room)
    assert html.count('class="bmenu"') == 3
    assert "/report/" in html and "/block/" in html


def test_composer_is_there(chat):
    client, room = chat
    html = page(client, room)
    assert 'class="composer"' in html and 'name="body"' in html
