"""Staff approval for people who signed themselves up.

The gate is deliberately narrow. Somebody waiting can use their own record and
read published plans; only church-wide announcements are held back. An app that
looks broken to the person who just decided to engage is a worse outcome than a
stranger seeing a potluck notice a day early.
"""

import re

import pytest

from app.models import (
    AuditEvent,
    Church,
    Conversation,
    ConversationMember,
    OutboxMessage,
    Person,
    User,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, PASSWORD

NEW_EMAIL = "nina.ibarra@example.com"
NEW_PASSWORD = "a-long-enough-passphrase"
MEMBER_EMAIL = "member@journeychurchsemo.com"


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    church.allow_self_signup = True
    db.session.commit()
    return church


@pytest.fixture
def announcement(db, journey):
    conversation = Conversation(
        church_id=journey.id, kind="announcement", title="Church-wide"
    )
    db.session.add(conversation)
    db.session.commit()
    return conversation


@pytest.fixture
def stranger(db, journey):
    """Somebody who signed themselves up and confirmed their email.

    Built directly rather than through the HTTP flow. Verifying signs the new
    person in, and the `db` fixture holds one application context open, so
    Flask-Login would cache them on `g` and any *other* client in the same
    test would inherit their identity. That trap has now bitten four times.
    `TestTheSignupFlowProducesThisState` covers the real path.
    """
    person = Person(
        church_id=journey.id, first_name="Nina", last_name="Ibarra",
        email=NEW_EMAIL, stage="visitor", first_seen_on=utcnow().date(),
        self_registered=True, approved_at=None,
    )
    db.session.add(person)
    db.session.flush()

    user = User(
        church_id=journey.id, email=NEW_EMAIL, name="Nina Ibarra", role="member",
        person_id=person.id,
    )
    user.set_password(NEW_PASSWORD)
    user.mark_verified()
    db.session.add(user)
    db.session.commit()
    return person


@pytest.fixture
def stranger_client(app, db, journey, stranger):
    """A client signed in as the unapproved person."""
    signed_in = app.test_client()
    signed_in.post(
        "/auth/login",
        data={"email": NEW_EMAIL, "password": NEW_PASSWORD},
        headers={"Host": JOURNEY_HOST},
    )
    return signed_in


class TestWhoNeedsApproving:
    def test_somebody_who_signed_themselves_up_is_waiting(self, db, stranger):
        assert stranger.self_registered
        assert not stranger.is_approved
        assert stranger.is_waiting_for_approval

    def test_somebody_staff_entered_is_approved_already(self, db, journey):
        """A human with roster access vouching for them is a stronger signal
        than any button."""
        person = Person(
            church_id=journey.id, first_name="Entered", last_name="ByStaff",
            stage="visitor", approved_at=utcnow(),
        )
        db.session.add(person)
        db.session.commit()
        assert person.is_approved
        assert not person.is_waiting_for_approval

    def test_an_imported_person_is_not_waiting(self, db, journey):
        """`self_registered` is false by default, so an import cannot fill the
        queue with people nobody needs to look at."""
        person = Person(
            church_id=journey.id, first_name="Imported", last_name="Person",
            stage="visitor",
        )
        db.session.add(person)
        db.session.commit()
        assert not person.is_waiting_for_approval

    def test_the_waiting_list_is_scoped_to_one_church(self, db, journey, stranger):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(
            Person(
                church_id=riverbend.id, first_name="Their", last_name="Stranger",
                stage="visitor", self_registered=True,
            )
        )
        db.session.commit()

        assert Person.waiting_count(journey.id) == 1
        waiting = db.session.scalars(Person.waiting_for_approval(journey.id)).all()
        assert [p.id for p in waiting] == [stranger.id]

    def test_an_archived_person_drops_off_the_list(self, db, journey, stranger):
        stranger.is_archived = True
        db.session.commit()
        assert Person.waiting_count(journey.id) == 0


class TestWhatIsHeldBack:
    def test_announcements_are_hidden_until_approved(self, db, journey, stranger, announcement):
        """A stranger who signed up this morning is not yet part of "the
        church", so a church-wide message is not yet for them."""
        assert not announcement.can_read(stranger)

    def test_announcements_appear_once_approved(self, db, journey, stranger, announcement):
        stranger.approve()
        db.session.commit()
        assert announcement.can_read(stranger)

    def test_a_room_they_were_invited_to_is_readable_while_waiting(
        self, db, journey, stranger
    ):
        """Somebody chose to invite them, which is its own approval."""
        room = Conversation(church_id=journey.id, kind="room", title="Newcomers")
        db.session.add(room)
        db.session.flush()
        db.session.add(
            ConversationMember(
                church_id=journey.id, conversation_id=room.id, person_id=stranger.id
            )
        )
        db.session.commit()

        assert room.can_read(stranger)
        assert room.can_post(stranger)

    def test_the_list_and_the_page_agree(self, db, journey, stranger, announcement):
        """Expressed once, so a conversation cannot be listed and then refused."""
        visible = db.session.scalars(
            Conversation.visible_to(journey.id, stranger)
        ).all()
        assert announcement.id not in {c.id for c in visible}

        stranger.approve()
        db.session.commit()
        visible = db.session.scalars(
            Conversation.visible_to(journey.id, stranger)
        ).all()
        assert announcement.id in {c.id for c in visible}

    def test_opening_an_announcement_while_waiting_is_a_404(
        self, db, journey, stranger, stranger_client, announcement
    ):
        r = stranger_client.get(
            f"/me/chat/{announcement.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 404

    def test_posting_to_one_while_waiting_is_refused(
        self, db, journey, stranger, stranger_client, announcement
    ):
        r = stranger_client.post(
            f"/me/chat/{announcement.id}/",
            data={"body": "Hello everyone"},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404
        assert announcement.messages == []

    def test_an_approved_member_is_unaffected(self, db, journey, announcement, member):
        """Everybody who already existed keeps seeing what they always saw."""
        person = Person(
            church_id=journey.id, first_name="Alicia", last_name="Romero",
            email=MEMBER_EMAIL, stage="attender", approved_at=utcnow(),
        )
        db.session.add(person)
        db.session.flush()
        user = db.session.scalar(
            db.select(User).where(User.email == MEMBER_EMAIL, User.church_id == journey.id)
        )
        user.person_id = person.id
        db.session.commit()

        r = member.get(f"/me/chat/{announcement.id}/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200


class TestTheRestOfTheAppStillWorks:
    """The gate is narrow on purpose."""

    def test_their_own_home_screen_works(self, db, stranger, stranger_client):
        r = stranger_client.get("/me/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Nina" in r.data

    def test_home_explains_the_wait_rather_than_looking_broken(
        self, db, stranger, stranger_client
    ):
        r = stranger_client.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"let you in shortly" in r.data

    def test_their_own_details_work(self, db, stranger, stranger_client):
        assert stranger_client.get(
            "/me/you/", headers={"Host": JOURNEY_HOST}
        ).status_code == 200

    def test_published_reading_still_works(self, db, journey, stranger, stranger_client):
        from app.models import Resource, ResourceSession

        resource = Resource(
            church_id=journey.id, title="Known", kind="reading_plan", status="published"
        )
        db.session.add(resource)
        db.session.flush()
        db.session.add(
            ResourceSession(
                church_id=journey.id, resource_id=resource.id, position=1,
                title="Day 1", body="Read this.",
            )
        )
        db.session.commit()

        r = stranger_client.get("/me/read/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 200
        assert b"Known" in r.data

    def test_the_notice_disappears_after_approval(self, db, stranger, stranger_client):
        stranger.approve()
        db.session.commit()
        r = stranger_client.get("/me/", headers={"Host": JOURNEY_HOST})
        assert b"let you in shortly" not in r.data


class TestApproving:
    def test_staff_can_approve_from_the_person_page(self, db, journey, stranger, staff):
        staff.post(
            f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(stranger)
        assert stranger.is_approved

    def test_the_roster_lists_who_is_waiting(self, db, stranger, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Waiting to be let in" in r.data
        assert b"Nina Ibarra" in r.data

    def test_the_card_disappears_when_nobody_is_waiting(self, db, journey, staff):
        r = staff.get("/people/", headers={"Host": JOURNEY_HOST})
        assert b"Waiting to be let in" not in r.data

    def test_approving_twice_changes_nothing(self, db, stranger, staff):
        staff.post(f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(stranger)
        first = stranger.approved_at

        staff.post(f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST})
        db.session.refresh(stranger)
        assert stranger.approved_at == first

    def test_it_is_audited(self, db, journey, stranger, staff):
        staff.post(f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST})
        events = db.session.scalars(AuditEvent.recent(journey.id)).all()
        assert any("approved after signing themselves up" in e.summary for e in events)

    def test_it_lands_on_their_timeline(self, db, journey, stranger, staff):
        from app.models import PersonEvent

        staff.post(f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST})
        events = db.session.scalars(
            PersonEvent.for_person(journey.id, stranger.id)
        ).all()
        assert any("Approved" in e.summary for e in events)

    def test_they_are_told(self, db, journey, stranger, staff):
        staff.post(f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST})
        messages = db.session.scalars(db.select(OutboxMessage)).all()
        welcome = [m for m in messages if "You are in" in m.subject]
        assert welcome
        # Transactional: it is about their account, not church news.
        assert welcome[0].category == "account"

    def test_a_member_cannot_approve_anyone(self, db, stranger, member):
        r = member.post(
            f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 403
        db.session.refresh(stranger)
        assert not stranger.is_approved

    def test_nobody_can_approve_themselves(self, db, stranger, stranger_client):
        r = stranger_client.post(
            f"/people/{stranger.id}/approve/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 403
        db.session.refresh(stranger)
        assert not stranger.is_approved

    def test_a_person_from_another_church_is_a_404(self, db, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(
            church_id=riverbend.id, first_name="Their", last_name="Stranger",
            stage="visitor", self_registered=True,
        )
        db.session.add(theirs)
        db.session.commit()

        r = staff.post(f"/people/{theirs.id}/approve/", headers={"Host": JOURNEY_HOST})
        assert r.status_code == 404


class TestTheDeployDoesNotHideAnnouncementsFromEveryone:
    """`approved_at` is null by default, so every person that existed before
    this migration reads as unapproved.

    Without a backfill the deploy hides church-wide announcements from an
    entire congregation, silently, and nobody would connect it to a schema
    change.
    """

    def test_the_migration_backfills_existing_people(self):
        import glob
        from pathlib import Path

        paths = glob.glob(
            str(Path(__file__).resolve().parent.parent
                / "migrations" / "versions" / "*staff_approval*.py")
        )
        assert paths, "The approval migration is missing"
        body = Path(paths[0]).read_text()
        assert "UPDATE person SET approved_at" in body

    def test_an_unapproved_legacy_person_is_not_treated_as_a_stranger(
        self, db, journey, announcement
    ):
        """`self_registered` is what distinguishes them, not approval alone,
        so an imported person with a null approval never lands in the queue.
        """
        legacy = Person(
            church_id=journey.id, first_name="Legacy", last_name="Member",
            stage="member",
        )
        db.session.add(legacy)
        db.session.commit()

        assert not legacy.is_waiting_for_approval
        assert Person.waiting_count(journey.id) == 0


class TestTheSignupFlowProducesThisState:
    """The fixture above builds the rows directly, so one test walks the real
    path and checks it lands in the same place."""

    def test_signing_up_and_confirming_leaves_somebody_waiting(
        self, db, journey, client
    ):
        client.post(
            "/auth/join",
            data={"name": "Andre Bright", "email": "andre@example.com",
                  "password": NEW_PASSWORD},
            headers={"Host": JOURNEY_HOST},
        )
        message = db.session.scalars(
            db.select(OutboxMessage).order_by(OutboxMessage.id.desc())
        ).first()
        token = re.search(r"/auth/verify/([A-Za-z0-9_\-]+)", message.body_text).group(1)
        client.get(f"/auth/verify/{token}", headers={"Host": JOURNEY_HOST})

        person = db.session.scalar(
            db.select(Person).where(Person.email == "andre@example.com")
        )
        assert person is not None
        assert person.self_registered
        assert person.is_waiting_for_approval
        assert person.stage == "visitor"


class TestApprovalOnlyGatesPeopleWhoSignedThemselvesUp:
    """`is_approved` is "not waiting", not "approved_at is set".

    Defining it the other way made every new staff-created person start
    invisible to announcements unless somebody remembered to stamp a column,
    and forgetting would have been silent. An existing test caught it.
    """

    def test_a_staff_created_person_needs_no_stamp(self, db, journey, announcement):
        person = Person(
            church_id=journey.id, first_name="Entered", last_name="ByStaff",
            stage="visitor",
        )
        db.session.add(person)
        db.session.commit()

        assert person.approved_at is None
        assert person.is_approved
        assert announcement.can_read(person)

    def test_only_a_self_registered_person_is_gated(self, db, journey, announcement):
        person = Person(
            church_id=journey.id, first_name="Signed", last_name="UpAlone",
            stage="visitor", self_registered=True,
        )
        db.session.add(person)
        db.session.commit()

        assert not person.is_approved
        assert not announcement.can_read(person)
