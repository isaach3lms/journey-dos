"""Filtering, reporting, blocking, and the community standards.

App Store Guideline 1.2 asks four things of an app where people post content
other people read: agree to terms with no tolerance for objectionable content,
filter it, let people report it, and let people block whoever wrote it. The
reviewer's recording has to show them, so each is tested here as behaviour
rather than as the presence of a button.

Every test uses one signed-in client at most. The `db` fixture holds one
application context open and Flask-Login caches the user on `g`, so a second
client in the same test inherits the first one's identity.
"""

import pytest

from app.moderation import is_allowed, objectionable_terms
from app.models import (
    AuditEvent,
    Church,
    Conversation,
    ConversationMember,
    Message,
    MessageReport,
    OutboxMessage,
    Person,
    PersonBlock,
    User,
)
from app.models.moderation import (
    REPORT_DISMISSED,
    REPORT_OPEN,
    REPORT_REMOVED,
    SOURCE_BLOCK,
    SOURCE_REPORT,
)
from tests.conftest import JOURNEY_HOST

MEMBER_EMAIL = "member@journeychurchsemo.com"
WORDS = "Anybody up for coffee after the late service?"


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def alicia(db, journey):
    """The person behind the `member` client."""
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
def marcus(db, journey):
    person = Person(
        church_id=journey.id, first_name="Marcus", last_name="Webb", stage="member"
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def room(db, journey, alicia, marcus):
    conversation = Conversation(church_id=journey.id, kind="room", title="Young adults")
    db.session.add(conversation)
    db.session.flush()
    for person in (alicia, marcus):
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=conversation.id, person_id=person.id
            )
        )
    db.session.commit()
    return conversation


@pytest.fixture
def his_message(db, room, marcus):
    message = Message.post(room, marcus, WORDS)
    db.session.commit()
    return message


def thread_url(conversation):
    return f"/me/chat/{conversation.id}/"


def report_url(conversation, message):
    return f"/me/chat/{conversation.id}/messages/{message.id}/report/"


def block_url(conversation, message):
    return f"/me/chat/{conversation.id}/messages/{message.id}/block/"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

class TestTheFilter:
    @pytest.mark.parametrize("text", [
        "this is fucking ridiculous", "what a load of shit", "sh1t", "f*ck off",
        "shiiiiit", "you absolute BASTARD", "$hit happens", "pissed off",
    ])
    def test_it_catches_the_obvious(self, text):
        assert objectionable_terms(text), text

    @pytest.mark.parametrize("text", [
        "Fear him who can destroy both soul and body in hell.",
        "I'll be damned if I miss Sunday",
        "Reading Dickens with the youth group",
        "Hancock County prayer walk",
        "Bring a cocktail sauce for the shrimp",
        "Mississippi mission trip",
        "The assessment is due Friday",
        "Scunthorpe is a real town",
    ])
    def test_it_leaves_ordinary_church_sentences_alone(self, text):
        """A filter that rejects a message quoting Matthew 10 is a filter
        people learn to route around."""
        assert is_allowed(text), text

    def test_it_says_which_word(self):
        assert objectionable_terms("this is shit") == ["shit"]

    def test_empty_text_is_fine(self):
        assert is_allowed("") and is_allowed(None)

    def test_a_refused_message_is_not_stored(self, db, room, member):
        r = member.post(
            thread_url(room), data={"body": "this is shit"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"was not posted" in r.data
        assert db.session.scalars(db.select(Message)).all() == []

    def test_a_clean_message_posts(self, db, room, member):
        member.post(thread_url(room), data={"body": WORDS}, headers={"Host": JOURNEY_HOST})
        assert db.session.scalars(db.select(Message)).one().body == WORDS

    def test_staff_are_held_to_the_same_rule(self, db, journey, staff):
        """One rule for the room is easier to defend than two."""
        announcement = Conversation(
            church_id=journey.id, kind="announcement", title="Church-wide"
        )
        db.session.add(announcement)
        db.session.commit()
        r = staff.post(
            f"/messages/{announcement.id}/post/", data={"body": "shit happens"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"was not posted" in r.data
        assert db.session.scalars(db.select(Message)).all() == []


# ---------------------------------------------------------------------------
# Agreeing to the standards
# ---------------------------------------------------------------------------

class TestTheStandardsComeFirst:
    def _unaccept(self, db, journey):
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        user.community_accepted_at = None
        db.session.commit()
        return user

    def test_chat_shows_the_standards_until_agreed(self, db, journey, alicia, room, member):
        self._unaccept(db, journey)
        r = member.get("/me/chat/", headers={"Host": JOURNEY_HOST})
        assert b"Before you open chat" in r.data
        assert b"I agree" in r.data

    def test_a_thread_is_gated_too(self, db, journey, room, member):
        self._unaccept(db, journey)
        r = member.get(thread_url(room), headers={"Host": JOURNEY_HOST})
        assert b"Before you open chat" in r.data

    def test_nobody_posts_before_agreeing(self, db, journey, room, member):
        self._unaccept(db, journey)
        member.post(thread_url(room), data={"body": WORDS}, headers={"Host": JOURNEY_HOST})
        assert db.session.scalars(db.select(Message)).all() == []

    def test_agreeing_opens_chat(self, db, journey, room, member):
        user = self._unaccept(db, journey)
        member.post("/me/chat/agree/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(user)
        assert user.has_accepted_community
        r = member.get("/me/chat/", headers={"Host": JOURNEY_HOST})
        assert b"Before you open chat" not in r.data

    def test_a_new_account_starts_unagreed(self, db, journey):
        """Fail-closed, like email verification."""
        user = User(church_id=journey.id, email="new@example.com", name="New", role="member")
        user.set_password("a-long-enough-passphrase")
        db.session.add(user)
        db.session.commit()
        assert not user.has_accepted_community


class TestTheStandardsPage:
    def test_it_is_public(self, client, journey):
        assert client.get("/community/", headers={"Host": JOURNEY_HOST}).status_code == 200

    def test_it_says_there_is_no_tolerance(self, client, journey):
        """The phrase Guideline 1.2 asks the terms to make clear."""
        body = client.get("/community/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "no tolerance" in body

    def test_it_explains_reporting_and_blocking(self, client, journey):
        body = client.get("/community/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "report" in body and "block" in body

    def test_support_links_to_it(self, client, journey):
        assert b"/community/" in client.get("/support/", headers={"Host": JOURNEY_HOST}).data

    def test_support_answers_the_unkind_chat_question(self, client, journey):
        body = client.get("/support/", headers={"Host": JOURNEY_HOST}).get_data(as_text=True)
        assert "unkind in chat" in body


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class TestReporting:
    def test_every_message_from_somebody_else_offers_it(self, db, room, his_message, member):
        r = member.get(thread_url(room), headers={"Host": JOURNEY_HOST})
        assert report_url(room, his_message).encode() in r.data

    def test_your_own_message_does_not(self, db, room, alicia, member):
        mine = Message.post(room, alicia, "my own words")
        db.session.commit()
        r = member.get(thread_url(room), headers={"Host": JOURNEY_HOST})
        assert report_url(room, mine).encode() not in r.data

    def test_it_files_a_report(self, db, room, his_message, alicia, member):
        member.post(
            report_url(room, his_message), data={"reason": "Mocking someone"},
            headers={"Host": JOURNEY_HOST},
        )
        report = db.session.scalars(db.select(MessageReport)).one()
        assert report.status == REPORT_OPEN
        assert report.source == SOURCE_REPORT
        assert report.reporter_person_id == alicia.id
        assert report.reason == "Mocking someone"

    def test_a_reason_is_optional(self, db, room, his_message, member):
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert db.session.scalars(db.select(MessageReport)).one().reason is None

    def test_one_report_does_not_remove_the_message(self, db, room, his_message, member):
        """In a room of twelve, one unhappy reader could silence anybody."""
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        db.session.refresh(his_message)
        assert not his_message.is_deleted

    def test_reporting_twice_is_one_report(self, db, room, his_message, member):
        for _ in range(2):
            member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert len(db.session.scalars(db.select(MessageReport)).all()) == 1

    def test_the_report_holds_no_copy_of_the_words(self, db, room, his_message, member):
        """If staff remove the message, the words must be gone everywhere."""
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        columns = {c.name for c in MessageReport.__table__.columns}
        assert "body" not in columns and "text" not in columns

    def test_staff_are_emailed_straight_away(self, db, room, his_message, member):
        """Timely responses to concerns are part of Guideline 1.2."""
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        alerts = db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.category == "moderation")
        ).all()
        assert alerts
        assert "pastor@journeychurchsemo.com" in {m.to_email for m in alerts}

    def test_the_alert_does_not_quote_the_message(self, db, room, his_message, member):
        """A copy in every staff inbox cannot be taken back."""
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        for alert in db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.category == "moderation")
        ):
            assert WORDS not in alert.body_text

    def test_it_is_audited(self, db, journey, room, his_message, member):
        member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("was reported" in e.summary for e in events)

    def test_nobody_reports_their_own_message(self, db, room, alicia, member):
        mine = Message.post(room, alicia, "my own words")
        db.session.commit()
        r = member.post(
            report_url(room, mine), headers={"Host": JOURNEY_HOST}, follow_redirects=True
        )
        assert b"your own message" in r.data
        assert db.session.scalars(db.select(MessageReport)).all() == []

    def test_a_private_room_you_are_not_in_is_a_404(self, db, journey, marcus, alicia, member):
        """Reporting must not become a way to learn a room exists."""
        private = Conversation(church_id=journey.id, kind="room", title="Elders")
        db.session.add(private)
        db.session.flush()
        db.session.add(
            ConversationMember(church_id=journey.id, conversation_id=private.id, person_id=marcus.id)
        )
        message = Message.post(private, marcus, "private words")
        db.session.commit()

        r = member.post(report_url(private, message), headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404

    def test_a_deleted_message_cannot_be_reported(self, db, room, his_message, member):
        his_message.soft_delete()
        db.session.commit()
        r = member.post(report_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

class TestBlocking:
    def test_it_hides_their_messages_straight_away(self, db, room, his_message, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        r = member.get(thread_url(room), headers={"Host": JOURNEY_HOST})
        assert WORDS.encode() not in r.data
        assert b"hidden from people you blocked" in r.data

    def test_it_needs_nobodys_permission(self, db, room, his_message, alicia, marcus, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert marcus.id in PersonBlock.blocked_ids(alicia.church_id, alicia.id)

    def test_everybody_else_still_sees_the_message(self, db, journey, room, his_message, marcus, member):
        """The block protects the person who asked, not the whole room."""
        other = Person(church_id=journey.id, first_name="Priya", last_name="Anand", stage="member")
        db.session.add(other)
        db.session.commit()
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        blocked_for_priya = PersonBlock.blocked_ids(journey.id, other.id)
        assert his_message in room.messages_for(other.id, blocked_for_priya)

    def test_it_files_a_report_so_staff_know(self, db, room, his_message, member):
        """Blocking usually means something happened."""
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        report = db.session.scalars(db.select(MessageReport)).one()
        assert report.source == SOURCE_BLOCK
        assert report.status == REPORT_OPEN

    def test_staff_are_emailed(self, db, room, his_message, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert db.session.scalars(
            db.select(OutboxMessage).where(OutboxMessage.category == "moderation")
        ).all()

    def test_their_messages_stop_counting_as_unread(self, db, room, his_message, alicia, member):
        """Otherwise the badge keeps pointing at what you asked never to see."""
        assert room.unread_for(alicia.id) == 1
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        blocked = PersonBlock.blocked_ids(alicia.church_id, alicia.id)
        assert room.unread_for(alicia.id, blocked) == 0
        assert Message.unread_total(alicia.church_id, alicia.id) == 0

    def test_it_is_audited(self, db, journey, room, his_message, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("blocked Marcus Webb" in e.summary for e in events)

    def test_blocking_twice_is_one_block(self, db, room, his_message, member):
        for _ in range(2):
            member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        assert len(db.session.scalars(db.select(PersonBlock)).all()) == 1

    def test_nobody_blocks_themselves(self, db, room, alicia, member):
        mine = Message.post(room, alicia, "my own words")
        db.session.commit()
        r = member.post(block_url(room, mine), headers={"Host": JOURNEY_HOST}, follow_redirects=True)
        assert b"cannot block yourself" in r.data
        assert db.session.scalars(db.select(PersonBlock)).all() == []

    def test_the_database_refuses_a_self_block_too(self, db, journey, alicia):
        db.session.add(
            PersonBlock(church_id=journey.id, blocker_person_id=alicia.id, blocked_person_id=alicia.id)
        )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


class TestUnblocking:
    def test_the_you_screen_lists_blocks(self, db, room, his_message, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        r = member.get("/me/you/", headers={"Host": JOURNEY_HOST})
        assert b"People you blocked" in r.data
        assert b"Marcus Webb" in r.data

    def test_unblocking_restores_their_messages(self, db, room, his_message, member):
        member.post(block_url(room, his_message), headers={"Host": JOURNEY_HOST})
        block = db.session.scalars(db.select(PersonBlock)).one()
        member.post(f"/me/you/blocks/{block.id}/remove/", headers={"Host": JOURNEY_HOST})
        r = member.get(thread_url(room), headers={"Host": JOURNEY_HOST})
        assert WORDS.encode() in r.data

    def test_nobody_removes_somebody_elses_block(self, db, journey, marcus, member, alicia):
        other = Person(church_id=journey.id, first_name="Priya", last_name="Anand", stage="member")
        db.session.add(other)
        db.session.flush()
        theirs = PersonBlock(
            church_id=journey.id, blocker_person_id=other.id,
            blocked_person_id=marcus.id, blocked_name="Marcus Webb",
        )
        db.session.add(theirs)
        db.session.commit()

        r = member.post(f"/me/you/blocks/{theirs.id}/remove/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404
        assert db.session.get(PersonBlock, theirs.id) is not None


# ---------------------------------------------------------------------------
# Staff deciding
# ---------------------------------------------------------------------------

@pytest.fixture
def open_report(db, room, his_message, alicia):
    report, _ = MessageReport.file(his_message, alicia, "Mocking someone")
    db.session.commit()
    return report


class TestStaffDecide:
    def test_the_queue_lists_open_reports(self, db, open_report, staff):
        r = staff.get("/messages/reports/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Reported by Alicia Romero" in r.data
        assert WORDS.encode() in r.data

    def test_the_messages_screen_points_at_it(self, db, open_report, staff):
        r = staff.get("/messages/", headers={"Host": JOURNEY_HOST})
        assert b"Reported messages (1)" in r.data

    def test_removing_deletes_the_message_and_closes_the_report(
        self, db, open_report, his_message, staff
    ):
        staff.post(f"/messages/reports/{open_report.id}/remove/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(his_message)
        db.session.refresh(open_report)
        assert his_message.is_deleted
        assert his_message.body is None
        assert open_report.status == REPORT_REMOVED
        assert open_report.resolved_by_name

    def test_every_report_on_the_message_closes_together(
        self, db, journey, open_report, his_message, staff
    ):
        """Three people reporting one message is one decision."""
        other = Person(church_id=journey.id, first_name="Priya", last_name="Anand", stage="member")
        db.session.add(other)
        db.session.flush()
        second, _ = MessageReport.file(his_message, other)
        db.session.commit()

        staff.post(f"/messages/reports/{open_report.id}/remove/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(second)
        assert second.status == REPORT_REMOVED

    def test_keeping_leaves_the_message(self, db, open_report, his_message, staff):
        staff.post(f"/messages/reports/{open_report.id}/keep/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(his_message)
        db.session.refresh(open_report)
        assert not his_message.is_deleted
        assert open_report.status == REPORT_DISMISSED

    def test_decisions_are_audited(self, db, journey, open_report, staff):
        staff.post(f"/messages/reports/{open_report.id}/keep/", headers={"Host": JOURNEY_HOST})
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("was decided: kept" in e.summary for e in events)

    def test_deleting_from_the_thread_closes_reports_too(
        self, db, room, open_report, his_message, staff
    ):
        staff.post(
            f"/messages/{room.id}/messages/{his_message.id}/delete/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(open_report)
        assert open_report.status == REPORT_REMOVED

    def test_a_leader_can_decide(self, db, open_report, leader):
        r = leader.get("/messages/reports/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200

    def test_a_member_cannot(self, db, open_report, member):
        assert member.get("/messages/reports/", headers={"Host": JOURNEY_HOST}).status_code == 403
        r = member.post(f"/messages/reports/{open_report.id}/remove/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 403

    def test_an_unknown_decision_is_refused(self, db, open_report, staff):
        r = staff.post(f"/messages/reports/{open_report.id}/nuke/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 400

    def test_a_report_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        author = Person(church_id=riverbend.id, first_name="Their", last_name="Author", stage="member")
        reporter = Person(church_id=riverbend.id, first_name="Their", last_name="Reporter", stage="member")
        conversation = Conversation(church_id=riverbend.id, kind="room", title="Theirs")
        db.session.add_all([author, reporter, conversation])
        db.session.flush()
        message = Message.post(conversation, author, "their words")
        db.session.flush()
        report, _ = MessageReport.file(message, reporter)
        db.session.commit()

        r = staff.post(f"/messages/reports/{report.id}/remove/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404
        db.session.refresh(message)
        assert not message.is_deleted
