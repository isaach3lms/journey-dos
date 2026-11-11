"""Asking to be set up with a login.

This replaces "Create an account" on the sign-in page. The old link handed a
login to anybody who typed an address; the new one collects who somebody is
and who is in their household, and a person decides.

Four things are worth testing and they are all about what happens after the
form is submitted, not about the fields:

1. **It is stored, not only emailed.** An email is where a request goes to
   die. The person who received it is away, the family hears nothing, and the
   church never knows it happened.
2. **The email carries everything.** The connect card's alert deliberately
   withholds what the guest wrote. This is the opposite case: every field is
   material somebody needs in front of them, and making them open a page to
   get it is how a request sits unanswered for a week.
3. **Asking twice does not make two families.** Somebody who hears nothing
   asks again, and a second row makes the list look like two households.
4. **It creates no account by itself.** The household is free text that a
   human reads. A button that made a login straight from it would be guessing
   at the part that needs judgement.
"""

import pytest

from app import accounts_requested
from app.models import AccountRequest, Church, OutboxMessage, User
from app.models.church import DEFAULT_ACCOUNT_REQUEST_EMAIL
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}

ASK = {
    "first_name": "Marcus",
    "last_name": "Delgado",
    "email": "marcus@example.com",
    "phone": "(573) 555-0144",
    "address": "412 Mill Street, Jackson, MO 63755",
    "household": "My wife Carla, and two boys, 7 and 4.",
}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def asks(db, journey):
    return db.session.scalars(
        db.select(AccountRequest).where(AccountRequest.church_id == journey.id)
    ).all()


def mail(db, journey, address=DEFAULT_ACCOUNT_REQUEST_EMAIL):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.to_email == address)
    ).all()


class TestTheSignInPage:
    def test_it_offers_a_request_rather_than_an_account(self, client):
        page = client.get("/auth/login", headers=H).get_data(as_text=True)

        assert "Request an account" in page
        assert "/request-account/" in page

    def test_the_request_link_is_always_there(self, db, journey, client):
        """Asking is safe: it sends a request to a person and hands out
        nothing. The self-serve route is the one behind a setting."""
        journey.allow_self_signup = False
        db.session.commit()

        page = client.get("/auth/login", headers=H).get_data(as_text=True)

        assert "/request-account/" in page

    def test_the_old_create_an_account_link_is_gone(self, db, journey, client):
        """Even with self-signup switched on. Two links here would make
        somebody choose between them with no way to tell which is right, and
        a church routing accounts past a person should not also be showing
        the door that skips them."""
        journey.allow_self_signup = True
        db.session.commit()

        page = client.get("/auth/login", headers=H).get_data(as_text=True)

        assert "Create an account" not in page
        assert "/auth/join" not in page

    def test_the_self_serve_route_still_answers_when_it_is_switched_on(
            self, db, journey, client):
        """Unlinked, not removed. A church that deliberately wants it keeps
        it, and turning the setting off closes it completely."""
        journey.allow_self_signup = True
        db.session.commit()

        assert client.get("/auth/join", headers=H).status_code == 200

        journey.allow_self_signup = False
        db.session.commit()

        assert client.get("/auth/join", headers=H).status_code == 404

    def test_the_form_needs_no_sign_in(self, client):
        assert client.get("/request-account/", headers=H).status_code == 200


class TestMakingARequest:
    def test_it_is_stored(self, db, journey, client):
        """Not only emailed. An email is where a request goes to die."""
        client.post("/request-account/", data=ASK, headers=H)

        made = asks(db, journey)
        assert len(made) == 1
        assert made[0].full_name == "Marcus Delgado"
        assert made[0].is_open is True

    def test_the_household_is_kept_as_written(self, db, journey, client):
        """A household is "my wife Carla and two boys" far more often than it
        is a set of fields. A human reads it and types the real records."""
        client.post("/request-account/", data=ASK, headers=H)

        assert asks(db, journey)[0].household == ASK["household"]

    def test_the_address_is_kept(self, db, journey, client):
        client.post("/request-account/", data=ASK, headers=H)

        assert "Mill Street" in asks(db, journey)[0].address

    def test_the_person_is_told_it_is_in(self, client):
        answer = client.post("/request-account/", data=ASK, headers=H)

        assert answer.status_code == 200
        assert "Thank you" in answer.get_data(as_text=True)

    def test_a_name_is_required(self, db, journey, client):
        answer = client.post("/request-account/",
                             data={**ASK, "first_name": " "}, headers=H)

        assert asks(db, journey) == []
        assert "first name" in answer.get_data(as_text=True)

    def test_a_working_email_is_required(self, db, journey, client):
        """It becomes the sign-in and it is how the link is sent, so a bad
        one is refused while somebody is still looking at the form rather
        than days later by a person who cannot reply."""
        for bad in ("", "marcus", "marcus@", "marcus@localhost"):
            client.post("/request-account/", data={**ASK, "email": bad},
                        headers=H)

        assert asks(db, journey) == []

    def test_asking_twice_does_not_make_two(self, db, journey, client,
                                            monkeypatch):
        """Somebody who hears nothing for a week asks again. A second row is
        not more information and makes the list look like two families."""
        from app.blueprints import public

        monkeypatch.setattr(public, "COOLDOWN_SECONDS", 0)
        client.post("/request-account/", data=ASK, headers=H)
        answer = client.post("/request-account/", data=ASK, headers=H)

        assert len(asks(db, journey)) == 1
        assert "already have a request" in answer.get_data(as_text=True)

    def test_asking_again_after_it_was_handled_is_allowed(
            self, db, journey, client, monkeypatch):
        """The block is on an outstanding request, not on the address
        forever. Somebody set up and later removed can ask again."""
        from app.blueprints import public

        monkeypatch.setattr(public, "COOLDOWN_SECONDS", 0)
        client.post("/request-account/", data=ASK, headers=H)
        made = asks(db, journey)[0]
        made.resolve("done")
        db.session.commit()

        client.post("/request-account/", data=ASK, headers=H)

        assert len(asks(db, journey)) == 2

    def test_the_form_hands_back_what_they_typed(self, client):
        answer = client.post("/request-account/", data={**ASK, "email": "no"},
                             headers=H)

        assert "Mill Street" in answer.get_data(as_text=True)


class TestKeepingBotsOut:
    def test_a_filled_trap_writes_nothing(self, db, journey, client):
        client.post("/request-account/",
                    data={**ASK, "website": "http://spam.test"}, headers=H)

        assert asks(db, journey) == []

    def test_a_trapped_bot_is_told_it_worked(self, client):
        answer = client.post("/request-account/",
                             data={**ASK, "website": "http://spam.test"},
                             headers=H)

        assert "Thank you" in answer.get_data(as_text=True)

    def test_a_second_request_within_seconds_is_refused(self, db, journey,
                                                        client):
        client.post("/request-account/", data=ASK, headers=H)
        client.post("/request-account/",
                    data={**ASK, "email": "other@example.com"}, headers=H)

        assert len(asks(db, journey)) == 1


class TestTheEmail:
    def test_it_goes_to_the_platform_by_default(self, db, journey, client):
        client.post("/request-account/", data=ASK, headers=H)

        assert mail(db, journey)

    def test_it_uses_the_same_setting_as_the_connect_card(self, db, journey,
                                                          client):
        """One place to change, not two. A church that moved one moved both."""
        journey.account_request_email = "office@journeychurchsemo.com"
        db.session.commit()

        client.post("/request-account/", data=ASK, headers=H)

        assert mail(db, journey, "office@journeychurchsemo.com")
        assert mail(db, journey) == []

    def test_it_carries_everything_needed_to_do_the_setup(self, db, journey,
                                                          client):
        """The opposite rule from the connect card alert. Making somebody
        open a page to get this is how a request sits unanswered."""
        client.post("/request-account/", data=ASK, headers=H)

        body = mail(db, journey)[0].body_text
        assert "Marcus Delgado" in body
        assert "marcus@example.com" in body
        assert "Mill Street" in body
        assert "two boys" in body

    def test_it_links_to_the_list(self, db, journey, client):
        """So whoever does the setup can mark it off rather than leaving the
        church guessing whether anybody did."""
        client.post("/request-account/", data=ASK, headers=H)

        assert "/people/requests/" in mail(db, journey)[0].body_text

    def test_a_failed_email_does_not_lose_the_request(self, db, journey, client,
                                                      monkeypatch):
        """The row is written and the person has been told it is in. An error
        page at that point sends them round the form again."""
        def boom(*args, **kwargs):
            raise RuntimeError("the mail provider is down")

        monkeypatch.setattr(accounts_requested, "alert", boom)

        answer = client.post("/request-account/", data=ASK, headers=H)

        assert "Thank you" in answer.get_data(as_text=True)
        assert len(asks(db, journey)) == 1


class TestReadingThem:
    def test_a_leader_can_see_them(self, db, journey, client, leader):
        client.post("/request-account/", data=ASK, headers=H)

        page = client.get("/people/requests/", headers=H).get_data(as_text=True)

        assert "Marcus Delgado" in page
        assert "two boys" in page
        assert "Mill Street" in page

    def test_a_member_cannot(self, client, member):
        answer = client.get("/people/requests/", headers=H)

        assert answer.status_code in (302, 403, 404)

    def test_a_stranger_cannot(self, client):
        answer = client.get("/people/requests/", headers=H)

        assert answer.status_code in (302, 401, 403)

    def test_another_churchs_requests_are_not_listed(self, db, journey, client,
                                                     staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(AccountRequest(church_id=riverbend.id, first_name="Nadia",
                                      last_name="Osei", email="nadia@x.test"))
        db.session.commit()

        page = client.get("/people/requests/", headers=H).get_data(as_text=True)

        assert "Nadia" not in page

    def test_the_roster_has_a_button(self, client, staff):
        page = client.get("/people/", headers=H).get_data(as_text=True)

        assert "Account requests" in page
        assert "/people/requests/" in page


class TestClearingThem:
    def _one(self, db, journey, client):
        client.post("/request-account/", data=ASK, headers=H)
        return asks(db, journey)[0]

    def test_it_can_be_marked_set_up(self, db, journey, client, staff):
        ask = self._one(db, journey, client)

        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        db.session.refresh(ask)
        assert ask.status == "done"
        assert ask.handled_by_name

    def test_it_can_be_declined(self, db, journey, client, staff):
        ask = self._one(db, journey, client)

        client.post(f"/people/requests/{ask.id}/", data={"action": "declined"},
                    headers=H)

        db.session.refresh(ask)
        assert ask.status == "declined"

    def test_a_handled_request_leaves_the_list(self, db, journey, client, staff):
        ask = self._one(db, journey, client)
        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        # Fetched on its own rather than following the redirect: the flash
        # that confirms the action names the person, so a check for the name
        # anywhere on the page would fail on the confirmation it was given.
        page = client.get("/people/requests/", headers=H).get_data(as_text=True)

        assert "Nobody is waiting" in page
        assert "Mill Street" not in page

    def test_a_handled_request_is_still_there_under_show_handled(
            self, db, journey, client, staff):
        ask = self._one(db, journey, client)
        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        page = client.get("/people/requests/?all=1",
                          headers=H).get_data(as_text=True)

        assert "Mill Street" in page

    def test_it_is_never_deleted(self, db, journey, client, staff):
        """Three months later somebody asks whether the Delgado family were
        ever got back to."""
        ask = self._one(db, journey, client)
        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        assert db.session.get(AccountRequest, ask.id) is not None

    def test_it_can_be_put_back(self, db, journey, client, staff):
        ask = self._one(db, journey, client)
        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        client.post(f"/people/requests/{ask.id}/", data={"action": "reopen"},
                    headers=H)

        db.session.refresh(ask)
        assert ask.is_open is True
        assert ask.handled_by_name is None

    def test_an_unknown_action_changes_nothing(self, db, journey, client, staff):
        ask = self._one(db, journey, client)

        answer = client.post(f"/people/requests/{ask.id}/",
                             data={"action": "deleted"}, headers=H)

        db.session.refresh(ask)
        assert answer.status_code == 400
        assert ask.is_open is True

    def test_another_churchs_request_cannot_be_touched(self, db, journey, client,
                                                       staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = AccountRequest(church_id=riverbend.id, first_name="Nadia",
                                last_name="Osei", email="nadia@x.test")
        db.session.add(theirs)
        db.session.commit()

        answer = client.post(f"/people/requests/{theirs.id}/",
                             data={"action": "done"}, headers=H)

        db.session.refresh(theirs)
        assert answer.status_code in (302, 404)
        assert theirs.is_open is True

    def test_a_member_cannot_clear_one(self, db, journey, client, member):
        ask = AccountRequest(church_id=journey.id, first_name="Marcus",
                             last_name="Delgado", email="marcus@example.com")
        db.session.add(ask)
        db.session.commit()

        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        db.session.refresh(ask)
        assert ask.is_open is True


class TestItCreatesNoAccountByItself:
    def test_no_login_is_made(self, db, journey, client):
        """The household is free text that a human reads. A button that made
        an account straight from it would be guessing at the part that needs
        judgement."""
        client.post("/request-account/", data=ASK, headers=H)

        assert db.session.scalar(
            db.select(User).where(User.email == "marcus@example.com")
        ) is None

    def test_marking_it_set_up_makes_no_login_either(self, db, journey, client,
                                                     staff):
        client.post("/request-account/", data=ASK, headers=H)
        ask = asks(db, journey)[0]

        client.post(f"/people/requests/{ask.id}/", data={"action": "done"},
                    headers=H)

        assert db.session.scalar(
            db.select(User).where(User.email == "marcus@example.com")
        ) is None


class TestTheSpouse:
    """Their own email and phone, as fields rather than buried in prose.

    This is the one part of a household that usually needs a second login. An
    account is an email address, and kids check-in looks a family up by phone,
    so both have to come out of the form as values rather than be read out of
    a sentence by whoever does the setup.
    """

    SPOUSE = {
        **ASK,
        "spouse_name": "Carla Delgado",
        "spouse_email": "Carla@Example.com",
        "spouse_phone": "(573) 555-0199",
    }

    def test_the_form_asks_for_them(self, client):
        page = client.get("/request-account/", headers=H).get_data(as_text=True)

        assert 'name="spouse_email"' in page
        assert 'name="spouse_phone"' in page
        assert 'name="spouse_name"' in page

    def test_they_are_stored(self, db, journey, client):
        client.post("/request-account/", data=self.SPOUSE, headers=H)

        made = asks(db, journey)[0]
        assert made.spouse_name == "Carla Delgado"
        assert made.spouse_phone == "(573) 555-0199"

    def test_the_spouse_email_is_lowercased(self, db, journey, client):
        """It is an account address too, and two spellings of it are two
        people as far as any lookup goes."""
        client.post("/request-account/", data=self.SPOUSE, headers=H)

        assert asks(db, journey)[0].spouse_email == "carla@example.com"

    def test_they_are_in_the_email(self, db, journey, client):
        """Whoever sets the accounts up needs both addresses in front of
        them, not one and a sentence mentioning the other."""
        client.post("/request-account/", data=self.SPOUSE, headers=H)

        body = mail(db, journey)[0].body_text
        assert "Carla Delgado" in body
        assert "carla@example.com" in body
        assert "555-0199" in body

    def test_they_are_on_the_staff_list(self, db, journey, client, staff):
        client.post("/request-account/", data=self.SPOUSE, headers=H)

        page = client.get("/people/requests/", headers=H).get_data(as_text=True)

        assert "Carla Delgado" in page
        assert "carla@example.com" in page

    def test_a_request_with_no_spouse_still_works(self, db, journey, client):
        """Most will not have one, and the fields are optional."""
        client.post("/request-account/", data=ASK, headers=H)

        made = asks(db, journey)[0]
        assert made.spouse_email is None
        assert made.has_spouse is False

    def test_the_spouse_block_is_hidden_when_there_is_none(self, db, journey,
                                                           client, staff):
        """An empty labelled block on every request is noise on the screen
        somebody is working through."""
        client.post("/request-account/", data=ASK, headers=H)

        page = client.get("/people/requests/", headers=H).get_data(as_text=True)

        assert "Mill Street" in page
        assert ">Spouse<" not in page

    def test_a_phone_alone_is_enough_to_show_the_block(self, db, journey,
                                                       client, staff):
        """Somebody who gives one of the three has given something worth
        showing, and a block that needed all three would hide it."""
        client.post("/request-account/",
                    data={**ASK, "spouse_phone": "(573) 555-0199"}, headers=H)

        assert asks(db, journey)[0].has_spouse is True
