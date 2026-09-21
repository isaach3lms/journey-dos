"""An email when somebody posts in a room you are in."""

from datetime import timedelta

import pytest

from app import chat_notify
from app.models import (
    Church, Conversation, ConversationMember, Message, OutboxMessage, Person, User,
)
from app.models.base import utcnow
from app.models.moderation import PersonBlock
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def person(db, first, email=None, **kw):
    p = Person(church_id=journey(db).id, first_name=first, last_name="Test",
               email=email or f"{first.lower()}@example.com", approved_at=utcnow(), **kw)
    db.session.add(p)
    db.session.flush()
    return p


def room(db, *people, kind="room", title="Worship Team"):
    convo = Conversation(church_id=journey(db).id, kind=kind, title=title)
    db.session.add(convo)
    db.session.flush()
    for p in people:
        db.session.add(ConversationMember(church_id=convo.church_id, conversation_id=convo.id, person_id=p.id))
    db.session.commit()
    return convo


def chat_mail(db):
    return db.session.scalars(
        db.select(OutboxMessage).where(OutboxMessage.category == "chat")
    ).all()


@pytest.fixture
def alicia_person(db):
    """Alicia on the roster, linked to the member login, not signed in.

    The `db` fixture holds one application context for the whole test, and
    Flask-Login caches the signed-in user on `g`, so a test cannot sign one
    person in and then another. A test about staff posting takes this fixture
    and signs in once, as staff.
    """
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    p = person(db, "Alicia", email=user.email)
    user.person_id = p.id
    db.session.commit()
    return p


@pytest.fixture
def alicia(db, client, sign_in, alicia_person):
    sign_in("member@journeychurchsemo.com")
    return alicia_person


class TestWhoGetsOne:
    def test_everyone_else_in_the_room(self, db, client, alicia):
        ben, cara = person(db, "Ben"), person(db, "Cara")
        convo = room(db, alicia, ben, cara)
        client.post(f"/me/chat/{convo.id}/", data={"body": "Rehearsal at 6"}, headers=H)
        mail = chat_mail(db)
        assert sorted(m.to_email for m in mail) == ["ben@example.com", "cara@example.com"]
        assert "Alicia Test posted in Worship Team" in mail[0].subject
        assert "Rehearsal at 6" in mail[0].body_text

    def test_never_the_author(self, db, client, alicia):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert alicia.email not in [m.to_email for m in chat_mail(db)]

    def test_nobody_without_an_address(self, db, client, alicia):
        quiet = person(db, "Quiet")
        quiet.email = None
        convo = room(db, alicia, quiet)
        client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert chat_mail(db) == []

    def test_somebody_who_turned_chat_email_off(self, db, client, alicia):
        ben = person(db, "Ben")
        ben.set_preference("chat", False)
        convo = room(db, alicia, ben)
        db.session.commit()
        client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert chat_mail(db) == []

    def test_somebody_who_blocked_the_author(self, db, client, alicia):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        PersonBlock.add(ben, alicia)
        db.session.commit()
        client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert chat_mail(db) == []

    def test_church_wide_announcements_send_nothing_automatically(self, db, client, alicia):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben, kind="announcement", title="All Church")
        client.post(f"/me/chat/{convo.id}/", data={"body": "Picnic Sunday"}, headers=H)
        assert chat_mail(db) == []

    def test_a_staff_post_notifies_the_room(self, db, client, sign_in, alicia_person):
        convo = room(db, alicia_person)
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/messages/{convo.id}/post/", data={"body": "See you Tuesday"}, headers=H)
        mail = chat_mail(db)
        assert [m.to_email for m in mail] == [alicia_person.email]
        assert "Pastor Reed posted in Worship Team" in mail[0].subject


class TestNoFlood:
    def test_one_email_per_room_per_half_hour(self, db, client, alicia):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        for text in ("one", "two", "three"):
            client.post(f"/me/chat/{convo.id}/", data={"body": text}, headers=H)
        assert len(chat_mail(db)) == 1

    def test_a_different_room_is_its_own_notification(self, db, client, alicia):
        ben = person(db, "Ben")
        one = room(db, alicia, ben, title="Worship Team")
        two = room(db, alicia, ben, title="Kids Team")
        for convo in (one, two):
            client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert len(chat_mail(db)) == 2

    def test_the_window_moves_on(self, db, app, alicia, monkeypatch):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        with app.test_request_context(headers={"Host": JOURNEY_HOST}):
            first = Message.post(convo, alicia, "one")
            db.session.flush()
            chat_notify.notify_new_message(convo, first, author_person=alicia)
            db.session.commit()
            later = utcnow() + timedelta(minutes=45)
            monkeypatch.setattr(chat_notify, "utcnow", lambda: later)
            second = Message.post(convo, alicia, "two")
            db.session.flush()
            chat_notify.notify_new_message(convo, second, author_person=alicia)
            db.session.commit()
        assert len(chat_mail(db)) == 2


class TestSafety:
    def test_a_broken_notification_never_loses_the_message(self, db, client, alicia, monkeypatch):
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        monkeypatch.setattr(chat_notify, "queue", lambda **kw: (_ for _ in ()).throw(RuntimeError("mail is down")))
        r = client.post(f"/me/chat/{convo.id}/", data={"body": "still saved"}, headers=H)
        assert r.status_code == 302
        assert db.session.scalar(db.select(Message).where(Message.body == "still saved")) is not None
        assert chat_mail(db) == []

    def test_the_category_is_optional_and_listed_for_members(self, db, client, alicia):
        from app.categories import CATEGORY_BY_CODE

        assert CATEGORY_BY_CODE["chat"].is_transactional is False
        page = client.get("/me/you/", headers=H).data
        assert b"Chat messages" in page

    def test_other_churches_are_untouched(self, db, client, alicia):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Riverbend", last_name="Person",
                        email="riverbend@example.com", approved_at=utcnow())
        db.session.add(theirs)
        ben = person(db, "Ben")
        convo = room(db, alicia, ben)
        db.session.commit()
        client.post(f"/me/chat/{convo.id}/", data={"body": "hello"}, headers=H)
        assert [m.to_email for m in chat_mail(db)] == ["ben@example.com"]
