"""The staff thread reads like the member one: iMessage bubbles, not a log."""

import re

import pytest

from app.models import Church, Conversation, ConversationMember, Message, Person, User
from app.models.base import utcnow
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
        db.session.add(ConversationMember(church_id=convo.church_id,
                                          conversation_id=convo.id, person_id=p.id))
    db.session.commit()
    return convo


def bubble_classes(html: str) -> list[str]:
    """mine / theirs, in the order they appear."""
    return re.findall(r'class="bmsg (mine|theirs)', html)


@pytest.fixture
def pastor_person(db):
    """The staff login with a roster record, not signed in."""
    user = db.session.scalar(db.select(User).where(User.email == "pastor@journeychurchsemo.com"))
    p = person(db, "Reed", email=user.email)
    user.person_id = p.id
    db.session.commit()
    return p


class TestBubbles:
    def test_the_thread_is_bubbles(self, db, staff):
        convo = room(db)
        Message.post(convo, None, "Rehearsal moved", author_name="Pastor Reed")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        assert b'class="bubbles"' in page
        assert b'class="bmsg' in page
        assert b'class="thread"' not in page  # the old flat list is gone

    def test_your_messages_are_yours_and_everyone_elses_are_not(self, db, staff, pastor_person):
        ben = person(db, "Ben")
        convo = room(db, ben, pastor_person)
        Message.post(convo, ben, "Can we run it twice?", author_name="Ben Test")
        Message.post(convo, pastor_person, "Yes, from the top", author_name="Pastor Reed")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert bubble_classes(page) == ["theirs", "mine"]

    def test_staff_without_a_roster_record_still_see_their_own_side(self, db, staff):
        """A staff login with no Person is matched on the name stored on the
        message, which is the only trace of who wrote it."""
        ben = person(db, "Ben")
        convo = room(db, ben)
        Message.post(convo, ben, "Morning", author_name="Ben Test")
        Message.post(convo, None, "Morning all", author_name="Pastor Reed")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert bubble_classes(page) == ["theirs", "mine"]

    def test_a_name_shows_for_others_and_not_for_you(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        Message.post(convo, ben, "Hello", author_name="Ben Test")
        Message.post(convo, None, "Hi Ben", author_name="Pastor Reed")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert '<div class="bname">Ben Test</div>' in page
        assert '<div class="bname">Pastor Reed</div>' not in page

    def test_one_name_for_a_run_of_messages(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        for text in ("one", "two", "three"):
            Message.post(convo, ben, text, author_name="Ben Test")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert page.count('<div class="bname">Ben Test</div>') == 1
        assert len(bubble_classes(page)) == 3

    def test_a_removed_message_keeps_its_place(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        message = Message.post(convo, ben, "Oops", author_name="Ben Test")
        message.is_deleted = True
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        assert b"This message was removed." in page
        assert b'class="bubble gone"' in page


class TestStaffStillHaveTheirControls:
    def test_delete_sits_in_the_bubble_menu(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        message = Message.post(convo, ben, "Take this down", author_name="Ben Test")
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert 'class="bmenu"' in page
        assert f"/messages/{convo.id}/messages/{message.id}/delete/" in page

    def test_no_menu_on_a_message_already_removed(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        message = Message.post(convo, ben, "Gone", author_name="Ben Test")
        message.is_deleted = True
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data.decode()
        assert 'class="bmenu"' not in page

    def test_deleting_from_the_menu_works(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        message = Message.post(convo, ben, "Take this down", author_name="Ben Test")
        db.session.commit()
        staff.post(f"/messages/{convo.id}/messages/{message.id}/delete/", headers=H,
                   follow_redirects=True)
        db.session.refresh(message)
        assert message.is_deleted is True

    def test_the_composer_replaces_the_old_box(self, db, staff):
        convo = room(db)
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        assert b'class="composer staffcomposer"' in page
        assert b"composerinput" in page
        assert b"composersend" in page

    def test_announcements_keep_the_email_checkbox(self, db, staff):
        convo = room(db, kind="announcement", title="All Church")
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        assert b'name="also_email"' in page

    def test_posting_still_works(self, db, staff):
        convo = room(db)
        staff.post(f"/messages/{convo.id}/post/", data={"body": "See you Tuesday"},
                   headers=H, follow_redirects=True)
        assert [m.body for m in convo.messages] == ["See you Tuesday"]

    def test_an_archived_chat_has_no_composer(self, db, staff):
        convo = room(db)
        convo.is_archived = True
        db.session.commit()
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        # The shared script names the box, so check for the box itself.
        assert b'<textarea class="composerinput"' not in page
        assert b"staffcomposer" not in page

    def test_the_members_card_is_still_there(self, db, staff):
        ben = person(db, "Ben")
        convo = room(db, ben)
        page = staff.get(f"/messages/{convo.id}/", headers=H).data
        assert b"Ben Test" in page
        assert f"/messages/{convo.id}/members/".encode() in page
