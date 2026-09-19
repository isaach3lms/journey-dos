"""Increment 12: messaging.

The tests that matter are about who can see and who can post. A private room
readable by anyone signed in, or a church-wide announcement anyone can reply
to, are both failures that look like working software.
"""

import pytest

from app.markup import render_message
from app.models import (
    KIND_ANNOUNCEMENT,
    KIND_DIRECT,
    KIND_ROOM,
    Church,
    Conversation,
    ConversationMember,
    Message,
    OutboxMessage,
    Person,
    User,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def alicia(db, journey):
    person = Person(
        church_id=journey.id, first_name="Alicia", last_name="Romero",
        email=MEMBER_EMAIL, stage="attender",
    )
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
    )
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def other(db, journey):
    person = Person(
        church_id=journey.id, first_name="Marcus", last_name="Webb",
        email="marcus@example.com", stage="member",
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def announcement(db, journey):
    conversation = Conversation(
        church_id=journey.id, kind=KIND_ANNOUNCEMENT, title="Church-wide"
    )
    db.session.add(conversation)
    db.session.commit()
    return conversation


@pytest.fixture
def room(db, journey, other):
    conversation = Conversation(
        church_id=journey.id, kind=KIND_ROOM, title="Worship team"
    )
    db.session.add(conversation)
    db.session.flush()
    db.session.add(
        ConversationMember(
            church_id=journey.id, conversation_id=conversation.id, person_id=other.id
        )
    )
    db.session.commit()
    return conversation


class TestChatRenderer:
    def test_it_escapes(self):
        out = str(render_message("<script>alert(1)</script>"))
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_line_breaks_are_kept(self):
        assert "<br>" in str(render_message("one\ntwo"))

    def test_blank_lines_make_paragraphs(self):
        assert str(render_message("one\n\ntwo")).count("<p>") == 2

    def test_it_does_not_reformat_what_people_typed(self):
        """"- 5" means minus five, not a bullet. "# 1" means number one."""
        out = str(render_message("- 5 chairs\n# 1 priority"))
        assert "<ul>" not in out
        assert "<h3>" not in out
        assert "- 5 chairs" in out

    def test_empty(self):
        assert str(render_message(None)) == ""
        assert str(render_message("")) == ""


class TestWhoCanRead:
    def test_an_announcement_is_readable_by_everyone(self, announcement, alicia):
        assert announcement.can_read(alicia.id)

    def test_an_announcement_has_no_membership_rows(self, announcement):
        """Writing a row per person to say "everyone" is a synchronization
        problem with no upside."""
        assert announcement.members == []

    def test_a_room_needs_membership(self, room, alicia, other):
        assert room.can_read(other.id)
        assert not room.can_read(alicia.id)

    def test_a_member_only_lists_what_they_can_see(self, db, journey, room, announcement, alicia):
        visible = db.session.scalars(
            Conversation.visible_to(journey.id, alicia.id)
        ).all()
        titles = {c.title for c in visible}
        assert "Church-wide" in titles
        assert "Worship team" not in titles

    def test_opening_a_private_room_is_a_404_not_a_403(self, room, member, alicia):
        """A 403 tells somebody a private room exists, which is itself a
        disclosure about who is talking to whom."""
        r = member.get(f"/me/chat/{room.id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_room_at_another_church_is_a_404(self, db, member, alicia):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Conversation(church_id=riverbend.id, kind=KIND_ROOM, title="Theirs")
        db.session.add(theirs)
        db.session.commit()
        assert member.get(
            f"/me/chat/{theirs.id}/", headers={"Host": JOURNEY_HOST}
        ).status_code == 404


class TestWhoCanPost:
    def test_only_staff_post_to_an_announcement(self, announcement, alicia):
        """Otherwise a church-wide message becomes a room nobody chose to join."""
        assert not announcement.can_post(alicia.id, is_staff=False)
        assert announcement.can_post(alicia.id, is_staff=True)

    def test_a_member_posting_to_an_announcement_is_refused(
        self, announcement, alicia, member, db
    ):
        """When the church has member announcements switched off."""
        church = db.session.get(Church, announcement.church_id)
        church.members_can_announce = False
        db.session.commit()
        r = member.post(
            f"/me/chat/{announcement.id}/",
            data={"body": "Can I reply?"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 403
        assert announcement.messages == []

    def test_a_member_of_a_room_can_post(self, db, journey, room, alicia, member):
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=room.id, person_id=alicia.id
            )
        )
        db.session.commit()

        member.post(
            f"/me/chat/{room.id}/",
            data={"body": "On my way"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(room)
        assert [m.body for m in room.messages] == ["On my way"]

    def test_somebody_outside_a_room_cannot_post(self, room, alicia, member):
        r = member.post(
            f"/me/chat/{room.id}/",
            data={"body": "Let me in"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        assert room.messages == []

    def test_an_archived_conversation_takes_no_posts(self, db, room, other):
        assert room.can_post(other.id)
        room.is_archived = True
        db.session.commit()
        assert not room.can_post(other.id)

    def test_an_empty_message_is_refused(self, db, journey, room, alicia, member):
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=room.id, person_id=alicia.id
            )
        )
        db.session.commit()
        member.post(
            f"/me/chat/{room.id}/", data={"body": "   "}, headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(room)
        assert room.messages == []


class TestMessages:
    def test_the_author_name_is_copied(self, db, room, other):
        """"Marcus said" should not become "someone said" later."""
        Message.post(room, other, "Hello")
        db.session.commit()

        message = room.messages[0]
        assert message.author_name == "Marcus Webb"

        db.session.delete(other)
        db.session.commit()
        db.session.refresh(message)
        assert message.author_name == "Marcus Webb"

    def test_posting_moves_the_conversation_timestamp(self, db, room, other):
        assert room.last_message_at is None
        Message.post(room, other, "Hello")
        db.session.commit()
        assert room.last_message_at is not None

    def test_a_soft_delete_clears_the_words_and_keeps_the_row(self, db, room, other):
        """A hard delete lets a leader erase a conversation somebody needs."""
        message = Message.post(room, other, "Something regrettable")
        db.session.commit()

        message.soft_delete()
        db.session.commit()

        assert message.body is None
        assert message.is_deleted
        assert message.deleted_at is not None
        assert db.session.get(Message, message.id) is not None

    def test_a_deleted_message_leaves_a_visible_hole(self, db, room, other, staff):
        message = Message.post(room, other, "Something regrettable")
        db.session.commit()

        staff.post(
            f"/messages/{room.id}/messages/{message.id}/delete/",
            headers={"Host": JOURNEY_HOST},
        )
        r = staff.get(f"/messages/{room.id}/", headers={"Host": JOURNEY_HOST})
        assert b"This message was removed" in r.data
        assert b"Something regrettable" not in r.data

    def test_deleted_messages_are_hidden_from_members(self, db, journey, room, alicia, other, member):
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=room.id, person_id=alicia.id
            )
        )
        message = Message.post(room, other, "Something regrettable")
        db.session.commit()
        message.soft_delete()
        db.session.commit()

        assert room.visible_messages == []

    def test_a_message_body_is_escaped_in_the_view(self, db, journey, room, alicia, other, member):
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=room.id, person_id=alicia.id
            )
        )
        Message.post(room, other, "<script>alert('x')</script>")
        db.session.commit()

        r = member.get(f"/me/chat/{room.id}/", headers={"Host": JOURNEY_HOST})
        assert b"<script>alert" not in r.data
        assert b"&lt;script&gt;" in r.data


class TestUnread:
    def _join(self, db, journey, room, person):
        membership = ConversationMember(
            church_id=journey.id, conversation_id=room.id, person_id=person.id
        )
        db.session.add(membership)
        db.session.commit()
        return membership

    def test_a_new_message_is_unread(self, db, journey, room, alicia, other):
        self._join(db, journey, room, alicia)
        Message.post(room, other, "Hello")
        db.session.commit()
        assert room.unread_for(alicia.id) == 1

    def test_your_own_messages_are_not_unread(self, db, journey, room, alicia):
        self._join(db, journey, room, alicia)
        Message.post(room, alicia, "Hello")
        db.session.commit()
        assert room.unread_for(alicia.id) == 0

    def test_opening_the_thread_marks_it_read(self, db, journey, room, alicia, other, member):
        self._join(db, journey, room, alicia)
        Message.post(room, other, "Hello")
        db.session.commit()

        member.get(f"/me/chat/{room.id}/", headers={"Host": JOURNEY_HOST})
        db.session.expire_all()
        conversation = Conversation.get_for_church(journey.id, room.id)
        assert conversation.unread_for(alicia.id) == 0

    def test_a_deleted_message_is_not_unread(self, db, journey, room, alicia, other):
        self._join(db, journey, room, alicia)
        message = Message.post(room, other, "Hello")
        db.session.commit()
        message.soft_delete()
        db.session.commit()
        assert room.unread_for(alicia.id) == 0

    def test_the_total_is_one_query_across_conversations(
        self, db, journey, room, alicia, other
    ):
        self._join(db, journey, room, alicia)
        Message.post(room, other, "One")
        Message.post(room, other, "Two")
        db.session.commit()
        assert Message.unread_total(journey.id, alicia.id) == 2


class TestDirectMessages:
    def test_one_thread_per_pair_whoever_starts_it(self, db, journey, alicia, other):
        first, created_a = Conversation.find_or_create_direct(
            journey.id, alicia.id, other.id, "Alicia and Marcus"
        )
        db.session.commit()
        second, created_b = Conversation.find_or_create_direct(
            journey.id, other.id, alicia.id, "Marcus and Alicia"
        )
        db.session.commit()

        assert created_a and not created_b
        assert first.id == second.id

    def test_both_people_are_members(self, db, journey, alicia, other):
        conversation, _ = Conversation.find_or_create_direct(
            journey.id, alicia.id, other.id, "Alicia and Marcus"
        )
        db.session.commit()
        assert {m.person_id for m in conversation.members} == {alicia.id, other.id}

    def test_the_pair_key_is_order_independent(self):
        assert Conversation.pair_key(5, 9) == Conversation.pair_key(9, 5) == "5:9"

    def test_nobody_else_can_read_it(self, db, journey, alicia, other):
        conversation, _ = Conversation.find_or_create_direct(
            journey.id, alicia.id, other.id, "Alicia and Marcus"
        )
        third = Person(
            church_id=journey.id, first_name="Third", last_name="Party", stage="member"
        )
        db.session.add(third)
        db.session.commit()
        assert not conversation.can_read(third.id)

    def test_two_pairs_do_not_collide(self, db, journey, alicia, other):
        third = Person(
            church_id=journey.id, first_name="Third", last_name="Party", stage="member"
        )
        db.session.add(third)
        db.session.commit()

        a, _ = Conversation.find_or_create_direct(journey.id, alicia.id, other.id, "A")
        db.session.commit()
        b, _ = Conversation.find_or_create_direct(journey.id, alicia.id, third.id, "B")
        db.session.commit()
        assert a.id != b.id


class TestStaffScreens:
    def test_creating_a_room(self, db, journey, staff):
        staff.post(
            "/messages/",
            data={"title": "Worship team", "kind": KIND_ROOM},
            headers={"Host": JOURNEY_HOST},
        )
        found = db.session.scalars(Conversation.for_church(journey.id)).all()
        assert [c.title for c in found] == ["Worship team"]

    def test_a_direct_kind_cannot_be_created_from_the_form(self, staff):
        """Direct threads start from a person, so the pair is always known."""
        r = staff.post(
            "/messages/",
            data={"title": "x", "kind": KIND_DIRECT},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_members_cannot_be_added_to_an_announcement(self, announcement, alicia, staff):
        """Adding one would imply everybody else is excluded."""
        r = staff.post(
            f"/messages/{announcement.id}/members/",
            data={"person_id": alicia.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_a_person_from_another_church_cannot_be_added(self, db, room, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        stranger = Person(
            church_id=riverbend.id, first_name="Not", last_name="Ours", stage="member"
        )
        db.session.add(stranger)
        db.session.commit()

        r = staff.post(
            f"/messages/{room.id}/members/",
            data={"person_id": stranger.id},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 400

    def test_a_member_cannot_reach_the_staff_screens(self, room, member):
        assert member.get("/messages/", headers={"Host": JOURNEY_HOST}).status_code == 403
        assert member.get(
            f"/messages/{room.id}/", headers={"Host": JOURNEY_HOST}
        ).status_code == 403

    def test_the_list_only_shows_this_church(self, db, room, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(
            Conversation(church_id=riverbend.id, kind=KIND_ROOM, title="Theirs")
        )
        db.session.commit()

        r = staff.get("/messages/", headers={"Host": JOURNEY_HOST})
        assert b"Worship team" in r.data
        assert b"Theirs" not in r.data


class TestEmailingAnAnnouncement:
    def test_it_queues_for_people_who_allow_announcements(
        self, db, announcement, alicia, other, staff
    ):
        staff.post(
            f"/messages/{announcement.id}/post/",
            data={"body": "Potluck on Sunday", "also_email": "on"},
            headers={"Host": JOURNEY_HOST},
        )
        messages = db.session.scalars(db.select(OutboxMessage)).all()
        assert len(messages) == 2
        assert all("Potluck" in m.body_text for m in messages)

    def test_somebody_who_turned_announcements_off_still_sees_it_in_the_app(
        self, db, announcement, alicia, other, staff
    ):
        alicia.set_preference("announcement", False)
        db.session.commit()

        staff.post(
            f"/messages/{announcement.id}/post/",
            data={"body": "Potluck on Sunday", "also_email": "on"},
            headers={"Host": JOURNEY_HOST},
        )
        recipients = {
            m.to_email for m in db.session.scalars(db.select(OutboxMessage))
        }
        assert MEMBER_EMAIL not in recipients
        # Still posted, so it is there when they open the app.
        db.session.refresh(announcement)
        assert len(announcement.messages) == 1

    def test_posting_without_the_box_ticked_emails_nobody(
        self, db, announcement, alicia, staff
    ):
        staff.post(
            f"/messages/{announcement.id}/post/",
            data={"body": "Potluck on Sunday"},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_a_room_post_never_emails_the_church(self, db, room, alicia, other, staff):
        staff.post(
            f"/messages/{room.id}/post/",
            data={"body": "Rehearsal moved", "also_email": "on"},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(db.select(OutboxMessage)).all() == []
