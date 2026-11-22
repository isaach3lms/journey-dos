"""Staff and admin accounts receiving what the church sends.

**The bug.** A church reported that announcements reached members and not
staff. It was not the notification system. Everything the DOS sends is
addressed to a roster record: `broadcast.resolve` builds its audience from
`Person` rows, `notify` takes a person, and `PushQueueItem.person_id` is a
non-null foreign key to `person.id`. A login with no roster record attached
cannot be queued at all.

So a staff account created from an address nobody had put on the roster was
in no audience, for anything, permanently. It was not filtered or skipped or
logged, because there was nothing to address. The account worked, sign-in
worked, the phone had granted permission, and nothing ever arrived.

**Why it took two days to find.** Every screen in the product was built
around the roster, so every screen agreed that everything was fine. The staff
coverage block counted accounts with permission granted, and these accounts
had granted it. The member's own settings screen said notifications were on,
and they were. Nothing anywhere asked the one question that mattered, which
is whether the church could address this person at all.

The tests below pin the three parts of the fix: a check that answers that
question, a screen that shows the answer, and a retry at sign-in so an
account created before its roster record repairs itself.
"""

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from app.push.coverage import (
    ARCHIVED,
    CHILD,
    NO_EMAIL,
    NO_RECORD,
    OPTED_OUT,
    REACHED,
    UNAPPROVED,
    audience_state,
    is_reached,
    unreached,
)
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def make_staff(db, journey, email="newstaff@journeychurchsemo.com", *,
               person=True, **person_kwargs):
    """A staff login, with or without a roster record behind it."""
    user = User(church_id=journey.id, email=email, name="Sam Pastor",
                role="staff")
    user.mark_verified()
    user.set_unusable_password()
    db.session.add(user)
    db.session.flush()

    if person:
        fields = {"first_name": "Sam", "last_name": "Pastor", "email": email,
                  "stage": "member", "approved_at": utcnow()}
        fields.update(person_kwargs)
        row = Person(church_id=journey.id, **fields)
        db.session.add(row)
        db.session.flush()
        user.person_id = row.id

    db.session.commit()
    return user


class TestWhetherTheChurchCanReachAnAccount:
    def test_a_login_with_no_roster_record_is_not_in_the_audience(
            self, db, journey):
        """The bug itself. Not a preference, not a filter: there is nothing
        to address, so nothing is sent."""
        user = make_staff(db, journey, person=False)

        assert audience_state(user) == NO_RECORD
        assert is_reached(user) is False

    def test_a_linked_approved_record_is_reached(self, db, journey):
        user = make_staff(db, journey)

        assert audience_state(user) == REACHED

    def test_an_unapproved_record_is_named_as_such(self, db, journey):
        """A different fix from an absent one, which is the whole reason this
        answers with a reason rather than a boolean.

        Approval only applies to somebody who signed themselves up: a record
        staff entered was never in the queue, so both flags are needed to
        make one that is genuinely waiting.
        """
        user = make_staff(db, journey, approved_at=None, self_registered=True)

        assert audience_state(user) == UNAPPROVED

    def test_an_archived_record_is_named(self, db, journey):
        user = make_staff(db, journey, is_archived=True)

        assert audience_state(user) == ARCHIVED

    def test_a_record_with_no_address_is_named(self, db, journey):
        user = make_staff(db, journey, email="")

        assert audience_state(user) == NO_EMAIL

    def test_a_child_record_is_named_as_a_likely_mislink(self, db, journey):
        user = make_staff(db, journey, is_child=True)

        assert audience_state(user) == CHILD

    def test_somebody_who_opted_out_is_distinguished_from_a_fault(
            self, db, journey):
        """Nothing is broken here, and the screen must not offer to fix it.
        A church overriding a member's own choice is a different product."""
        user = make_staff(db, journey)
        user.person.set_preference("announcement", False)
        db.session.commit()

        assert audience_state(user) == OPTED_OUT

    def test_it_agrees_with_the_send_it_describes(self, db, journey):
        """The way this comes apart is somebody adding a condition to one and
        not the other. A screen saying an account is covered while the send
        skips it is worse than no screen, because it ends the search."""
        from app.broadcast import _eligible

        cases = (
            {},
            {"approved_at": None, "self_registered": True},
            {"is_archived": True},
            {"is_child": True},
            {"email": ""},
        )
        for index, kwargs in enumerate(cases):
            # Numbered, not keyed off the dict: two of these have the same
            # number of keys, so a length made two accounts collide.
            fields = dict(kwargs)
            # The login's address and the record's address are two different
            # things, and this loop needs to vary the second while keeping
            # the first unique.
            person_email = fields.pop("email", None)
            user = make_staff(db, journey, email=f"agree{index}@example.com",
                              **fields)
            if person_email is not None:
                user.person.email = person_email
                db.session.commit()
            expected = _eligible(user.person)

            assert is_reached(user) is expected, kwargs


class TestTheWorstOnesComeFirst:
    def test_accounts_with_no_record_sort_above_opt_outs(self, db, journey):
        """One of these is a fault the church introduced and the other is a
        member's decision. A list that mixed them would bury the fault."""
        quiet = make_staff(db, journey, email="quiet@example.com")
        quiet.person.set_preference("announcement", False)
        broken = make_staff(db, journey, email="broken@example.com",
                            person=False)
        db.session.commit()

        rows = unreached([quiet, broken])

        assert [state for _, state in rows] == [NO_RECORD, OPTED_OUT]
        assert rows[0][0].id == broken.id

    def test_reached_accounts_are_left_out_entirely(self, db, journey):
        fine = make_staff(db, journey)

        assert unreached([fine]) == []


class TestTheAccountsScreenSaysSo:
    def test_a_broken_account_is_named_on_the_screen(self, db, client,
                                                     sign_in, journey):
        """What was missing. Nothing in the product said an account could not
        be reached, so nobody looked."""
        make_staff(db, journey, person=False)
        sign_in("pastor@journeychurchsemo.com")

        page = client.get("/settings/?open=accounts",
                          headers=H).get_data(as_text=True)

        assert "Not on the roster" in page

    def test_a_working_account_says_so_too(self, db, client, sign_in,
                                           journey):
        """Shown on every row, not only the broken ones. "Nothing here says
        otherwise" is exactly what let this run for months."""
        make_staff(db, journey)
        sign_in("pastor@journeychurchsemo.com")

        page = client.get("/settings/?open=accounts",
                          headers=H).get_data(as_text=True)

        assert "Gets church notifications" in page

    def test_the_fix_is_offered_where_it_applies(self, db, client, sign_in,
                                                 journey):
        user = make_staff(db, journey, person=False)
        sign_in("pastor@journeychurchsemo.com")

        page = client.get("/settings/?open=accounts",
                          headers=H).get_data(as_text=True)

        assert f"/accounts/{user.id}/roster/" in page

    def test_an_opt_out_is_not_offered_a_fix(self, db, client, sign_in,
                                             journey):
        """The church does not get a button to undo a member's own choice."""
        user = make_staff(db, journey, email="quiet@example.com")
        user.person.set_preference("announcement", False)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")

        page = client.get("/settings/?open=accounts",
                          headers=H).get_data(as_text=True)

        # Neither affordance. Found by a mutation: asserting only that the
        # Add to roster form is absent passed happily when opting out was
        # added to the fixable list, because that renders the other button,
        # the one pointing at their record. Both are the church reaching for
        # a decision that is not its to make.
        assert f"/accounts/{user.id}/roster/" not in page
        assert f"/people/{user.person_id}" not in page


class TestAddingAnAccountToTheRoster:
    def test_it_creates_a_record_and_links_it(self, db, client, sign_in,
                                              journey):
        user = make_staff(db, journey, person=False)
        sign_in("pastor@journeychurchsemo.com")

        client.post(f"/settings/accounts/{user.id}/roster/", headers=H,
                    data={}, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id is not None
        assert audience_state(user) == REACHED

    def test_the_new_record_is_approved(self, db, client, sign_in, journey):
        """Creating it pending would make the record and still send nothing,
        which is the same bug one step further along."""
        user = make_staff(db, journey, person=False)
        sign_in("pastor@journeychurchsemo.com")

        client.post(f"/settings/accounts/{user.id}/roster/", headers=H,
                    data={}, follow_redirects=True)
        db.session.refresh(user)

        assert user.person.is_approved

    def test_an_existing_record_is_linked_rather_than_duplicated(
            self, db, client, sign_in, journey):
        """The account reaches this state because the address was not on the
        roster at the time. It may well be now, and a second record would
        split one person's notifications between two rows."""
        user = make_staff(db, journey, person=False,
                          email="late@journeychurchsemo.com")
        existing = Person(church_id=journey.id, first_name="Late",
                          last_name="Arrival",
                          email="late@journeychurchsemo.com",
                          stage="member", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")

        client.post(f"/settings/accounts/{user.id}/roster/", headers=H,
                    data={}, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id == existing.id
        assert db.session.scalar(db.select(db.func.count(Person.id)).where(
            Person.email == "late@journeychurchsemo.com")) == 1

    def test_it_refuses_for_an_account_that_already_has_a_record(
            self, db, client, sign_in, journey):
        """Whatever is wrong with an existing record is fixed on that
        person's screen. Making another one is never the answer."""
        user = make_staff(db, journey, approved_at=None)
        before = user.person_id
        sign_in("pastor@journeychurchsemo.com")

        client.post(f"/settings/accounts/{user.id}/roster/", headers=H,
                    data={}, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id == before

    def test_another_churchs_account_is_not_found(self, db, client, sign_in,
                                                  journey):
        other = db.session.scalar(
            db.select(Church).where(Church.slug != "journey"))
        stranger = User(church_id=other.id, email="x@other.example",
                        name="Stranger", role="staff")
        stranger.set_unusable_password()
        db.session.add(stranger)
        db.session.commit()
        sign_in("pastor@journeychurchsemo.com")

        page = client.post(f"/settings/accounts/{stranger.id}/roster/",
                           headers=H, data={})

        assert page.status_code == 404


class TestItRepairsItselfAtSignIn:
    def test_an_unlinked_account_links_on_login(self, db, client, journey):
        """The match used to be tried only when the account was created, so
        adding the roster record an hour later fixed nothing and nothing said
        why that person never got an announcement."""
        user = make_staff(db, journey, person=False,
                          email="later@journeychurchsemo.com")
        user.set_password("correct-horse-battery")
        db.session.add(Person(
            church_id=journey.id, first_name="Later", last_name="Record",
            email="later@journeychurchsemo.com", stage="member",
            approved_at=utcnow()))
        db.session.commit()

        client.post("/auth/login", headers=H, data={
            "email": "later@journeychurchsemo.com",
            "password": "correct-horse-battery",
        }, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id is not None
        assert audience_state(user) == REACHED

    def test_a_household_address_is_still_refused(self, db, client, journey):
        """Two people on one address is a household, and guessing which
        spouse owns the login shows one of them the other's record."""
        user = make_staff(db, journey, person=False,
                          email="both@journeychurchsemo.com")
        user.set_password("correct-horse-battery")
        for first in ("Ann", "Ben"):
            db.session.add(Person(
                church_id=journey.id, first_name=first, last_name="Shared",
                email="both@journeychurchsemo.com", stage="member",
                approved_at=utcnow()))
        db.session.commit()

        client.post("/auth/login", headers=H, data={
            "email": "both@journeychurchsemo.com",
            "password": "correct-horse-battery",
        }, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id is None

    def test_an_already_linked_account_is_left_alone(self, db, client,
                                                     journey):
        """No query, no write, and above all no re-matching: an account
        linked by hand to the right record must not be moved by an address
        that happens to match somebody else."""
        user = make_staff(db, journey, email="linked@journeychurchsemo.com")
        user.set_password("correct-horse-battery")
        db.session.commit()
        before = user.person_id

        client.post("/auth/login", headers=H, data={
            "email": "linked@journeychurchsemo.com",
            "password": "correct-horse-battery",
        }, follow_redirects=True)
        db.session.refresh(user)

        assert user.person_id == before
