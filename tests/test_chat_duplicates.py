"""One send is one message, however many times the button was pressed.

The bug, as it arrived: a room at Journey showing the same phone number six
times in a row, and a reply to a staff member twice.

The cause is not mysterious once you watch the request. Posting pushes every
device belonging to every person in the room before the page comes back, one
HTTP call each, and until that finishes the send button looks exactly as it
did before it was pressed. So it gets pressed again. The page is doing what
it was told six times, and each one is a real, separate, valid request.

Two halves, and both are needed:

**The button locks.** Stops the obvious path and tells the person something
happened. Does nothing for a phone that retried the request by itself, or for
a second tap that lands before the script runs.

**The server refuses the repeat.** `Message.repeat_of`. Same words, same
person, same room, inside thirty seconds, and still the newest thing said. It
is that last clause that keeps this from eating a real repeat: "Amen" twice
in a minute is two messages if anybody spoke in between, and one thumb if
nobody did.

The latency itself is the root cause and is not fixed here. See the notes at
the bottom of this file for what that would take.
"""

from datetime import timedelta

from app.models import Church, Conversation, ConversationMember, Message, Person, User
from app.models.base import utcnow
from app.models.message import KIND_ANNOUNCEMENT, KIND_ROOM
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def church(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def linked_person(db, email, first, last):
    c = church(db)
    user = db.session.scalar(
        db.select(User).where(User.church_id == c.id, User.email == email))
    person = Person(church_id=c.id, first_name=first, last_name=last, email=email,
                    stage="member", first_seen_on=utcnow().date(),
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    user.community_accepted_at = utcnow()
    db.session.commit()
    return person


def room(db, kind=KIND_ROOM):
    convo = Conversation(church_id=church(db).id, kind=kind, title="Tuesday Group")
    db.session.add(convo)
    db.session.commit()
    return convo


def add_member(db, convo, person):
    db.session.add(ConversationMember(
        church_id=convo.church_id, conversation_id=convo.id, person_id=person.id))
    db.session.commit()


def bodies(db, convo):
    return [
        m.body for m in db.session.scalars(
            db.select(Message)
            .where(Message.conversation_id == convo.id)
            .order_by(Message.id)
        ).all()
    ]


TYLER = "Tyler Slinkard (573) 837-5978"


class TestAMemberPressingSendTwice:
    def test_six_presses_make_one_message(self, client, sign_in, db):
        """The reported case, at the reported count."""
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        for _ in range(6):
            client.post(f"/me/chat/{convo.id}/", data={"body": TYLER}, headers=H)

        assert bodies(db, convo) == [TYLER]

    def test_the_repeat_still_lands_on_the_thread(self, client, sign_in, db):
        """Not an error page and not a flash. Somebody who tapped twice did
        not make a mistake worth being told about, and the thread they arrive
        at already shows what they wrote."""
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        client.post(f"/me/chat/{convo.id}/", data={"body": TYLER}, headers=H)
        again = client.post(f"/me/chat/{convo.id}/", data={"body": TYLER}, headers=H)

        assert again.status_code == 302
        assert again.headers["Location"].endswith(f"/me/chat/{convo.id}/")

    def test_a_different_message_is_not_swallowed(self, client, sign_in, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        client.post(f"/me/chat/{convo.id}/", data={"body": TYLER}, headers=H)
        client.post(f"/me/chat/{convo.id}/",
                    data={"body": "Megan Slinkard (573) 475-0235"}, headers=H)

        assert bodies(db, convo) == [TYLER, "Megan Slinkard (573) 475-0235"]

    def test_only_one_notification_goes_out(self, client, sign_in, db, monkeypatch):
        """The duplicate rows were the visible half. Everybody in the room
        also got the same notification six times, which is how somebody
        decides to turn notifications off for good."""
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        calls = []
        import app.blueprints.member as member_bp

        monkeypatch.setattr(
            member_bp, "notify_new_message",
            lambda *a, **k: calls.append(a) or (0, 0),
        )

        for _ in range(4):
            client.post(f"/me/chat/{convo.id}/", data={"body": TYLER}, headers=H)

        assert len(calls) == 1


class TestStaffPressingSendTwice:
    def test_a_staff_login_with_no_roster_record(self, client, sign_in, db):
        """Identified by the name they post under, the same way `post`
        records it. Without that branch the guard would never match a staff
        message, because every one of them has a null person id and they
        would all look like different authors."""
        convo = room(db)
        sign_in("pastor@journeychurchsemo.com")

        for _ in range(3):
            client.post(f"/messages/{convo.id}/post/",
                        data={"body": "Thanks Isaac, I filled it out."}, headers=H)

        assert bodies(db, convo) == ["Thanks Isaac, I filled it out."]

    def test_two_staff_saying_the_same_thing_both_land(self, client, sign_in, db):
        """A null person id is not an identity. Two different staff logins
        writing "Amen" are two people agreeing, and collapsing them would be
        the guard deciding who gets to speak."""
        convo = room(db)
        sign_in("pastor@journeychurchsemo.com")
        client.post(f"/messages/{convo.id}/post/", data={"body": "Amen"}, headers=H)
        # Signed out in between on purpose. The login route redirects anybody
        # already signed in, so a second `sign_in` on the same client is a
        # no-op and both messages would come from the pastor, which is the
        # case this test exists to rule out.
        client.post("/auth/logout", headers=H, follow_redirects=True)
        sign_in("leader@journeychurchsemo.com")
        client.post(f"/messages/{convo.id}/post/", data={"body": "Amen"}, headers=H)

        assert bodies(db, convo) == ["Amen", "Amen"]

    def test_announcements_are_covered_too(self, client, sign_in, db):
        convo = room(db, kind=KIND_ANNOUNCEMENT)
        sign_in("pastor@journeychurchsemo.com")

        for _ in range(3):
            client.post(f"/messages/{convo.id}/post/",
                        data={"body": "Picnic Sunday"}, headers=H)

        assert bodies(db, convo) == ["Picnic Sunday"]


class TestWhatTheGuardMustNotEat:
    """Every case here is somebody saying the same thing on purpose."""

    def test_a_repeat_after_somebody_else_spoke(self, client, sign_in, db):
        """The clause that makes the whole rule safe. Two "Amen"s with a
        message in between are a conversation, not a double tap, however
        close together they are."""
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        client.post(f"/me/chat/{convo.id}/", data={"body": "Amen"}, headers=H)
        Message.post(convo, None, "Praying for you all", author_name="Pastor Reed")
        db.session.commit()
        client.post(f"/me/chat/{convo.id}/", data={"body": "Amen"}, headers=H)

        assert bodies(db, convo) == ["Amen", "Praying for you all", "Amen"]

    def test_a_repeat_after_the_window(self, client, sign_in, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        add_member(db, convo, alicia)
        sign_in("member@journeychurchsemo.com")

        client.post(f"/me/chat/{convo.id}/", data={"body": "Here"}, headers=H)
        first = db.session.scalars(
            db.select(Message).where(Message.conversation_id == convo.id)).all()[0]
        first.sent_at = utcnow() - timedelta(seconds=Message.REPEAT_SECONDS + 5)
        db.session.commit()

        client.post(f"/me/chat/{convo.id}/", data={"body": "Here"}, headers=H)
        assert bodies(db, convo) == ["Here", "Here"]

    def test_re_saying_something_they_deleted(self, db):
        """Deleting means it is gone. A guard that then refuses to let them
        write it again is a guard arguing with the person using it."""
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        posted = Message.post(convo, alicia, "Wrong number, sorry")
        db.session.commit()
        posted.soft_delete()
        db.session.commit()

        assert Message.repeat_of(convo, alicia, "Wrong number, sorry") is None

    def test_the_same_words_in_a_different_room(self, db):
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        one = room(db)
        two = Conversation(church_id=church(db).id, kind=KIND_ROOM, title="Youth")
        db.session.add(two)
        db.session.commit()

        Message.post(one, alicia, "Running late")
        db.session.commit()

        assert Message.repeat_of(two, alicia, "Running late") is None

    def test_a_different_person_saying_it(self, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        dana = linked_person(db, "leader@journeychurchsemo.com", "Dana", "Webb")

        Message.post(convo, alicia, "Amen")
        db.session.commit()

        assert Message.repeat_of(convo, dana, "Amen") is None

    def test_an_empty_room(self, db):
        convo = room(db)
        alicia = linked_person(db, "member@journeychurchsemo.com", "Alicia", "Romero")
        assert Message.repeat_of(convo, alicia, "First one") is None


class TestTheButtonLocks:
    """The other half. A server guard that throws away the second request
    still leaves somebody watching a button that looks like it did nothing,
    and that is the thing that made them press it again."""

    def test_the_composer_locks_send_on_submit(self):
        from pathlib import Path

        source = Path("app/templates/_composer.html").read_text()
        assert "send.disabled = true" in source
        assert 'form.addEventListener("submit"' in source

    def test_the_box_is_read_only_rather_than_disabled(self):
        """A disabled field is not submitted, so disabling the textarea would
        post an empty body and the message would be lost outright. That is a
        worse bug than the one being fixed."""
        from pathlib import Path

        source = Path("app/templates/_composer.html").read_text()
        assert "box.readOnly = true" in source
        assert "box.disabled = true" not in source

    def test_it_unlocks_when_the_page_comes_back_from_the_cache(self):
        """Safari restores a page from bfcache without re-running scripts. A
        lock with no way out leaves somebody back on the thread unable to
        type."""
        from pathlib import Path

        source = Path("app/templates/_composer.html").read_text()
        assert 'addEventListener("pageshow"' in source
        assert "e.persisted" in source


# ---------------------------------------------------------------------------
# What is not fixed here.
#
# The reason the button looks untouched is that the request is slow, and it is
# slow because `app.push.send.send_to_person` sends every notification inline:
# one HTTPS call per device, per person in the room, each with a ten second
# timeout, all before the redirect. A room of twenty people is twenty or more
# round trips the author waits through.
#
# Moving that to the outbox is the actual fix, and it is not a small one: the
# worker runs every five minutes, so queueing a chat push there would make it
# arrive five minutes late, which is not a notification. It needs either a
# continuously running worker or sending after the response is returned.
#
# Until then this file is what stands between a slow request and a thread with
# the same sentence in it six times.
# ---------------------------------------------------------------------------
