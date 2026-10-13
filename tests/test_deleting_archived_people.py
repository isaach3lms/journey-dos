"""Removing an archived person from the roster for good.

This is the only action in the system with nothing left to look at
afterwards. Archiving hides somebody and can be undone; this cannot, and
there is no bin it goes to for thirty days.

So most of this file is about what it refuses and what it leaves alone,
rather than about the delete working. Three groups matter:

**It is hard to do by accident.** Archived only, staff only, a screen that
names what each person takes with them, and a typed word.

**Two things block it.** A sign-in account, because the login would survive
with nothing behind it. Giving history, because the gift rows outlive the
person by design and an unattributed gift is a reconciliation problem
nobody will trace back to a Tuesday afternoon on the roster screen.

**What survives, survives.** Messages they wrote in a room, connect cards,
and email already sent are records of things that happened to the church.
They lose the name attached to them rather than disappearing, and a test
below holds each of those in place.

The cascades themselves are a Postgres fact and this suite runs on SQLite
with foreign keys unenforced, so the rows are deleted here through the ORM
and the database-level behaviour is checked against a real Postgres in the
migration verification.
"""

import pytest

from app.models import (
    Checkin,
    CheckinSession,
    Church,
    ContactLog,
    ExternalGift,
    GuestCard,
    Household,
    Person,
    PersonEvent,
    User,
)
from app.models.base import utcnow
from app.purge import (
    BLOCKED_ACCOUNT,
    BLOCKED_GIVING,
    BLOCKED_NOT_ARCHIVED,
    Blocked,
    delete,
    inspect,
)
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def a_person(db, journey, first="Marcus", last="Webb", archived=True, **kwargs):
    person = Person(church_id=journey.id, first_name=first, last_name=last,
                    stage="visitor", approved_at=utcnow(), **kwargs)
    db.session.add(person)
    db.session.flush()
    if archived:
        person.archive()
    db.session.commit()
    return person


def names(db, journey):
    return {
        p.first_name for p in db.session.scalars(
            db.select(Person).where(Person.church_id == journey.id)).all()
    }


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


def confirm(client, *people):
    return client.post("/people/archived/delete/", headers=H,
                       data={"person_id": [p.id for p in people]})


def run_delete(client, *people, word="DELETE"):
    return client.post("/people/archived/delete/confirm/", headers=H, data={
        "person_id": [p.id for p in people],
        "confirm": word,
    }, follow_redirects=True)


class TestItWorks:
    def test_an_archived_person_can_be_deleted(self, db, journey, staff):
        person = a_person(db, journey)
        run_delete(staff, person)
        assert "Marcus" not in names(db, journey)

    def test_their_history_goes_with_them(self, db, journey, staff):
        person = a_person(db, journey)
        PersonEvent.record(person, "note", "Called them")
        db.session.commit()

        run_delete(staff, person)
        assert db.session.scalars(db.select(PersonEvent)).all() == []

    def test_several_at_once(self, db, journey, staff):
        """A church clearing out test records has twenty of them, not one."""
        people = [a_person(db, journey, first=f"Test{n}") for n in range(5)]
        run_delete(staff, *people)
        assert not [n for n in names(db, journey) if n.startswith("Test")]

    def test_it_says_how_many(self, db, journey, staff):
        people = [a_person(db, journey, first=f"Test{n}") for n in range(3)]
        body = run_delete(staff, *people).get_data(as_text=True)
        assert "3 deleted for good" in body


class TestWhatItRefuses:
    def test_somebody_with_a_sign_in(self, db, journey, staff):
        """The login would survive with nothing behind it: an account that
        still opens the app and whose screens have no person to render."""
        person = a_person(db, journey)
        user = User(church_id=journey.id, email="marcus@example.com",
                    name="Marcus Webb", role="member", person_id=person.id)
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()

        assert inspect(person).blocked_by == BLOCKED_ACCOUNT
        run_delete(staff, person)
        assert "Marcus" in names(db, journey)

    def test_somebody_with_giving_history(self, db, journey, staff):
        """The gifts outlive the person by design, because a church's
        figures for a year must not change because somebody tidied the
        roster in March. Unattributed, they are a problem nobody traces
        back here."""
        person = a_person(db, journey)
        db.session.add(ExternalGift(
            church_id=journey.id, person_id=person.id, provider="tithely",
            provider_txn_id="gift-1", amount_cents=5000,
            received_on=utcnow().date(), donor_name="Marcus Webb"))
        db.session.commit()

        assert inspect(person).blocked_by == BLOCKED_GIVING
        run_delete(staff, person)
        assert "Marcus" in names(db, journey)

    def test_somebody_who_is_not_archived(self, db, journey, staff):
        """Two steps with a decision in between, and the first one is
        reversible. Nobody deletes a person straight off the roster."""
        person = a_person(db, journey, archived=False)
        assert inspect(person).blocked_by == BLOCKED_NOT_ARCHIVED
        run_delete(staff, person)
        assert "Marcus" in names(db, journey)

    def test_the_refusal_is_named_not_silent(self, db, journey, staff):
        """Somebody who ticked eight and deleted six needs to know which
        two are still there."""
        fine = a_person(db, journey, first="Junk")
        blocked = a_person(db, journey, first="Marcus")
        db.session.add(ExternalGift(
            church_id=journey.id, person_id=blocked.id, provider="tithely",
            provider_txn_id="g1", amount_cents=100,
            received_on=utcnow().date(), donor_name="Marcus Webb"))
        db.session.commit()

        body = run_delete(staff, fine, blocked).get_data(as_text=True)
        assert "Marcus Webb" in body
        assert "Junk" not in names(db, journey)

    def test_a_block_appearing_after_the_screen_rendered(self, db, journey, staff):
        """The counts on a confirmation page were true when it rendered.
        The check that decides is the one at the moment of deleting, which
        is the same rule the outbox applies to an opt-out."""
        person = a_person(db, journey)
        assert inspect(person).allowed

        user = User(church_id=journey.id, email="late@example.com",
                    name="Marcus Webb", role="member", person_id=person.id)
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()

        with pytest.raises(Blocked):
            delete(person)

    def test_a_blocked_delete_raises_rather_than_doing_nothing(self, db, journey):
        """By this point a human has confirmed. A delete that quietly does
        nothing sends them back next week to try again."""
        person = a_person(db, journey, archived=False)
        with pytest.raises(Blocked):
            delete(person)


class TestWhatSurvives:
    def test_a_message_they_wrote_stays_in_the_room(self, db, journey, staff):
        """A conversation is a record of what was said in it. Deleting
        somebody must not rewrite other people's threads."""
        from app.models import Conversation, Message
        from app.models.message import KIND_ROOM

        person = a_person(db, journey)
        room = Conversation(church_id=journey.id, kind=KIND_ROOM, title="Tuesday")
        db.session.add(room)
        db.session.flush()
        Message.post(room, person, "See you Tuesday")
        db.session.commit()

        run_delete(staff, person)

        [message] = db.session.scalars(db.select(Message)).all()
        assert message.body == "See you Tuesday"
        assert message.author_person_id is None

    def test_a_connect_card_stays(self, db, journey, staff):
        """The church still had a guest that Sunday, and the number for
        that week should not change retrospectively."""
        person = a_person(db, journey)
        db.session.add(GuestCard(church_id=journey.id, person_id=person.id,
                                 first_name="Marcus", last_name="Webb"))
        db.session.commit()

        run_delete(staff, person)

        [card] = db.session.scalars(db.select(GuestCard)).all()
        assert card.first_name == "Marcus"
        assert card.person_id is None

    def test_the_audit_entry_outlives_them(self, db, journey, staff):
        """The only thing left that says they were ever here. The audit
        model was built with loose text rather than a foreign key for
        exactly this reason."""
        from app.models import AuditEvent
        from app.models.audit import PERSON_DELETED

        person = a_person(db, journey)
        PersonEvent.record(person, "note", "Called them")
        db.session.commit()

        run_delete(staff, person)

        entry = db.session.scalar(
            db.select(AuditEvent).where(AuditEvent.action == PERSON_DELETED))
        assert entry is not None
        assert entry.subject_label == "Marcus Webb"
        assert entry.actor_name
        assert "timeline" in (entry.detail or ""), (
            "the entry does not say what went with them, which is the "
            "question somebody asks a year later"
        )

    def test_nothing_is_logged_for_a_refusal(self, db, journey, staff):
        from app.models import AuditEvent
        from app.models.audit import PERSON_DELETED

        person = a_person(db, journey, archived=False)
        run_delete(staff, person)

        assert db.session.scalars(db.select(AuditEvent).where(
            AuditEvent.action == PERSON_DELETED)).all() == []


class TestItIsHardToDoByAccident:
    def test_the_typed_word_is_required(self, db, journey, staff):
        person = a_person(db, journey)
        body = run_delete(staff, person, word="").get_data(as_text=True)
        assert "Marcus" in names(db, journey)
        assert "did not match" in body

    def test_a_wrong_word_deletes_nothing(self, db, journey, staff):
        person = a_person(db, journey)
        run_delete(staff, person, word="yes")
        assert "Marcus" in names(db, journey)

    def test_lower_case_is_accepted(self, db, journey, staff):
        """Typed on a phone keyboard that capitalises nothing. The guard is
        deliberateness, not spelling."""
        person = a_person(db, journey)
        run_delete(staff, person, word="delete")
        assert "Marcus" not in names(db, journey)

    def test_the_confirm_screen_writes_nothing(self, db, journey, staff):
        person = a_person(db, journey)
        confirm(staff, person)
        assert "Marcus" in names(db, journey)

    def test_the_confirm_screen_says_what_goes(self, db, journey, staff):
        person = a_person(db, journey)
        for n in range(3):
            PersonEvent.record(person, "note", f"Note {n}")
        db.session.commit()

        body = confirm(staff, person).get_data(as_text=True)
        assert "3 timeline entries" in body
        assert "cannot be reversed" in body

    def test_the_confirm_screen_names_the_blocked_and_why(self, db, journey, staff):
        person = a_person(db, journey)
        db.session.add(ExternalGift(
            church_id=journey.id, person_id=person.id, provider="tithely",
            provider_txn_id="g1", amount_cents=100,
            received_on=utcnow().date(), donor_name="Marcus Webb"))
        db.session.commit()

        body = confirm(staff, person).get_data(as_text=True)
        assert "giving history" in body

    def test_an_empty_selection_says_so(self, db, journey, staff):
        response = staff.post("/people/archived/delete/", headers=H,
                              data={}, follow_redirects=True)
        assert response.status_code == 200


class TestWhoCanDoIt:
    def test_a_leader_cannot(self, db, journey, client, sign_in):
        """Putting somebody back is open to leaders because it is
        reversible. This is not."""
        person = a_person(db, journey)
        sign_in("leader@journeychurchsemo.com")

        client.post("/people/archived/delete/confirm/", headers=H, data={
            "person_id": [person.id], "confirm": "DELETE"})
        assert "Marcus" in names(db, journey)

    def test_a_member_cannot(self, db, journey, client, sign_in):
        person = a_person(db, journey)
        sign_in("member@journeychurchsemo.com")
        client.post("/people/archived/delete/confirm/", headers=H, data={
            "person_id": [person.id], "confirm": "DELETE"})
        assert "Marcus" in names(db, journey)

    def test_signed_out_cannot(self, db, journey, client):
        person = a_person(db, journey)
        client.post("/people/archived/delete/confirm/", headers=H, data={
            "person_id": [person.id], "confirm": "DELETE"})
        assert "Marcus" in names(db, journey)

    def test_another_church_cannot_reach_this_one(self, db, journey, staff):
        other = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=other.id, first_name="Theirs",
                        last_name="Person", stage="visitor",
                        approved_at=utcnow())
        db.session.add(theirs)
        db.session.flush()
        theirs.archive()
        db.session.commit()

        run_delete(staff, theirs)
        assert db.session.get(Person, theirs.id) is not None

    def test_the_button_is_not_shown_to_a_leader(self, db, journey, client,
                                                 sign_in):
        a_person(db, journey)
        sign_in("leader@journeychurchsemo.com")
        body = client.get("/people/archived/", headers=H).get_data(as_text=True)
        assert "Delete for good" not in body

    def test_the_button_is_shown_to_staff(self, db, journey, staff):
        a_person(db, journey)
        body = staff.get("/people/archived/", headers=H).get_data(as_text=True)
        assert "Delete for good" in body


class TestTheCountsAreReal:
    def test_a_blank_record_says_so(self, db, journey):
        person = a_person(db, journey)
        verdict = inspect(person)
        assert verdict.allowed
        assert not verdict.takes_anything

    def test_check_ins_are_counted(self, db, journey):
        person = a_person(db, journey)
        session = CheckinSession(church_id=journey.id, name="Sunday",
                                 starts_at=utcnow())
        household = Household(church_id=journey.id, name="The Webbs")
        db.session.add_all([session, household])
        db.session.flush()
        db.session.add(Checkin(church_id=journey.id, session_id=session.id,
                               person_id=person.id, household_id=household.id,
                               household_name="The Webbs", pickup_code="ABCD"))
        db.session.commit()

        assert inspect(person).counts["checkins"] == 1

    def test_logged_contacts_are_counted(self, db, journey):
        person = a_person(db, journey)
        db.session.add(ContactLog(church_id=journey.id, person_id=person.id,
                                  method="call", summary="Rang them"))
        db.session.commit()

        assert inspect(person).counts["contacts"] == 1

    def test_one_persons_history_is_not_counted_against_another(self, db, journey):
        """A count that is quietly church-wide would show the same numbers
        beside every name, and somebody would stop reading them."""
        one = a_person(db, journey, first="One")
        two = a_person(db, journey, first="Two")
        PersonEvent.record(one, "note", "Only about One")
        db.session.commit()

        assert inspect(one).counts["events"] == 1
        assert inspect(two).counts["events"] == 0
