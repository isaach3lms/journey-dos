"""Setting somebody up to sign in, from their own profile.

The account controls were only in Settings, under a row listing everybody who
has a login. That is the right place to audit who has access and the wrong
place to answer "this person is standing in front of me and cannot get in",
which is the question staff are actually holding, on a screen that already
has the person's name, address and history on it.

So the same controls appear in the Snapshot card. **The same controls**, not a
second copy: the forms post to the Settings routes, because those routes hold
the guard against removing the last staff account, the guard against changing
your own access, and the audit entries. A second implementation would be a
second place for one of those to be got wrong, and it would be the one nobody
was looking at.

Two things make that reuse safe, and both are tested here:

- The routes come back to the screen the form was pressed on, decided by a
  hidden person id rather than a `next=` URL. An open redirect on a form that
  switches logins on and off is the worst possible place for one.
- The block renders for staff only, while the profile itself is open to
  leaders. Absent, not disabled: a row of controls that refuses teaches
  people to press things and see what happens.
"""

import pytest

from app.content import PEOPLE, SETTINGS
from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def a_person(db, journey, first="Amy", last="Davis", email="amy@example.com"):
    person = Person(church_id=journey.id, first_name=first, last_name=last,
                    email=email, stage="visitor")
    db.session.add(person)
    db.session.commit()
    return person


def an_account(db, journey, person, role="member", active=True):
    user = User(church_id=journey.id, email=person.email, name=person.full_name,
                role=role, person_id=person.id, is_active_account=active)
    user.set_unusable_password()
    user.mark_verified()
    db.session.add(user)
    db.session.commit()
    return user


class TestWhoSeesIt:
    def test_staff_get_the_controls(self, db, journey, staff):
        person = a_person(db, journey)
        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert PEOPLE["account_heading"] in body
        assert PEOPLE["account_create"] in body

    def test_a_leader_does_not(self, db, journey, leader):
        """A leader reads a profile and must not be able to switch a login on
        and off. Absent rather than refused, so there is nothing to press."""
        person = a_person(db, journey)
        page = leader.get(f"/people/{person.id}/", headers=H)
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert PEOPLE["account_heading"] not in body
        assert SETTINGS["account_temp"] not in body

    def test_a_leader_cannot_reach_the_routes_either(self, db, journey, leader):
        """The screen hiding a control is not what keeps it safe. These are
        the same staff-gated routes Settings posts to, and this is the
        assertion that says surfacing them did not loosen that."""
        person = a_person(db, journey)
        account = an_account(db, journey, person)
        for path in (f"/settings/accounts/{account.id}/temp-password/",
                     f"/settings/accounts/{account.id}/toggle/",
                     f"/settings/accounts/{account.id}/resend/",
                     f"/settings/accounts/{account.id}/role/"):
            page = leader.post(path, headers=H, data={"person": person.id,
                                                      "role": "staff"})
            assert page.status_code in (403, 404), path
        db.session.refresh(account)
        assert account.role == "member"
        assert account.is_active_account is True


class TestWhatTheBlockSays:
    def test_it_offers_to_make_one_when_there_is_none(self, db, journey, staff):
        person = a_person(db, journey)
        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert PEOPLE["account_none"] in body
        assert PEOPLE["account_create"] in body

    def test_without_an_address_it_says_what_to_do_instead(self, db, journey, staff):
        """There is nothing to sign in with and nowhere to send the link, so
        a button that cannot work is worse than a sentence."""
        person = a_person(db, journey, email=None)
        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert PEOPLE["account_no_email"] in body
        assert PEOPLE["account_create"] not in body

    def test_an_existing_account_gets_the_three_buttons(self, db, journey, staff):
        person = a_person(db, journey)
        an_account(db, journey, person)
        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert SETTINGS["account_resend"] in body
        assert SETTINGS["account_temp"] in body
        assert SETTINGS["account_deactivate"] in body

    def test_a_switched_off_account_offers_to_switch_it_back_on(self, db, journey, staff):
        person = a_person(db, journey)
        an_account(db, journey, person, active=False)
        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert SETTINGS["account_reactivate"] in body
        assert SETTINGS["account_off"] in body

    def test_your_own_profile_shows_no_controls(self, db, journey, staff):
        """Every one of these refuses on your own account. Three buttons that
        each flash the same error is worse than saying so once."""
        pastor = db.session.scalar(db.select(User).where(
            User.church_id == journey.id,
            User.email == "pastor@journeychurchsemo.com"))
        person = a_person(db, journey, first="Reed", last="Parker",
                          email="pastor@journeychurchsemo.com")
        pastor.person_id = person.id
        db.session.commit()

        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert PEOPLE["account_is_you"] in body
        assert SETTINGS["account_temp"] not in body

    def test_a_different_sign_in_address_is_named(self, db, journey, staff):
        """The roster record and the login can hold different addresses, and
        the one that gets them in is the account's. Said only when they
        differ: the same twice is noise."""
        person = a_person(db, journey, email="amy@home.example")
        account = an_account(db, journey, person)
        account.email = "amy@work.example"
        db.session.commit()

        body = staff.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert "amy@work.example" in body


class TestMakingOneFromTheProfile:
    def test_it_creates_an_account_they_can_use(self, db, journey, staff):
        person = a_person(db, journey)
        page = staff.post("/settings/accounts/", headers=H, follow_redirects=True,
                          data={"person": person.id, "name": person.full_name,
                                "email": person.email, "role": "member"})
        assert page.status_code == 200

        account = User.for_person(journey.id, person.id)
        assert account is not None
        assert account.email == "amy@example.com"
        assert account.role == "member"

    def test_it_links_to_the_record_staff_were_looking_at(self, db, journey, staff):
        """Not re-matched by address.

        `link_person_by_email` refuses an address two people share, which is
        a household. Creating an account from a profile would then leave it
        attached to nobody, on the exact screen that named who it was for.
        """
        shared = "family@example.com"
        one = a_person(db, journey, first="Amy", email=shared)
        a_person(db, journey, first="Rob", email=shared)

        staff.post("/settings/accounts/", headers=H, follow_redirects=True,
                   data={"person": one.id, "name": one.full_name,
                         "email": shared, "role": "member"})

        account = User.for_person(journey.id, one.id)
        assert account is not None, "the account was not linked to the person"
        assert account.person_id == one.id

    def test_a_second_account_for_one_person_is_refused(self, db, journey, staff):
        """`person_id` carries no unique constraint, because the column was
        added to a table that already had rows. Two logins on one record is
        two inboxes for one member's notifications and no way to tell which
        is theirs."""
        person = a_person(db, journey)
        an_account(db, journey, person)

        staff.post("/settings/accounts/", headers=H, follow_redirects=True,
                   data={"person": person.id, "name": person.full_name,
                         "email": "second@example.com", "role": "member"})

        accounts = db.session.scalars(db.select(User).where(
            User.church_id == journey.id, User.person_id == person.id)).all()
        assert len(accounts) == 1

    def test_the_new_account_is_emailed_a_link(self, db, journey, staff):
        from app.models import OutboxMessage

        person = a_person(db, journey)
        staff.post("/settings/accounts/", headers=H, follow_redirects=True,
                   data={"person": person.id, "name": person.full_name,
                         "email": person.email, "role": "member"})

        sent = db.session.scalars(db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.to_email == "amy@example.com")).all()
        assert sent, "nothing was queued, so nobody can set a password"


class TestItComesBackToTheRightScreen:
    """A hidden person id, not a `next=` URL.

    An open redirect on a form that switches logins on and off is the worst
    possible place for one, and an id can only ever name a row in this
    church.
    """

    def _account(self, db, journey):
        person = a_person(db, journey)
        return person, an_account(db, journey, person)

    @pytest.mark.parametrize("action,extra", [
        ("resend", {}),
        ("temp-password", {}),
        ("toggle", {}),
        ("role", {"role": "leader"}),
    ])
    def test_pressed_on_a_profile_it_returns_there(self, db, journey, staff,
                                                   action, extra):
        person, account = self._account(db, journey)
        page = staff.post(f"/settings/accounts/{account.id}/{action}/", headers=H,
                          data={"person": person.id, **extra})
        assert page.status_code == 302
        assert f"/people/{person.id}/" in page.headers["Location"]

    @pytest.mark.parametrize("action,extra", [
        ("resend", {}),
        ("temp-password", {}),
        ("toggle", {}),
        ("role", {"role": "leader"}),
    ])
    def test_pressed_in_settings_it_still_returns_to_settings(self, db, journey,
                                                              staff, action, extra):
        """The other half. Giving these a second home must not move the first
        one."""
        _, account = self._account(db, journey)
        page = staff.post(f"/settings/accounts/{account.id}/{action}/", headers=H,
                          data=extra)
        assert page.status_code == 302
        assert "/settings/" in page.headers["Location"]
        assert "/people/" not in page.headers["Location"]

    def test_creating_one_comes_back_to_the_profile(self, db, journey, staff):
        person = a_person(db, journey)
        page = staff.post("/settings/accounts/", headers=H,
                          data={"person": person.id, "name": person.full_name,
                                "email": person.email, "role": "member"})
        assert page.status_code == 302
        assert f"/people/{person.id}/" in page.headers["Location"]

    def test_a_person_from_another_church_is_not_a_redirect_target(self, db, staff):
        """The id names a row, and the row has to be this church's. Otherwise
        the field is a way to ask one church's screen to send you to
        another's."""
        other = db.session.scalar(db.select(Church).where(Church.slug != "journey"))
        stranger = Person(church_id=other.id, first_name="Someone",
                          last_name="Else", email="else@example.com",
                          stage="visitor")
        db.session.add(stranger)
        db.session.commit()

        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        person = a_person(db, journey)
        account = an_account(db, journey, person)

        page = staff.post(f"/settings/accounts/{account.id}/resend/", headers=H,
                          data={"person": stranger.id})
        assert page.status_code == 302
        assert f"/people/{stranger.id}/" not in page.headers["Location"]
        assert "/settings/" in page.headers["Location"]


class TestTheGuardsStillApply:
    """The reason these post to the Settings routes rather than to new ones."""

    def test_you_cannot_change_your_own_access_from_your_own_profile(
        self, db, journey, staff
    ):
        """The guard that actually fires on this path.

        Changing your own access from a screen you are standing on is how
        somebody demotes themselves out of it. It was written for the
        Settings list and it holds here unchanged, which is the point of
        posting to the same route.

        Worth saying plainly what is NOT being tested: the last-staff guard
        sitting behind it. On `toggle_account` and `change_role` that guard
        cannot be reached. The caller is signed-in staff, so they are active
        staff, so `active_staff_count(excluding=target)` counts at least them
        whenever the target is somebody else; and when the target is the
        caller, this guard returns first. It is defence in depth behind a
        door that is already shut, not a rule this screen can exercise.
        """
        pastor = db.session.scalar(db.select(User).where(
            User.church_id == journey.id,
            User.email == "pastor@journeychurchsemo.com"))
        person = a_person(db, journey, first="Pastor", last="Reed",
                          email="pastor@journeychurchsemo.com")
        pastor.person_id = person.id
        db.session.commit()

        for path, data in ((f"/settings/accounts/{pastor.id}/toggle/", {}),
                           (f"/settings/accounts/{pastor.id}/role/",
                            {"role": "member"})):
            staff.post(path, headers=H, data={"person": person.id, **data},
                       follow_redirects=True)
            db.session.refresh(pastor)
            assert pastor.is_active_account is True, path
            assert pastor.role == "staff", path

    def test_somebody_elses_account_still_toggles(self, db, journey, staff):
        """The other side of the guard above. It must refuse your own
        account, not every account."""
        person = a_person(db, journey)
        account = an_account(db, journey, person)

        staff.post(f"/settings/accounts/{account.id}/toggle/", headers=H,
                   data={"person": person.id}, follow_redirects=True)
        db.session.refresh(account)
        assert account.is_active_account is False

    def test_a_temporary_password_is_still_recorded(self, db, journey, staff):
        """The audit entry is one of the things reusing the route buys."""
        from app.models import AuditEvent

        person = a_person(db, journey)
        account = an_account(db, journey, person)
        staff.post(f"/settings/accounts/{account.id}/temp-password/", headers=H,
                   data={"person": person.id}, follow_redirects=True)

        entries = db.session.scalars(db.select(AuditEvent).where(
            AuditEvent.church_id == journey.id,
            AuditEvent.subject_type == "user")).all()
        assert any("temporary password" in (e.summary or "").lower()
                   for e in entries)

    def test_the_password_itself_is_never_written_to_the_log(self, db, journey, staff):
        """It is shown once on screen and nowhere else. A log is designed to
        be read, kept and exported, and a working password in one is worse
        than no log."""
        from app.models import AuditEvent

        person = a_person(db, journey)
        account = an_account(db, journey, person)
        page = staff.post(f"/settings/accounts/{account.id}/temp-password/",
                          headers=H, data={"person": person.id},
                          follow_redirects=True)
        shown = page.get_data(as_text=True)

        entries = db.session.scalars(db.select(AuditEvent).where(
            AuditEvent.church_id == journey.id)).all()
        logged = " ".join((e.summary or "") + (e.detail or "") for e in entries)
        # The flash carries the password; pull it back out and prove the log
        # does not hold the same string.
        import re
        match = re.search(r"Temporary password for [^:]+: (\S+)\.", shown)
        assert match, "the password was not shown to the person who pressed it"
        assert match.group(1) not in logged
