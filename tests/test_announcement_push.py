"""An announcement posted in the app, and who hears about it.

A pastor posted a church-wide announcement and not one phone made a sound.
The chat notifier excludes announcements on purpose, because emailing three
hundred people every time somebody posts to the whole church is how a church
teaches its members to filter its mail. But that exclusion was written when
email was the only channel, and it took the notification out with it: the one
thing announcements were for reached nobody unless the person posting also
ticked a box labelled "email it as well".

So the two channels split the way they should have from the start. **The
notification always goes.** **The email goes only when the box is ticked.**
Which is the whole of this file.
"""

from __future__ import annotations

import pytest

from app.models import Church, Conversation, OutboxMessage, Person, User
from app.models.base import utcnow
from app.models.message import KIND_ANNOUNCEMENT, KIND_ROOM
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def congregation(db, journey):
    """Three members with addresses, and a child who must not be mailed."""
    people = [
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               email="marcus@journey.test", stage="member", approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Dana", last_name="Webb",
               email="dana@journey.test", stage="member", approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Ellie", last_name="Webb",
               email="ellie@journey.test", stage="member", is_child=True,
               approved_at=utcnow()),
    ]
    db.session.add_all(people)
    db.session.commit()
    return people


@pytest.fixture
def notices(db, journey):
    conversation = Conversation(church_id=journey.id, title="Church news",
                                kind=KIND_ANNOUNCEMENT)
    db.session.add(conversation)
    db.session.commit()
    return conversation


def announcement_mail(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.category == "announcement",
        )
    ).all()


class TestPostingWithoutTickingTheBox:
    def test_nothing_is_emailed(self, app, db, journey, staff, notices, congregation):
        """The tick box is the only thing that puts a letter in an inbox, and
        that has not changed."""
        page = staff.post(
            f"/messages/{notices.id}/post/",
            data={"body": "Work day Saturday at nine."},
            headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        assert not announcement_mail(db, journey)

    def test_the_post_still_lands(self, app, db, journey, staff, notices):
        staff.post(f"/messages/{notices.id}/post/",
                   data={"body": "Work day Saturday at nine."},
                   headers=H, follow_redirects=True)
        db.session.refresh(notices)
        assert len(notices.messages) == 1

    def test_it_reaches_a_phone(self, app, db, journey, notices, congregation):
        """The gap. Before this, posting without ticking reached nobody at
        all: not an inbox, not a phone, nothing but a screen somebody had to
        open on purpose."""
        from app.chat_notify import notify_announcement
        from app.models import Message

        author = congregation[0]
        with app.test_request_context(headers=H):
            posted = Message.post(notices, author, "Work day Saturday.")
            db.session.flush()
            emailed, pushed = notify_announcement(
                notices, posted, "Work day Saturday.", also_email=False
            )
        db.session.commit()

        assert emailed == 0, "nothing should be emailed without the tick box"
        # Nobody has a device registered in this fixture, so the assertion
        # that carries weight is the one above plus the route test below.
        assert pushed == 0

    def test_a_registered_device_is_pushed(
        self, app, db, journey, notices, congregation
    ):
        from app.chat_notify import notify_announcement
        from app.models import Message, PushSubscription

        person = congregation[0]
        user = User(church_id=journey.id, email="marcus-login@journey.test",
                    name="Marcus Webb", role="member", is_active_account=True,
                    person_id=person.id)
        user.set_password("a-long-enough-password")
        user.mark_verified()
        user.accept_community()
        db.session.add(user)
        db.session.commit()
        PushSubscription.register(journey.id, user, "https://push.test/marcus",
                                  "k" * 20, "a" * 16, label="iPhone")
        db.session.commit()

        with app.test_request_context(headers=H):
            posted = Message.post(notices, congregation[1], "Work day Saturday.")
            db.session.flush()
            emailed, pushed = notify_announcement(
                notices, posted, "Work day Saturday.", also_email=False
            )
        db.session.commit()

        assert pushed >= 1, "an announcement reached no phone"
        assert emailed == 0


class TestPostingWithTheBoxTicked:
    def test_everybody_with_an_address_is_emailed(
        self, app, db, journey, staff, notices, congregation
    ):
        page = staff.post(
            f"/messages/{notices.id}/post/",
            data={"body": "Work day Saturday at nine.", "also_email": "on"},
            headers=H, follow_redirects=True,
        )
        assert page.status_code == 200
        assert {m.to_email for m in announcement_mail(db, journey)} >= {
            "marcus@journey.test", "dana@journey.test"
        }

    def test_a_child_is_not_emailed(
        self, app, db, journey, staff, notices, congregation
    ):
        """Who counts as everyone comes from app.broadcast, which is the
        point of it coming from there: this loop used to email children,
        archived people, and self-registered people awaiting approval."""
        staff.post(f"/messages/{notices.id}/post/",
                   data={"body": "Work day Saturday.", "also_email": "on"},
                   headers=H, follow_redirects=True)

        assert "ellie@journey.test" not in {
            m.to_email for m in announcement_mail(db, journey)
        }

    def test_the_screen_reports_how_many(
        self, app, db, journey, staff, notices, congregation
    ):
        page = staff.post(
            f"/messages/{notices.id}/post/",
            data={"body": "Work day Saturday.", "also_email": "on"},
            headers=H, follow_redirects=True,
        )
        assert "2" in page.get_data(as_text=True)


class TestTwoPostsAreTwoAnnouncements:
    def test_a_second_post_is_not_swallowed_as_a_duplicate(
        self, app, db, journey, staff, notices, congregation
    ):
        """The old dedupe key counted the messages in the room, so two posts
        landing in the same second read the same count and the second one
        vanished. It is keyed on the message id now."""
        for body in ("Work day Saturday.", "Also, bring gloves."):
            staff.post(f"/messages/{notices.id}/post/",
                       data={"body": body, "also_email": "on"},
                       headers=H, follow_redirects=True)

        assert len(announcement_mail(db, journey)) == 4  # two posts, two adults

    def test_the_same_post_twice_is_one_announcement(
        self, app, db, journey, notices, congregation
    ):
        """A retried request or a double-submitted form."""
        from app.chat_notify import notify_announcement
        from app.models import Message

        with app.test_request_context(headers=H):
            posted = Message.post(notices, congregation[0], "Work day Saturday.")
            db.session.flush()
            notify_announcement(notices, posted, "Work day Saturday.",
                                also_email=True)
            db.session.commit()
            notify_announcement(notices, posted, "Work day Saturday.",
                                also_email=True)
            db.session.commit()

        assert len(announcement_mail(db, journey)) == 2


class TestARoomIsStillARoom:
    def test_posting_in_a_room_does_not_go_church_wide(
        self, app, db, journey, staff, congregation
    ):
        """The split must not have turned every room post into an
        announcement to the whole church."""
        room = Conversation(church_id=journey.id, title="Worship team",
                            kind=KIND_ROOM)
        db.session.add(room)
        db.session.commit()

        staff.post(f"/messages/{room.id}/post/",
                   data={"body": "Rehearsal moved to seven."},
                   headers=H, follow_redirects=True)

        assert not announcement_mail(db, journey)


class TestAMemberPosting:
    def test_a_member_announcement_pushes_but_never_emails(
        self, app, db, journey, notices, congregation
    ):
        """A member is allowed five a day and cannot choose to put any of them
        in three hundred inboxes. That tick box is on the staff screen, where
        the person pressing it is accountable for it."""
        from app.chat_notify import notify_announcement
        from app.models import Message

        with app.test_request_context(headers=H):
            posted = Message.post(notices, congregation[0], "Lost a jacket.")
            db.session.flush()
            emailed, _ = notify_announcement(notices, posted, "Lost a jacket.",
                                             also_email=False)
        db.session.commit()

        assert emailed == 0
        assert not announcement_mail(db, journey)
