"""One-to-one conversations, and who is allowed to open one.

The policy, which is the whole point of the feature: **staff and leaders
start, members reply.** A church directory with open one-to-one messaging is
a directory where any member can write privately to any other member,
including to the sixteen year olds in it, with nobody able to see that it
happened. Every test in `TestWhoMayStartOne` exists to keep that shut.

The second rule is that a direct thread is private from the rest of the
church, including from staff who are not in it. Being able to moderate the
group chats is not a key to everybody's direct messages.
"""

import pytest

from app.models import (
    Church,
    Conversation,
    ConversationMember,
    Message,
    Person,
    User,
)
from app.models.message import KIND_DIRECT
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def add_person(db, church, first, last="Vance", **kw):
    person = Person(
        church_id=church.id, first_name=first, last_name=last,
        email=kw.pop("email", f"{first.lower()}@example.com"),
        stage=kw.pop("stage", "visitor"), **kw,
    )
    db.session.add(person)
    db.session.commit()
    return person


def link(db, email, person):
    """Attach a roster record to one of the fixture logins."""
    user = db.session.scalar(
        db.select(User).where(
            User.church_id == person.church_id, User.email == email
        )
    )
    user.person_id = person.id
    db.session.commit()
    return user


@pytest.fixture
def reed(db, journey):
    """The staff login, with a person behind it."""
    person = add_person(db, journey, "Reed", "Barrett", stage="leader")
    link(db, "pastor@journeychurchsemo.com", person)
    return person


@pytest.fixture
def dana(db, journey):
    """The leader login, with a person behind it."""
    person = add_person(db, journey, "Dana", "Webb", stage="leader")
    link(db, "leader@journeychurchsemo.com", person)
    return person


@pytest.fixture
def alicia(db, journey):
    """The member login, with a person behind it."""
    person = add_person(db, journey, "Alicia", "Romero", stage="member")
    link(db, "member@journeychurchsemo.com", person)
    return person


@pytest.fixture
def amy(db, journey):
    return add_person(db, journey, "Amy", "Davis")


def directs(db, journey):
    return db.session.scalars(
        db.select(Conversation).where(
            Conversation.church_id == journey.id,
            Conversation.kind == KIND_DIRECT,
        )
    ).all()


# ---------------------------------------------------------------------------
# Two people in one test
#
# The `db` fixture holds one application context open for the whole test, and
# Flask-Login caches the signed-in user on it. A second sign-in therefore
# inherits the first one's identity, and a privacy test written that way
# passes because the "other" staff member was never anybody else. These
# tests take the application and open their own contexts instead, which is
# the pattern `test_church_email.py` established for the same reason.
# ---------------------------------------------------------------------------

def signed_in(app, email):
    client = app.test_client()
    client.post("/auth/login", data={"email": email, "password": PASSWORD},
                headers=H, follow_redirects=True)
    return client


def seed_people(app):
    """Reed on staff, Dana leading, Alicia a member, all on the roster."""
    with app.app_context():
        from app.extensions import db as _db

        church = _db.session.scalar(
            _db.select(Church).where(Church.slug == "journey")
        )
        roster = {
            "reed": ("Reed", "Barrett", "leader", "pastor@journeychurchsemo.com"),
            "dana": ("Dana", "Webb", "leader", "leader@journeychurchsemo.com"),
            "alicia": ("Alicia", "Romero", "member", "member@journeychurchsemo.com"),
            "amy": ("Amy", "Davis", "visitor", None),
        }
        out = {}
        for key, (first, last, stage, login) in roster.items():
            person = Person(
                church_id=church.id, first_name=first, last_name=last,
                email=f"{key}@example.com", stage=stage, approved_at=utcnow(),
            )
            _db.session.add(person)
            _db.session.flush()
            if login:
                user = _db.session.scalar(
                    _db.select(User).where(
                        User.church_id == church.id, User.email == login
                    )
                )
                user.person_id = person.id
            out[key] = person.id
        _db.session.commit()
        out["church"] = church.id
        return out


def only_direct(app, church_id):
    from app.extensions import db as _db

    return _db.session.scalars(
        _db.select(Conversation).where(
            Conversation.church_id == church_id,
            Conversation.kind == KIND_DIRECT,
        )
    ).all()


class TestWhoMayStartOne:
    def test_staff_can(self, db, journey, reed, amy, staff):
        staff.post("/messages/direct/", data={"person_id": amy.id},
                   headers=H, follow_redirects=True)
        assert len(directs(db, journey)) == 1

    def test_a_leader_can(self, db, journey, dana, amy, leader):
        leader.post("/messages/direct/", data={"person_id": amy.id},
                    headers=H, follow_redirects=True)
        assert len(directs(db, journey)) == 1

    def test_a_member_cannot(self, db, journey, alicia, amy, member):
        """The rule. A member has no route to open a private channel with
        another member, which is what keeps this out of the gap between a
        church directory and a social network."""
        response = member.post("/messages/direct/", data={"person_id": amy.id},
                               headers=H)
        assert response.status_code in (302, 403)
        assert directs(db, journey) == []

    def test_signed_out_cannot(self, db, journey, amy, client):
        client.post("/messages/direct/", data={"person_id": amy.id}, headers=H)
        assert directs(db, journey) == []

    def test_the_model_says_so_on_its_own(self):
        """The policy is a model method, so the route, the template and any
        future caller cannot each have their own opinion of it."""
        assert Conversation.may_start_direct(is_staff=True) is True
        assert Conversation.may_start_direct(is_leader=True) is True
        assert Conversation.may_start_direct() is False

    def test_the_sidebar_offers_it_to_staff(self, db, journey, reed, staff):
        body = staff.get("/messages/", headers=H).data.decode()
        assert "/messages/direct/" in body

    def test_and_not_to_a_member(self, db, journey, alicia, member):
        """The member app has no starter either. A form a member cannot
        submit is still a form that tells them the feature is theirs."""
        body = member.get("/me/chat/", headers=H).data.decode()
        assert "/messages/direct/" not in body


class TestStartingOne:
    def test_it_lands_in_the_thread(self, db, journey, reed, amy, staff):
        response = staff.post("/messages/direct/", data={"person_id": amy.id},
                              headers=H, follow_redirects=True)
        assert amy.full_name in response.data.decode()

    def test_both_people_are_in_it(self, db, journey, reed, amy, staff):
        staff.post("/messages/direct/", data={"person_id": amy.id},
                   headers=H, follow_redirects=True)
        conversation = directs(db, journey)[0]
        assert {m.person_id for m in conversation.members} == {reed.id, amy.id}

    def test_starting_it_twice_opens_the_same_one(self, db, journey, reed, amy, staff):
        """Otherwise the history splits in two and each of them holds half
        the conversation."""
        for _ in range(2):
            staff.post("/messages/direct/", data={"person_id": amy.id},
                       headers=H, follow_redirects=True)
        assert len(directs(db, journey)) == 1

    def test_either_side_starting_it_finds_the_same_one(
        self, db, journey, reed, dana, staff, leader
    ):
        """`direct_pair` is sorted, so who typed first does not matter."""
        staff.post("/messages/direct/", data={"person_id": dana.id},
                   headers=H, follow_redirects=True)
        leader.post("/messages/direct/", data={"person_id": reed.id},
                    headers=H, follow_redirects=True)
        assert len(directs(db, journey)) == 1

    def test_not_with_yourself(self, db, journey, reed, staff):
        staff.post("/messages/direct/", data={"person_id": reed.id},
                   headers=H, follow_redirects=True)
        assert directs(db, journey) == []

    def test_not_with_somebody_at_another_church(self, db, journey, reed, staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = add_person(db, riverbend, "Sam", "Okafor")
        staff.post("/messages/direct/", data={"person_id": theirs.id},
                   headers=H, follow_redirects=True)
        assert directs(db, journey) == []

    def test_a_staff_login_with_no_roster_record_is_told_why(
        self, db, journey, amy, staff
    ):
        """There is nobody for the other side of the conversation to be
        talking to, and a silent failure here looks like a broken button."""
        response = staff.post("/messages/direct/", data={"person_id": amy.id},
                              headers=H, follow_redirects=True)
        assert directs(db, journey) == []
        assert "not attached to a person" in response.data.decode()


class TestItIsPrivate:
    @pytest.fixture
    def thread(self, db, journey, dana, amy, leader):
        leader.post("/messages/direct/", data={"person_id": amy.id},
                    headers=H, follow_redirects=True)
        return directs(db, journey)[0]

    def test_somebody_in_it_can_read_it(self, db, thread, dana):
        assert thread.can_read(dana) is True

    def test_another_staff_member_cannot(self, db, thread, reed):
        """Staff run the church's rooms. This is not one of the church's
        rooms."""
        assert thread.can_read(reed) is False

    def test_and_cannot_write_into_it(self, db, thread, reed):
        assert thread.can_post(reed, is_staff=True) is False

    def test_a_third_person_cannot_be_added(self, db, journey, thread, dana,
                                            alicia, leader):
        """Two people agreed to this conversation. A member list would make
        the title a lie and the pair key wrong."""
        response = leader.post(
            f"/messages/{thread.id}/members/",
            data={"person_id": alicia.id}, headers=H,
        )
        assert response.status_code == 400
        assert len(thread.members) == 2

    def test_it_is_not_in_the_group_list(self, db, journey, thread):
        """The staff sidebar lists the church's conversations. Everybody's
        direct threads is a different thing, and listing them would read as
        surveillance."""
        groups = db.session.scalars(
            Conversation.groups_for_church(journey.id)
        ).all()
        assert thread not in groups

    def test_it_is_in_the_list_of_the_people_in_it(self, db, journey, thread, dana):
        mine = db.session.scalars(
            Conversation.directs_for(journey.id, dana.id)
        ).all()
        assert mine == [thread]

    def test_and_not_in_anybody_elses(self, db, journey, thread, reed):
        mine = db.session.scalars(
            Conversation.directs_for(journey.id, reed.id)
        ).all()
        assert mine == []

    def test_a_login_with_no_person_has_none(self, db, journey, thread):
        assert db.session.scalars(
            Conversation.directs_for(journey.id, None)
        ).all() == []


class TestMembersReply:
    @pytest.fixture
    def thread(self, db, journey, dana, alicia, leader):
        leader.post("/messages/direct/", data={"person_id": alicia.id},
                    headers=H, follow_redirects=True)
        return directs(db, journey)[0]

    def test_the_member_may_post(self, db, thread, alicia):
        assert thread.can_post(alicia) is True

    # Replying through the member app, and the member's own chat list, are
    # in `TestPrivateFromTheOtherStaff`: they need the member to really be
    # somebody other than the leader who opened the thread.


class TestWhatItIsCalled:
    def test_the_row_keeps_both_names(self, db, journey, reed, amy, staff):
        staff.post("/messages/direct/", data={"person_id": amy.id},
                   headers=H, follow_redirects=True)
        conversation = directs(db, journey)[0]
        assert reed.full_name in conversation.title
        assert amy.full_name in conversation.title

    def test_but_each_side_sees_the_other_person(self, db, journey, reed, amy, staff):
        staff.post("/messages/direct/", data={"person_id": amy.id},
                   headers=H, follow_redirects=True)
        conversation = directs(db, journey)[0]
        assert conversation.title_for(reed.id) == amy.full_name
        assert conversation.title_for(amy.id) == reed.full_name

    def test_a_room_is_called_what_it_is_called(self, db, journey, reed):
        room = Conversation(church_id=journey.id, kind="room", title="Worship Team")
        db.session.add(room)
        db.session.commit()
        assert room.title_for(reed.id) == "Worship Team"

    def test_a_room_has_no_other_person(self, db, journey, reed, amy):
        """"The other one" is only meaningful where there are two. This is
        also what stops a room saving a template: there is nobody for the
        placeholders to stand for, so nothing is offered to save."""
        room = Conversation(church_id=journey.id, kind="room", title="Worship Team")
        db.session.add(room)
        db.session.flush()
        for person in (reed, amy):
            db.session.add(ConversationMember(
                church_id=journey.id, conversation_id=room.id,
                person_id=person.id,
            ))
        db.session.commit()

        assert room.other_member(reed.id) is None


class TestUnreadAndReading:
    @pytest.fixture
    def thread(self, db, journey, dana, alicia, leader):
        leader.post("/messages/direct/", data={"person_id": alicia.id},
                    headers=H, follow_redirects=True)
        return directs(db, journey)[0]

    # Unread across two people is in `TestPrivateFromTheOtherStaff`, for the
    # same reason: one client signed in twice is one person.

    def test_your_own_message_is_not_unread_to_you(
        self, db, journey, thread, dana, leader
    ):
        leader.post(f"/messages/{thread.id}/post/", data={"body": "Hello"},
                    headers=H, follow_redirects=True)
        db.session.expire_all()
        assert thread.unread_for(dana.id) == 0



class TestWhatTheOtherPersonIsTold:
    def test_the_email_does_not_read_like_a_room(
        self, db, journey, dana, alicia, leader
    ):
        """"Dana Webb posted in Dana Webb and Alicia Romero" is what the room
        wording produces on a thread whose title is both names."""
        from app.models import OutboxMessage

        leader.post("/messages/direct/", data={"person_id": alicia.id},
                    headers=H, follow_redirects=True)
        thread = directs(db, journey)[0]
        leader.post(f"/messages/{thread.id}/post/",
                    data={"body": "Glad you were with us."},
                    headers=H, follow_redirects=True)

        queued = db.session.scalars(
            OutboxMessage.for_person(journey.id, alicia.id)
        ).all()
        assert queued, "the other person should hear about it"
        assert queued[0].subject == f"{dana.full_name} sent you a message"
        assert "posted in" not in queued[0].subject


class TestPrivateFromTheOtherStaff:
    """The same rule as `TestItIsPrivate`, driven through real requests.

    Two genuinely different sign-ins, so a staff account really is somebody
    other than the leader who opened the thread. The model-level tests above
    would pass even if the route forgot to check.
    """

    def setup_thread(self, app):
        ids = seed_people(app)
        dana = signed_in(app, "leader@journeychurchsemo.com")
        dana.post("/messages/direct/", data={"person_id": ids["alicia"]},
                  headers=H, follow_redirects=True)
        with app.app_context():
            thread = only_direct(app, ids["church"])[0]
            ids["thread"] = thread.id
        return ids, dana

    def test_another_staff_member_gets_a_404(self, app):
        ids, _ = self.setup_thread(app)
        reed = signed_in(app, "pastor@journeychurchsemo.com")
        response = reed.get(f"/messages/{ids['thread']}/", headers=H)
        assert response.status_code == 404

    def test_another_staff_member_cannot_post_into_it(self, app):
        ids, _ = self.setup_thread(app)
        reed = signed_in(app, "pastor@journeychurchsemo.com")
        reed.post(f"/messages/{ids['thread']}/post/",
                  data={"body": "Butting in"}, headers=H)

        with app.app_context():
            from app.extensions import db as _db

            thread = _db.session.get(Conversation, ids["thread"])
            assert [m.body for m in thread.messages] == []

    def test_the_member_in_it_can_reply(self, app):
        ids, _ = self.setup_thread(app)
        alicia = signed_in(app, "member@journeychurchsemo.com")
        alicia.post(f"/me/chat/{ids['thread']}/",
                    data={"body": "Thanks, I will be there."},
                    headers=H, follow_redirects=True)

        with app.app_context():
            from app.extensions import db as _db

            thread = _db.session.get(Conversation, ids["thread"])
            assert [m.body for m in thread.messages] == ["Thanks, I will be there."]

    def test_it_is_on_the_members_own_chat_list(self, app):
        ids, _ = self.setup_thread(app)
        alicia = signed_in(app, "member@journeychurchsemo.com")
        body = alicia.get("/me/chat/", headers=H).data.decode()
        assert "Direct messages" in body
        assert "Dana Webb" in body

    def test_a_reply_is_unread_to_the_leader_until_opened(self, app):
        ids, dana = self.setup_thread(app)
        alicia = signed_in(app, "member@journeychurchsemo.com")
        alicia.post(f"/me/chat/{ids['thread']}/", data={"body": "Hello"},
                    headers=H, follow_redirects=True)

        with app.app_context():
            from app.extensions import db as _db

            thread = _db.session.get(Conversation, ids["thread"])
            assert thread.unread_for(ids["dana"]) == 1

        assert "chatbadge" in dana.get("/messages/", headers=H).data.decode()

        dana.get(f"/messages/{ids['thread']}/", headers=H)
        with app.app_context():
            from app.extensions import db as _db

            thread = _db.session.get(Conversation, ids["thread"])
            assert thread.unread_for(ids["dana"]) == 0
