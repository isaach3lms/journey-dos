"""Saving a direct message, and emailing one person from the Messages page.

Two halves of the same complaint: the church turned the automatic emails off
and is now writing to new people by hand, which means typing the same four
sentences for the fortieth time. A saved message is the same idea as a saved
email, and deliberately the same table, because the thing being saved is the
same thing.

The rule that keeps them apart is `kind`. A chat has no subject line, an
email does, and a picker that mixed them would silently drop one or offer
the other with a blank.
"""

import pytest

from app.models import (
    Church,
    Conversation,
    EmailTemplate,
    OutboxMessage,
    Person,
    User,
)
from app.models.base import utcnow
from app.models.email_template import TEMPLATE_EMAIL, TEMPLATE_MESSAGE
from app.models.message import KIND_DIRECT
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def add_person(db, church, first, last="Vance", stage="visitor"):
    person = Person(
        church_id=church.id, first_name=first, last_name=last,
        email=f"{first.lower()}@example.com", stage=stage,
        approved_at=utcnow(),
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture
def reed(db, journey):
    person = add_person(db, journey, "Reed", "Barrett", stage="leader")
    user = db.session.scalar(
        db.select(User).where(
            User.church_id == journey.id,
            User.email == "pastor@journeychurchsemo.com",
        )
    )
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def amy(db, journey):
    return add_person(db, journey, "Amy", "Davis")


@pytest.fixture
def rob(db, journey):
    return add_person(db, journey, "Rob", "Quinn")


def open_direct(client, person):
    client.post("/messages/direct/", data={"person_id": person.id},
                headers=H, follow_redirects=True)


def thread_with(db, journey, person):
    return db.session.scalar(
        db.select(Conversation).where(
            Conversation.church_id == journey.id,
            Conversation.kind == KIND_DIRECT,
        )
    )


def saved(db, journey, kind=TEMPLATE_MESSAGE):
    return db.session.scalars(
        EmailTemplate.for_church(journey.id, kind=kind)
    ).all()


def send(client, conversation, body, name=""):
    return client.post(
        f"/messages/{conversation.id}/post/",
        data={"body": body, "template_name": name},
        headers=H, follow_redirects=True,
    )


class TestSavingAMessage:
    def test_naming_it_keeps_it(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy, glad you came.",
             name="First visit")
        assert [t.name for t in saved(db, journey)] == ["First visit"]

    def test_leaving_the_name_blank_keeps_nothing(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy, glad you came.")
        assert saved(db, journey) == []

    def test_the_message_is_still_posted(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        send(staff, conversation, "Hi Amy, glad you came.", name="First visit")
        assert [m.body for m in conversation.messages] == ["Hi Amy, glad you came."]

    def test_it_is_saved_as_a_message_not_an_email(self, db, journey, reed, amy, staff):
        """The kind is what keeps the two pickers from offering each other's
        templates."""
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy.", name="First visit")
        assert saved(db, journey)[0].kind == TEMPLATE_MESSAGE
        assert saved(db, journey, kind=TEMPLATE_EMAIL) == []

    def test_the_name_comes_back_out(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy),
             "Hi Amy, so glad you were with us.", name="First visit")
        assert saved(db, journey)[0].body == \
            "Hi {first_name}, so glad you were with us."

    def test_what_was_actually_sent_keeps_the_real_name(
        self, db, journey, reed, amy, staff
    ):
        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        send(staff, conversation, "Hi Amy, glad you came.", name="First visit")
        assert conversation.messages[0].body == "Hi Amy, glad you came."

    def test_a_room_saves_nothing(self, db, journey, reed, staff):
        """A template is words written to a person. `{first_name}` has nobody
        to stand for in a room of thirty, and a saved one would arrive in the
        next room addressed to a placeholder."""
        room = Conversation(church_id=journey.id, kind="room", title="Worship Team")
        db.session.add(room)
        db.session.commit()

        send(staff, room, "Practice at 8.", name="Practice reminder")
        assert saved(db, journey) == []
        assert [m.body for m in room.messages] == ["Practice at 8."]


class TestUsingASavedMessage:
    def test_the_picker_offers_it_on_another_thread(
        self, db, journey, reed, amy, rob, staff
    ):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy.", name="First visit")

        open_direct(staff, rob)
        rob_thread = db.session.scalars(
            Conversation.directs_for(journey.id, reed.id)
        ).all()
        rob_thread = [c for c in rob_thread if c.other_member(reed.id).person_id == rob.id][0]
        body = staff.get(f"/messages/{rob_thread.id}/", headers=H).data.decode()
        assert "First visit" in body

    def test_picking_one_fills_it_in_with_this_person(
        self, db, journey, reed, amy, rob, staff
    ):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy),
             "Hi Amy, so glad you were with us.", name="First visit")
        template = saved(db, journey)[0]

        open_direct(staff, rob)
        rob_thread = [
            c for c in db.session.scalars(
                Conversation.directs_for(journey.id, reed.id)
            ).all()
            if c.other_member(reed.id).person_id == rob.id
        ][0]
        body = staff.get(
            f"/messages/{rob_thread.id}/?template={template.id}", headers=H
        ).data.decode()
        assert "Hi Rob, so glad you were with us." in body

    def test_an_email_template_cannot_be_loaded_into_a_chat(
        self, db, journey, reed, amy, staff
    ):
        """It would arrive with the subject line silently thrown away."""
        email_one = EmailTemplate(
            church_id=journey.id, name="An email", kind=TEMPLATE_EMAIL,
            category="welcome", subject="Glad you came", body="Hello there,",
        )
        db.session.add(email_one)
        db.session.commit()

        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        body = staff.get(
            f"/messages/{conversation.id}/?template={email_one.id}", headers=H
        ).data.decode()
        assert "Hello there," not in body

    def test_the_two_kinds_may_share_a_name(self, db, journey, reed, amy, staff):
        """"First visit" is a sensible name for both the email and the
        message about the same moment. Saving one must not overwrite the
        other."""
        db.session.add(EmailTemplate(
            church_id=journey.id, name="First visit", kind=TEMPLATE_EMAIL,
            category="welcome", subject="Glad you came", body="Hello there,",
        ))
        db.session.commit()

        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy.", name="First visit")

        assert len(saved(db, journey, kind=TEMPLATE_EMAIL)) == 1
        assert len(saved(db, journey, kind=TEMPLATE_MESSAGE)) == 1

    def test_sending_from_one_counts_as_a_use(self, db, journey, reed, amy, rob, staff):
        open_direct(staff, amy)
        send(staff, thread_with(db, journey, amy), "Hi Amy.", name="First visit")
        template = saved(db, journey)[0]
        assert template.used_count == 0

        open_direct(staff, rob)
        rob_thread = [
            c for c in db.session.scalars(
                Conversation.directs_for(journey.id, reed.id)
            ).all()
            if c.other_member(reed.id).person_id == rob.id
        ][0]
        staff.post(
            f"/messages/{rob_thread.id}/post/",
            data={"body": "Hi Rob.", "from_template": template.id},
            headers=H, follow_redirects=True,
        )
        db.session.refresh(template)
        assert template.used_count == 1

    def test_removing_one_takes_it_off_the_list(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        send(staff, conversation, "Hi Amy.", name="First visit")
        template = saved(db, journey)[0]

        staff.post(
            f"/messages/{conversation.id}/templates/{template.id}/delete/",
            headers=H, follow_redirects=True,
        )
        assert saved(db, journey) == []
        db.session.refresh(template)
        assert template.is_archived is True

    def test_the_messages_already_sent_stay(self, db, journey, reed, amy, staff):
        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        send(staff, conversation, "Hi Amy.", name="First visit")
        template = saved(db, journey)[0]

        staff.post(
            f"/messages/{conversation.id}/templates/{template.id}/delete/",
            headers=H, follow_redirects=True,
        )
        assert [m.body for m in conversation.messages] == ["Hi Amy."]

    def test_another_churchs_template_cannot_be_removed(
        self, db, journey, reed, amy, staff
    ):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = EmailTemplate(
            church_id=riverbend.id, name="Theirs", kind=TEMPLATE_MESSAGE,
            category="", subject="", body="B",
        )
        db.session.add(theirs)
        db.session.commit()

        open_direct(staff, amy)
        conversation = thread_with(db, journey, amy)
        response = staff.post(
            f"/messages/{conversation.id}/templates/{theirs.id}/delete/", headers=H
        )
        assert response.status_code == 404
        db.session.refresh(theirs)
        assert theirs.is_archived is False


class TestEmailingOnePerson:
    def test_the_form_offers_the_choice(self, db, journey, amy, staff):
        body = staff.get("/messages/email/", headers=H).data.decode()
        assert 'name="mode"' in body
        assert 'name="person_id"' in body

    def test_it_goes_to_that_person_only(self, db, journey, amy, rob, staff):
        staff.post("/messages/email/", data={
            "mode": "person", "person_id": amy.id,
            "subject": "Lunch after the service",
            "body": "A few of us eat together after the 9am.",
        }, headers=H, follow_redirects=True)

        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert [m.to_email for m in queued] == [amy.email]

    def test_the_flash_names_them(self, db, journey, amy, staff):
        response = staff.post("/messages/email/", data={
            "mode": "person", "person_id": amy.id,
            "subject": "Lunch", "body": "After the 9am.",
        }, headers=H, follow_redirects=True)
        assert f"Sent to {amy.full_name}" in response.data.decode()

    def test_it_is_written_down(self, db, journey, amy, staff):
        """An email cannot be taken back, so who sent what to whom is
        recorded whether it went to one person or three hundred."""
        from app.models import AuditEvent

        staff.post("/messages/email/", data={
            "mode": "person", "person_id": amy.id,
            "subject": "Lunch", "body": "After the 9am.",
        }, headers=H, follow_redirects=True)

        events = db.session.scalars(
            db.select(AuditEvent).where(AuditEvent.church_id == journey.id)
        ).all()
        assert any(amy.full_name in (e.subject_label or "") for e in events)

    def test_somebody_who_opted_out_is_not_emailed(self, db, journey, amy, staff):
        """Picking a name is not a way around the opt-out. The category is
        the same one a church-wide email uses, and so is the check."""
        amy.set_preference("announcement", False)
        db.session.commit()

        response = staff.post("/messages/email/", data={
            "mode": "person", "person_id": amy.id,
            "subject": "Lunch", "body": "After the 9am.",
        }, headers=H, follow_redirects=True)

        assert db.session.scalars(db.select(OutboxMessage)).all() == []
        # The distinctive half of the sentence. "turned off church
        # announcements" is also in the help text under the person picker,
        # so matching on that passes whatever the form says back.
        assert "has no email address on file" in response.data.decode()

    def test_a_child_can_be_emailed_by_name(self, db, journey, staff):
        """The audience filters leave children out because their row usually
        carries a parent's address and the count would be a lie. Neither
        reason survives somebody picking a name off a list."""
        teen = Person(
            church_id=journey.id, first_name="Noah", last_name="Price",
            email="noah@example.com", stage="member", approved_at=utcnow(),
            is_child=True,
        )
        db.session.add(teen)
        db.session.commit()
        assert teen.is_child, "the fixture is pointless if this is not a child"

        staff.post("/messages/email/", data={
            "mode": "person", "person_id": teen.id,
            "subject": "Camp", "body": "Sign-ups open Sunday.",
        }, headers=H, follow_redirects=True)

        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert [m.to_email for m in queued] == ["noah@example.com"]

    def test_somebody_at_another_church_cannot_be_picked(self, db, journey, staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = add_person(db, riverbend, "Sam", "Okafor")

        staff.post("/messages/email/", data={
            "mode": "person", "person_id": theirs.id,
            "subject": "Hello", "body": "Words.",
        }, headers=H, follow_redirects=True)

        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_a_leader_cannot_use_it_either(self, db, journey, amy, leader):
        """Reaching somebody in their inbox under the church's name is staff
        authority, one person or three hundred."""
        response = leader.post("/messages/email/", data={
            "mode": "person", "person_id": amy.id,
            "subject": "Hello", "body": "Words.",
        }, headers=H)
        assert response.status_code in (302, 403)
        assert db.session.scalars(db.select(OutboxMessage)).all() == []

    def test_an_audience_send_still_works(self, db, journey, amy, rob, staff):
        """The individual send is an audience of one behind the scenes, so
        this is the test that it did not break the other path."""
        staff.post("/messages/email/", data={
            "audience": "everyone", "subject": "Sunday",
            "body": "We start at nine.",
        }, headers=H, follow_redirects=True)

        queued = db.session.scalars(db.select(OutboxMessage)).all()
        assert {m.to_email for m in queued} == {amy.email, rob.email}


class TestTheAudienceOfOne:
    """`resolve` is where an individual send is decided, so it is tested
    there as well as through the form. The route has its own church check
    and `notify` has its own opt-out check, which means a form-level test
    passes even when this layer is wrong: both of the mistakes below were
    invisible until these four tests existed.
    """

    def test_it_is_the_person(self, db, journey, amy):
        from app.broadcast import resolve

        assert resolve(journey.id, f"person:{amy.id}") == (amy,)

    def test_not_somebody_at_another_church(self, db, journey):
        from app.broadcast import resolve

        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = add_person(db, riverbend, "Sam", "Okafor")
        assert resolve(journey.id, f"person:{theirs.id}") == ()

    def test_not_somebody_who_opted_out(self, db, journey, amy):
        from app.broadcast import CATEGORY, resolve

        amy.set_preference(CATEGORY, False)
        db.session.commit()
        assert resolve(journey.id, f"person:{amy.id}") == ()

    def test_not_somebody_with_no_address(self, db, journey, amy):
        from app.broadcast import resolve

        amy.email = None
        db.session.commit()
        assert resolve(journey.id, f"person:{amy.id}") == ()

    def test_a_nonsense_id_is_nobody_rather_than_an_error(self, db, journey):
        from app.broadcast import resolve

        assert resolve(journey.id, "person:999999") == ()
        assert resolve(journey.id, "person:banana") == ()

    def test_the_label_names_them(self, db, journey, amy):
        """Otherwise the flash and the audit entry both read "nobody"."""
        from app.broadcast import audience_label

        assert audience_label(journey.id, f"person:{amy.id}") == amy.full_name
