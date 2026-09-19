"""Members posting church-wide announcements, and staff keeping watch.

Members can post to announcement chats when the church allows it (on by
default, one switch in Settings). Staff get three ways to monitor: one list of
the latest messages everywhere with a delete on each, delete from the member
app on their phone, and delete a whole chat.
"""

import pytest

from app.models import (
    KIND_ANNOUNCEMENT,
    KIND_ROOM,
    AuditEvent,
    Church,
    Conversation,
    ConversationMember,
    Message,
    Person,
    User,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def link(db, email, first="Alicia", last="Romero", approved=True):
    c = journey(db)
    user = db.session.scalar(db.select(User).where(User.church_id == c.id, User.email == email))
    person = Person(
        church_id=c.id, first_name=first, last_name=last, email=email, stage="visitor",
        first_seen_on=utcnow().date(), self_registered=not approved,
        approved_at=utcnow() if approved else None,
    )
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return person


def announcement(db):
    convo = Conversation(church_id=journey(db).id, kind=KIND_ANNOUNCEMENT, title="Church-wide")
    db.session.add(convo)
    db.session.commit()
    return convo


def a_room(db, *people):
    convo = Conversation(church_id=journey(db).id, kind=KIND_ROOM, title="Tuesday Group")
    db.session.add(convo)
    db.session.flush()
    for p in people:
        db.session.add(ConversationMember(church_id=convo.church_id, conversation_id=convo.id, person_id=p.id))
    db.session.commit()
    return convo


def bodies(db, convo):
    return [m.body for m in db.session.scalars(
        db.select(Message).where(Message.conversation_id == convo.id).order_by(Message.id)
    )]


class TestMembersPostAnnouncements:
    def test_on_by_default(self, db):
        assert journey(db).members_can_announce is True

    def test_an_approved_member_can_post(self, client, sign_in, db):
        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        client.post(f"/me/chat/{convo.id}/", data={"body": "Bake sale Sunday!"}, headers=H)
        assert bodies(db, convo) == ["Bake sale Sunday!"]

    def test_the_compose_box_shows_with_a_note(self, client, sign_in, db):
        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        page = client.get(f"/me/chat/{convo.id}/", headers=H)
        assert b'name="body"' in page.data
        assert b"This goes to everyone at" in page.data

    def test_an_unapproved_signup_cannot(self, client, sign_in, db):
        link(db, "member@journeychurchsemo.com", approved=False)
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        r = client.post(f"/me/chat/{convo.id}/", data={"body": "Hello all"}, headers=H)
        assert r.status_code in (403, 404)
        assert bodies(db, convo) == []

    def test_switched_off_refuses(self, client, sign_in, db):
        journey(db).members_can_announce = False
        db.session.commit()
        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        r = client.post(f"/me/chat/{convo.id}/", data={"body": "Hello all"}, headers=H)
        assert r.status_code == 403
        page = client.get(f"/me/chat/{convo.id}/", headers=H)
        assert b'name="body"' not in page.data

    def test_the_filter_applies(self, client, sign_in, db):
        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        client.post(f"/me/chat/{convo.id}/", data={"body": "this is bullshit"}, headers=H)
        assert bodies(db, convo) == []

    def test_five_a_day(self, client, sign_in, db):
        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        for i in range(6):
            client.post(f"/me/chat/{convo.id}/", data={"body": f"Post {i}"}, headers=H)
        assert len(bodies(db, convo)) == 5
        page = client.get(f"/me/chat/{convo.id}/", headers=H)
        assert page.status_code == 200

    def test_the_limit_does_not_touch_rooms(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        convo = a_room(db, alicia)
        sign_in("member@journeychurchsemo.com")
        for i in range(7):
            client.post(f"/me/chat/{convo.id}/", data={"body": f"Room {i}"}, headers=H)
        assert len(bodies(db, convo)) == 7

    def test_leaders_are_not_limited(self, client, sign_in, db):
        link(db, "leader@journeychurchsemo.com", "Dana", "Webb")
        convo = announcement(db)
        sign_in("leader@journeychurchsemo.com")
        for i in range(7):
            client.post(f"/me/chat/{convo.id}/", data={"body": f"L {i}"}, headers=H)
        assert len(bodies(db, convo)) == 7

    def test_email_stays_staff_only(self, client, sign_in, db):
        from app.models import OutboxMessage

        link(db, "member@journeychurchsemo.com")
        convo = announcement(db)
        sign_in("member@journeychurchsemo.com")
        client.post(f"/me/chat/{convo.id}/", data={"body": "Hi", "also_email": "on"}, headers=H)
        assert db.session.scalar(db.select(OutboxMessage).where(OutboxMessage.category == "announcement")) is None


class TestSettingsSwitch:
    def test_staff_flip_it(self, client, sign_in, db):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/settings/", headers=H)
        assert b"Who can post church-wide announcements" in page.data
        client.post("/settings/announcements/", headers=H)
        assert journey(db).members_can_announce is False
        client.post("/settings/announcements/", headers=H)
        assert journey(db).members_can_announce is True

    def test_leaders_cannot(self, client, sign_in, db):
        sign_in("leader@journeychurchsemo.com")
        r = client.post("/settings/announcements/", headers=H)
        assert r.status_code in (302, 403, 404)
        assert journey(db).members_can_announce is True


class TestLatestMessagesList:
    def test_staff_see_everything_in_one_list(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        room = a_room(db, alicia)
        convo = announcement(db)
        Message.post(room, alicia, "In the room")
        Message.post(convo, alicia, "To everyone")
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/messages/", headers=H)
        assert b"Latest messages everywhere" in page.data
        assert b"In the room" in page.data and b"To everyone" in page.data

    def test_delete_from_the_list_returns_to_the_list(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        room = a_room(db, alicia)
        message = Message.post(room, alicia, "Remove me")
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        r = client.post(
            f"/messages/{room.id}/messages/{message.id}/delete/", data={"back": "recent"}, headers=H
        )
        assert r.headers["Location"].endswith("/messages/#recent")
        db.session.refresh(message)
        assert message.is_deleted and message.body is None
        page = client.get("/messages/", headers=H)
        assert b"Remove me" not in page.data

    def test_other_churches_do_not_appear(self, client, sign_in, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        convo = Conversation(church_id=other.id, kind=KIND_ROOM, title="Theirs")
        db.session.add(convo)
        db.session.flush()
        Message.post(convo, None, "Riverbend secret", author_name="X")
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        assert b"Riverbend secret" not in client.get("/messages/", headers=H).data


class TestDeleteFromThePhone:
    def test_a_leader_deletes_in_the_member_app(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        dana = link(db, "leader@journeychurchsemo.com", "Dana", "Webb")
        room = a_room(db, alicia, dana)
        message = Message.post(room, alicia, "Oops")
        db.session.commit()
        sign_in("leader@journeychurchsemo.com")
        page = client.get(f"/me/chat/{room.id}/", headers=H)
        assert f"/me/chat/{room.id}/messages/{message.id}/delete/".encode() in page.data
        client.post(f"/me/chat/{room.id}/messages/{message.id}/delete/", headers=H)
        db.session.refresh(message)
        assert message.is_deleted
        assert db.session.scalar(db.select(AuditEvent).where(AuditEvent.action == "message_deleted"))

    def test_members_cannot_delete(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        other = Person(church_id=alicia.church_id, first_name="Ben", last_name="Cole", approved_at=utcnow())
        db.session.add(other)
        db.session.flush()
        room = a_room(db, alicia, other)
        message = Message.post(room, other, "Not yours")
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        page = client.get(f"/me/chat/{room.id}/", headers=H)
        assert b"/delete/" not in page.data
        r = client.post(f"/me/chat/{room.id}/messages/{message.id}/delete/", headers=H)
        assert r.status_code == 403
        db.session.refresh(message)
        assert not message.is_deleted


class TestDeleteWholeChat:
    def test_staff_clear_and_close_it(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        room = a_room(db, alicia)
        for text in ("one", "two", "three"):
            Message.post(room, alicia, text)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        r = client.post(f"/messages/{room.id}/delete/", headers=H, follow_redirects=True)
        assert b"3 messages cleared" in r.data
        db.session.refresh(room)
        assert room.is_archived
        assert all(m.is_deleted and m.body is None for m in room.messages)
        event = db.session.scalar(db.select(AuditEvent).where(AuditEvent.action == "chat_deleted"))
        assert event is not None

    def test_it_disappears_for_members(self, client, sign_in, db):
        alicia = link(db, "member@journeychurchsemo.com")
        room = a_room(db, alicia)
        room.is_archived = True
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        assert b"Tuesday Group" not in client.get("/me/chat/", headers=H).data

    def test_leaders_cannot_delete_a_chat(self, client, sign_in, db):
        room = a_room(db)
        sign_in("leader@journeychurchsemo.com")
        r = client.post(f"/messages/{room.id}/delete/", headers=H)
        assert r.status_code in (302, 403)
        db.session.refresh(room)
        assert not room.is_archived

    def test_the_button_is_staff_only(self, client, sign_in, db):
        room = a_room(db)
        sign_in("pastor@journeychurchsemo.com")
        assert b"Delete this chat" in client.get(f"/messages/{room.id}/", headers=H).data

    def test_another_churchs_chat_is_a_404(self, client, sign_in, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        convo = Conversation(church_id=other.id, kind=KIND_ROOM, title="Theirs")
        db.session.add(convo)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        assert client.post(f"/messages/{convo.id}/delete/", headers=H).status_code == 404
