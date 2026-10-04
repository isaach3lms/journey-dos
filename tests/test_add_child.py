"""Staff creating a child.

Until now `is_child` could only be set by an import or from a shell, which
meant the kiosk could check in only the children somebody had already put in
the database by hand. Two screens now create one: the roster page, for a
family nobody has met, and a person's own record, which is where staff
already are when they learn somebody has kids.

The rules worth testing are the ones whose failure is invisible until a
Sunday morning. A child with no family cannot be checked in and no screen
shows it. A family that has just gained its first child and no check-in code
cannot be checked in either. And a child must not be walked down a follow-up
path written for adults.
"""

import pytest

from app.households import MAX_HOUSEHOLD_MEMBERS
from app.models import Church, Household, Person, SequenceEnrollment, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def parent(db, journey):
    """An adult on the roster with no family yet, which is the state anybody
    who signed themselves up arrives in."""
    person = Person(church_id=journey.id, first_name="Dana", last_name="Whitlow",
                    email="dana@example.com", stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


def a_household(db, journey, name="The Whitlow family"):
    home = Household(church_id=journey.id, name=name)
    db.session.add(home)
    db.session.commit()
    return home


def child_named(db, journey, first):
    return db.session.scalar(
        db.select(Person).where(Person.church_id == journey.id,
                               Person.first_name == first)
    )


class TestFromAPersonsRecord:
    def test_a_child_joins_the_parents_family(self, db, journey, client, staff,
                                              parent):
        home = a_household(db, journey)
        parent.household_id = home.id
        db.session.commit()

        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        nora = child_named(db, journey, "Nora")
        assert nora.is_child is True
        assert nora.household_id == home.id

    def test_the_surname_follows_the_parent(self, db, journey, client, staff,
                                            parent):
        """Most families share one, and it is already on the screen."""
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        assert child_named(db, journey, "Nora").last_name == "Whitlow"

    def test_a_different_surname_is_kept(self, db, journey, client, staff,
                                         parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora", "last_name": "Okafor"},
                    headers=H)

        assert child_named(db, journey, "Nora").last_name == "Okafor"

    def test_a_parent_with_no_family_gets_one(self, db, journey, client, staff,
                                              parent):
        assert parent.household_id is None

        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        db.session.refresh(parent)
        assert parent.household_id is not None
        assert child_named(db, journey, "Nora").household_id == parent.household_id

    def test_the_family_gets_a_checkin_code_immediately(self, db, journey,
                                                        client, staff, parent):
        """The one that matters most. A family holding a child and no code
        cannot check in, and nothing on any staff screen shows the
        difference, so it would be found on a Sunday with a queue."""
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        db.session.refresh(parent)
        assert parent.household.checkin_pin

    def test_the_child_starts_where_the_parent_is(self, db, journey, client,
                                                  staff, parent):
        """A family arrives together. A child does not enter at the front of
        a path meant for adults."""
        parent.stage = "volunteer"
        db.session.commit()

        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        assert child_named(db, journey, "Nora").stage == "volunteer"

    def test_a_birthday_is_recorded(self, db, journey, client, staff, parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora", "birthdate": "2019-04-02"},
                    headers=H)

        assert child_named(db, journey, "Nora").birthdate.isoformat() == "2019-04-02"

    def test_a_birthday_in_the_future_is_refused(self, db, journey, client,
                                                 staff, parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora", "birthdate": "2099-01-01"},
                    headers=H)

        assert child_named(db, journey, "Nora") is None

    def test_nonsense_in_the_birthday_does_not_create_a_record(
            self, db, journey, client, staff, parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora", "birthdate": "soon"},
                    headers=H)

        assert child_named(db, journey, "Nora") is None

    def test_volunteer_notes_are_kept(self, db, journey, client, staff, parent):
        """Allergies are the reason this field exists."""
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora", "notes": "Peanut allergy"},
                    headers=H)

        assert child_named(db, journey, "Nora").notes == "Peanut allergy"

    def test_no_first_name_adds_nobody(self, db, journey, client, staff, parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "  "}, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Person.id)).where(
                Person.church_id == journey.id, Person.is_child.is_(True))
        ) == 0

    def test_the_same_child_twice_is_refused(self, db, journey, client, staff,
                                             parent):
        """The mistake this is really guarding: the same child entered by two
        people under the same name, then two name tags on Sunday."""
        for _ in range(2):
            client.post(f"/people/{parent.id}/family/add/",
                        data={"first_name": "Nora"}, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Person.id)).where(
                Person.church_id == journey.id, Person.first_name == "Nora")
        ) == 1

    def test_a_refused_duplicate_does_not_leave_a_household_behind(
            self, db, journey, client, staff, parent):
        """The refusal happens after a household may have been created for a
        parent who had none. Rolling back is what stops a failed attempt
        leaving an empty family on the roster."""
        before = db.session.scalar(db.select(db.func.count(Household.id)))
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        after = db.session.scalar(db.select(db.func.count(Household.id)))
        assert after == before + 1

    def test_a_full_family_is_refused(self, db, journey, client, staff, parent):
        home = a_household(db, journey)
        parent.household_id = home.id
        for i in range(MAX_HOUSEHOLD_MEMBERS):
            db.session.add(Person(church_id=journey.id, first_name=f"Kid{i}",
                                  last_name="Whitlow", household_id=home.id,
                                  stage="member", is_child=True))
        db.session.commit()

        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        assert child_named(db, journey, "Nora") is None

    def test_a_leader_can_do_it(self, db, journey, client, leader, parent):
        """Leaders add people all day. A child is not a higher level of
        trust than an adult."""
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        assert child_named(db, journey, "Nora") is not None

    def test_a_member_cannot(self, db, journey, client, member, parent):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        assert child_named(db, journey, "Nora") is None

    def test_a_stranger_cannot(self, db, journey, client, parent):
        answer = client.post(f"/people/{parent.id}/family/add/",
                             data={"first_name": "Nora"}, headers=H)

        assert answer.status_code in (302, 401, 403)
        assert child_named(db, journey, "Nora") is None

    def test_another_churchs_person_is_not_found(self, db, journey, client,
                                                 staff, parent):
        """The rule the whole module is built on. A person id from another
        church is a 404, not a child in somebody else's family."""
        answer = client.post(f"/people/{parent.id}/family/add/",
                             data={"first_name": "Nora"},
                             headers={"Host": RIVERBEND_HOST})

        assert answer.status_code in (302, 404)
        assert child_named(db, journey, "Nora") is None


class TestFromTheRosterPage:
    def test_a_child_can_be_put_into_an_existing_family(self, db, journey,
                                                        client, staff):
        home = a_household(db, journey)

        client.post("/people/add/", data={
            "first_name": "Eli", "last_name": "Whitlow", "stage": "member",
            "is_child": "1", "household_id": str(home.id),
        }, headers=H)

        eli = child_named(db, journey, "Eli")
        assert eli.is_child is True
        assert eli.household_id == home.id
        assert home.checkin_pin

    def test_a_new_family_can_be_started_in_the_same_step(self, db, journey,
                                                          client, staff):
        """The case it was asked for: a family nobody has met, named once."""
        client.post("/people/add/", data={
            "first_name": "Eli", "last_name": "Brandt", "stage": "visitor",
            "is_child": "1", "household_name": "The Brandt family",
        }, headers=H)

        eli = child_named(db, journey, "Eli")
        assert eli.household.name == "The Brandt family"
        assert eli.household.checkin_pin

    def test_a_typed_name_wins_over_a_picked_family(self, db, journey, client,
                                                    staff):
        """Somebody who typed a name meant it."""
        home = a_household(db, journey)

        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_id": str(home.id), "household_name": "The Brandt family",
        }, headers=H)

        assert child_named(db, journey, "Eli").household.name == "The Brandt family"

    def test_a_child_with_no_family_is_refused(self, db, journey, client, staff):
        """Check-in is keyed on the family, so a child without one is a
        record that cannot do the only job it has. Refusing at entry turns a
        silent Sunday failure into a sentence on screen.

        Asserting the sentence, not just the absent record. Deleting the rule
        makes this request crash instead, and "no child was created" is true
        of a crash too, so the weaker assertion would have called a 500 a
        working guard.
        """
        answer = client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
        }, headers=H, follow_redirects=True)

        assert answer.status_code == 200
        assert "A child needs a family" in answer.get_data(as_text=True)
        assert child_named(db, journey, "Eli") is None

    def test_an_adult_still_needs_no_family(self, db, journey, client, staff):
        """The rule above is about children, and it must not leak."""
        client.post("/people/add/", data={
            "first_name": "Marta", "last_name": "Reyes", "stage": "visitor",
        }, headers=H)

        marta = child_named(db, journey, "Marta")
        assert marta is not None
        assert marta.is_child is False
        assert marta.household_id is None

    def test_typing_a_family_name_twice_does_not_make_two_families(
            self, db, journey, client, staff):
        """Found by a test, not by a church. Typing the same name twice used
        to create a second family with the same name, which splits siblings
        between two rows that look identical in every picker and gives the
        parent two check-in codes. Each name is one family."""
        for first in ("Eli", "Nora"):
            client.post("/people/add/", data={
                "first_name": first, "last_name": "Brandt", "stage": "member",
                "is_child": "1", "household_name": "The Brandt family",
            }, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Household.id)).where(
                Household.church_id == journey.id,
                Household.name == "The Brandt family")
        ) == 1
        assert (child_named(db, journey, "Eli").household_id
                == child_named(db, journey, "Nora").household_id)

    def test_the_same_name_in_a_different_case_is_the_same_family(
            self, db, journey, client, staff):
        """Two people typing the same family at a welcome desk will not agree
        on capitals."""
        a_household(db, journey, name="The Brandt family")

        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_name": "the brandt FAMILY",
        }, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Household.id)).where(
                Household.church_id == journey.id)
        ) == 1

    def test_another_churchs_family_is_not_joined_by_name(
            self, db, journey, client, staff):
        """The name match is a lookup like any other, so it is scoped like
        any other."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(Household(church_id=riverbend.id, name="The Brandt family"))
        db.session.commit()

        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_name": "The Brandt family",
        }, headers=H)

        assert child_named(db, journey, "Eli").household.church_id == journey.id

    def test_a_duplicate_child_in_a_named_family_is_still_refused(
            self, db, journey, client, staff):
        """The same child entered twice by two people, which is what the
        duplicate check is for, and it has to survive the name match above."""
        for _ in range(2):
            client.post("/people/add/", data={
                "first_name": "Eli", "last_name": "Brandt", "stage": "member",
                "is_child": "1", "household_name": "The Brandt family",
            }, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Person.id)).where(
                Person.church_id == journey.id, Person.first_name == "Eli")
        ) == 1

    def test_an_unknown_family_adds_nobody(self, db, journey, client, staff):
        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_id": "99999",
        }, headers=H)

        assert child_named(db, journey, "Eli") is None

    def test_another_churchs_family_cannot_be_named(self, db, journey, client,
                                                    staff):
        """A household id is a number in a form body, so the only thing
        stopping one church's child landing in another church's family is the
        tenant check on the lookup."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = Household(church_id=riverbend.id, name="The Osei family")
        db.session.add(theirs)
        db.session.commit()

        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_id": str(theirs.id),
        }, headers=H)

        assert child_named(db, journey, "Eli") is None

    def test_the_surname_follows_the_family_when_left_blank(self, db, journey,
                                                            client, staff):
        home = a_household(db, journey)
        db.session.add(Person(church_id=journey.id, first_name="Dana",
                              last_name="Whitlow", household_id=home.id,
                              stage="member", approved_at=utcnow()))
        db.session.commit()

        client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_id": str(home.id),
        }, headers=H)

        assert child_named(db, journey, "Eli").last_name == "Whitlow"

    def test_a_child_never_gets_a_sign_in(self, db, journey, client, staff):
        """Ticked on the same form by somebody working quickly. A child
        record exists for check-in; an account is a different thing
        entirely."""
        client.post("/people/add/", data={
            "first_name": "Eli", "last_name": "Brandt", "stage": "member",
            "is_child": "1", "household_name": "The Brandt family",
            "email": "eli@example.com", "with_login": "1", "role": "member",
        }, headers=H)

        eli = child_named(db, journey, "Eli")
        assert eli is not None
        assert db.session.scalar(
            db.select(User).where(User.email == "eli@example.com")
        ) is None

    def test_the_refused_sign_in_is_said_out_loud(self, db, journey, client,
                                                  staff):
        """A request somebody made on purpose is answered, not dropped."""
        answer = client.post("/people/add/", data={
            "first_name": "Eli", "stage": "member", "is_child": "1",
            "household_name": "The Brandt family", "email": "eli@example.com",
            "with_login": "1",
        }, headers=H, follow_redirects=True)

        assert "never gets an account" in answer.get_data(as_text=True)


class TestAChildIsNotWalkedDownAnAdultPath:
    def test_no_sequence_is_started_for_a_child(self, db, journey, client,
                                                staff, parent):
        """A child inherits a family's stage, and a stage that triggers a
        welcome series would put a four-year-old in a staff follow-up queue
        and send them copy written for adults.

        The parent is moved to a stage that does trigger one first, because
        with the family at a stage nothing fires on, this test would pass
        with the guard deleted.
        """
        parent.stage = "visitor"
        db.session.commit()

        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)

        nora = child_named(db, journey, "Nora")
        assert db.session.scalars(
            db.select(SequenceEnrollment).where(
                SequenceEnrollment.person_id == nora.id)
        ).all() == []

    def test_the_rule_lives_in_the_automation_not_the_screen(self, db, journey,
                                                             parent):
        """Every screen that creates a child would otherwise have to remember
        this, and one of them forgetting is not a failure anybody sees until
        after it has happened."""
        from app.automation import enroll_for_stage

        child = Person(church_id=journey.id, first_name="Nora",
                       last_name="Whitlow", stage="visitor", is_child=True)
        db.session.add(child)
        db.session.flush()

        assert enroll_for_stage(child) == []

    def test_an_adult_is_still_enrolled(self, db, journey):
        """The guard above must not switch off the thing it sits in front
        of."""
        from app.automation import enroll_for_stage
        from app.sequences import triggered_by

        adult = Person(church_id=journey.id, first_name="Marta",
                       last_name="Reyes", stage="visitor", approved_at=utcnow())
        db.session.add(adult)
        db.session.flush()

        expected = len(triggered_by("visitor"))
        assert len(enroll_for_stage(adult)) == expected


class TestTheForms:
    def test_the_roster_page_offers_the_child_option(self, client, staff):
        page = client.get("/people/", headers=H).get_data(as_text=True)

        assert "This is a child" in page
        assert 'name="household_name"' in page

    def test_a_persons_record_offers_adding_a_child(self, db, journey, client,
                                                    staff, parent):
        page = client.get(f"/people/{parent.id}/", headers=H).get_data(as_text=True)

        assert "Add a child to this family" in page
        assert f"/people/{parent.id}/family/add/" in page

    def test_a_childs_own_record_does_not_offer_it(self, db, journey, client,
                                                   staff, parent):
        """A child does not have children."""
        home = a_household(db, journey)
        child = Person(church_id=journey.id, first_name="Nora", last_name="Whitlow",
                       household_id=home.id, stage="member", is_child=True)
        db.session.add(child)
        db.session.commit()

        page = client.get(f"/people/{child.id}/", headers=H).get_data(as_text=True)

        assert "Add a child to this family" not in page


class TestTheChildCanActuallyBeCheckedIn:
    """The point of the whole feature, asserted end to end.

    Everything above tests a record being written correctly. None of it
    proves the thing staff actually wanted, which is that a child entered on
    a weekday can be checked in on Sunday. The kiosk is reached by typing the
    family's code, so that is how this gets there: no shortcut, the same
    route a volunteer uses.
    """

    @pytest.fixture
    def sunday(self, db, journey):
        from app.models import CheckinSession

        session = CheckinSession(church_id=journey.id, name="Sunday 9:30",
                                 starts_at=utcnow())
        db.session.add(session)
        db.session.commit()
        return session

    def test_a_child_added_today_checks_in_on_sunday(self, db, journey, client,
                                                     staff, parent, sunday):
        client.post(f"/people/{parent.id}/family/add/",
                    data={"first_name": "Nora"}, headers=H)
        db.session.refresh(parent)
        code = parent.household.checkin_pin
        assert code, "no code means no check-in, whatever the record says"

        # The volunteer's route in: type the family's code.
        found = client.post("/kids/kiosk/pin/", data={"pin": code}, headers=H,
                            follow_redirects=True)
        page = found.get_data(as_text=True)
        assert "Nora" in page

        # And the child is checkable rather than merely listed.
        nora = child_named(db, journey, "Nora")
        done = client.post(
            f"/kids/kiosk/family/{parent.household_id}/",
            data={"person_id": str(nora.id)}, headers=H, follow_redirects=True,
        )
        assert done.status_code == 200

        from app.models import Checkin

        assert db.session.scalar(
            db.select(db.func.count(Checkin.id)).where(Checkin.person_id == nora.id)
        ) == 1
