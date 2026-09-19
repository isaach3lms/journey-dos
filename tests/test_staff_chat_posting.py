"""Staff and leaders can write in group chats, not only members.

The bug: posting from the staff Messages screen checked room membership, and
staff are almost never members of the rooms they run. A staff login with no
roster record could never be a member at all. Every staff post to a room was
a 403.
"""

from app.models import Church, Conversation, Message, Person, User
from app.models.message import KIND_ANNOUNCEMENT, KIND_ROOM
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def church(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def linked_person(db, email, first, last):
    c = church(db)
    user = db.session.scalar(db.select(User).where(User.church_id == c.id, User.email == email))
    person = Person(church_id=c.id, first_name=first, last_name=last, email=email,
                    stage="visitor", first_seen_on=utcnow().date(), approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    return person


def room(db, kind=KIND_ROOM):
    c = church(db)
    convo = Conversation(church_id=c.id, kind=kind, title="Tuesday Group")
    db.session.add(convo)
    db.session.commit()
    return convo


def add_member(db, convo, person):
    from app.models import ConversationMember

    db.session.add(ConversationMember(
        church_id=convo.church_id, conversation_id=convo.id, person_id=person.id
    ))
    db.session.commit()


def messages_in(db, convo):
    return db.session.scalars(
        db.select(Message).where(Message.conversation_id == convo.id)
    ).all()


class TestStaffCanPostInRooms:
    def test_staff_without_a_roster_record(self, client, sign_in, db):
        convo = room(db)
        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "See you Tuesday"}, headers=H)
        assert response.status_code == 302
        [message] = messages_in(db, convo)
        assert message.body == "See you Tuesday"
        # Named, not "Someone".
        assert message.author_name == "Pastor Reed"

    def test_staff_with_a_roster_record_not_in_the_room(self, client, sign_in, db):
        convo = room(db)
        linked_person(db, "pastor@journeychurchsemo.com", "Pastor", "Reed")
        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "Hello all"}, headers=H)
        assert response.status_code == 302
        assert len(messages_in(db, convo)) == 1

    def test_leaders_can_post_in_rooms(self, client, sign_in, db):
        convo = room(db)
        sign_in("leader@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "Bring a Bible"}, headers=H)
        assert response.status_code == 302
        assert messages_in(db, convo)[0].author_name == "Dana Webb"

    def test_leaders_still_cannot_post_announcements(self, client, sign_in, db):
        convo = room(db, kind=KIND_ANNOUNCEMENT)
        sign_in("leader@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "Hi everyone"}, headers=H)
        assert response.status_code == 403
        assert messages_in(db, convo) == []

    def test_staff_can_post_announcements(self, client, sign_in, db):
        convo = room(db, kind=KIND_ANNOUNCEMENT)
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/messages/{convo.id}/post/", data={"body": "Picnic Sunday"}, headers=H)
        assert len(messages_in(db, convo)) == 1

    def test_archived_rooms_stay_closed(self, client, sign_in, db):
        convo = room(db)
        convo.is_archived = True
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "x"}, headers=H)
        assert response.status_code == 403

    def test_the_filter_still_applies_to_staff(self, client, sign_in, db):
        convo = room(db)
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/messages/{convo.id}/post/", data={"body": "what the shit"}, headers=H)
        assert messages_in(db, convo) == []

    def test_other_churches_rooms_are_not_reachable(self, client, sign_in, db):
        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        convo = Conversation(church_id=other.id, kind=KIND_ROOM, title="Theirs")
        db.session.add(convo)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")
        response = client.post(f"/messages/{convo.id}/post/", data={"body": "x"}, headers=H)
        assert response.status_code == 404


class TestMembersSeeIt:
    def test_members_read_staff_posts_and_count_them_unread(self, client, sign_in, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)

        # Staff with no roster record, so the message has no person id.
        Message.post(convo, None, "Welcome, everyone", author_name="Pastor Reed")
        db.session.commit()

        assert Message.unread_total(church(db).id, alicia.id) == 1

        sign_in("member@journeychurchsemo.com")
        page = client.get(f"/me/chat/{convo.id}/", headers=H)
        assert page.status_code == 200
        assert b"Welcome, everyone" in page.data
        assert b"Pastor Reed" in page.data

    def test_members_still_cannot_post_where_they_are_not_members(self, client, sign_in, db):
        convo = room(db)
        linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        sign_in("member@journeychurchsemo.com")
        response = client.post(f"/me/chat/{convo.id}/", data={"body": "hi"}, headers=H)
        assert response.status_code in (403, 404, 405)
        assert messages_in(db, convo) == []

    def test_members_in_the_room_can_still_post(self, client, sign_in, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")
        client.post(f"/me/chat/{convo.id}/", data={"body": "Thanks!"}, headers=H)
        assert [m.body for m in messages_in(db, convo)] == ["Thanks!"]


class TestTheRuleItself:
    def test_can_post_matrix(self, db):
        c = church(db)
        r = Conversation(church_id=c.id, kind=KIND_ROOM, title="r")
        a = Conversation(church_id=c.id, kind=KIND_ANNOUNCEMENT, title="a")
        db.session.add_all([r, a])
        db.session.commit()
        outsider = Person(church_id=c.id, first_name="O", last_name="S", approved_at=utcnow())
        db.session.add(outsider)
        db.session.commit()

        assert r.can_post(outsider) is False
        assert r.can_post(None, is_leader=True) is True
        assert r.can_post(None, is_staff=True) is True
        assert a.can_post(None, is_leader=True) is False
        assert a.can_post(None, is_staff=True) is True
