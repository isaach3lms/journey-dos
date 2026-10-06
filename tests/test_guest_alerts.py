"""Telling somebody a connect card came in.

A card that nobody hears about is a worse outcome than no card at all: the
guest handed over their details believing something would happen, and the
church finds out on Thursday when somebody opens a page they had no reason to
open. So the alert is the half of this feature that makes the other half mean
anything.

Three rules, and the first two are about what the alert does *not* carry:

1. **Never the guest's words.** Staff are told a card exists and given a
   link. A copy of what somebody wrote, sitting in four inboxes, cannot be
   taken back. The pastoral alert works this way for the same reason.
2. **An account request carries only what making an account needs.** A name
   and an address. The note a guest wrote is about the church, not about a
   login, and it goes somewhere that may not be this church at all.
3. **Alerting can never fail the submission.** The guest's card is already
   written when the alert runs. An error page at that point tells them it did
   not go through, and they fill it in again.
"""

import pytest

from app import guests
from app.models import Church, GuestCard, OutboxMessage, Person, User
from app.models.base import utcnow
from app.models.church import DEFAULT_ACCOUNT_REQUEST_EMAIL
from app.models.guest import HEARD_FRIEND
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}

CARD = {
    "first_name": "Marcus",
    "last_name": "Delgado",
    "email": "marcus@example.com",
    "phone": "(573) 555-0144",
    "heard": HEARD_FRIEND,
    "note": "Please pray for my mother, she is in hospital in Cape.",
}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def queued(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(OutboxMessage.church_id == journey.id)
    ).all()


def to(messages, address):
    return [m for m in messages if m.to_email == address]


class TestTheChurchIsTold:
    def test_a_card_emails_the_named_addresses(self, db, journey, client):
        journey.guest_alert_emails = "welcome@journeychurchsemo.com"
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), "welcome@journeychurchsemo.com")

    def test_several_addresses_each_get_one(self, db, journey, client):
        journey.guest_alert_emails = "a@journey.test\nb@journey.test"
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)

        sent = queued(db, journey)
        assert len(to(sent, "a@journey.test")) == 1
        assert len(to(sent, "b@journey.test")) == 1

    def test_with_nobody_named_every_staff_account_is_told(self, db, journey,
                                                           client):
        """The right default for a church that has not thought about it: the
        people who can already read a card are the people told it exists."""
        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), "pastor@journeychurchsemo.com")

    def test_a_deactivated_staff_account_is_not_told(self, db, journey, client):
        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), "gone@journeychurchsemo.com") == []

    def test_a_kiosk_login_is_not_told(self, db, journey, client):
        """The check-in iPad is a login, not a person with an inbox."""
        kiosk = User(church_id=journey.id, email="kiosk@journey.invalid",
                     name="Lobby iPad", role="staff", is_kiosk=True)
        kiosk.set_password(PASSWORD)
        db.session.add(kiosk)
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), "kiosk@journey.invalid") == []

    def test_a_member_is_not_told(self, db, journey, client):
        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), "member@journeychurchsemo.com") == []

    def test_another_church_is_not_told(self, db, journey, client):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))

        client.post("/welcome/", data=CARD, headers=H)

        assert db.session.scalars(
            db.select(OutboxMessage).where(
                OutboxMessage.church_id == riverbend.id)
        ).all() == []

    def test_staff_get_it_on_their_phone_too(self, db, journey, client,
                                             monkeypatch):
        """A card arrives on a Sunday morning, when nobody is reading email.
        The notification is the half that reaches anybody."""
        from app.push import MemoryPushTransport
        from app.push import send as push_send

        provider = MemoryPushTransport(addresses_people=True)
        monkeypatch.setattr(push_send, "build_push_transport",
                            lambda config: provider)

        pastor = db.session.scalar(db.select(User).where(
            User.email == "pastor@journeychurchsemo.com"))
        person = Person(church_id=journey.id, first_name="Reed", last_name="Kane",
                        email=pastor.email, stage="leader", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        pastor.person_id = person.id
        pastor.ensure_push_external_id()
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)

        assert provider.sent, "nothing was pushed"
        _, message = provider.sent[0]
        assert "Marcus" in message.body

    def test_the_notification_opens_the_cards(self, db, journey, client,
                                              monkeypatch):
        """A notification that opens the home screen makes somebody hunt for
        the thing it was about."""
        from app.push import MemoryPushTransport
        from app.push import send as push_send

        provider = MemoryPushTransport(addresses_people=True)
        monkeypatch.setattr(push_send, "build_push_transport",
                            lambda config: provider)
        pastor = db.session.scalar(db.select(User).where(
            User.email == "pastor@journeychurchsemo.com"))
        person = Person(church_id=journey.id, first_name="Reed", last_name="Kane",
                        email=pastor.email, stage="leader", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        pastor.person_id = person.id
        pastor.ensure_push_external_id()
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)

        _, message = provider.sent[0]
        assert message.url == "/people/guests/"


class TestWhatTheAlertDoesNotSay:
    def test_it_never_carries_what_the_guest_wrote(self, db, journey, client):
        """The rule this whole module is built around. Somebody's prayer
        request copied into four inboxes cannot be taken back."""
        client.post("/welcome/", data=CARD, headers=H)

        for message in queued(db, journey):
            assert "hospital" not in message.body_text
            assert "my mother" not in message.body_text

    def test_it_links_to_the_card_instead(self, db, journey, client):
        client.post("/welcome/", data=CARD, headers=H)

        assert all("/people/guests/" in m.body_text
                   for m in queued(db, journey))

    def test_it_names_the_guest(self, db, journey, client):
        """Enough to know whether to walk across the room."""
        client.post("/welcome/", data=CARD, headers=H)

        assert all("Marcus Delgado" in m.subject for m in queued(db, journey))


class TestAskingForAnAccount:
    def test_the_box_is_on_the_card(self, client):
        page = client.get("/welcome/", headers=H).get_data(as_text=True)

        assert 'name="wants_account"' in page

    def test_ticking_it_is_recorded(self, db, journey, client):
        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        card = db.session.scalars(db.select(GuestCard)).all()[0]
        assert card.wants_account is True

    def test_it_goes_to_the_platform_by_default(self, db, journey, client):
        """Creating a login is administration, not something church staff
        normally do."""
        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        assert to(queued(db, journey), DEFAULT_ACCOUNT_REQUEST_EMAIL)

    def test_a_church_can_route_it_to_itself(self, db, journey, client):
        journey.account_request_email = "office@journeychurchsemo.com"
        db.session.commit()

        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        sent = queued(db, journey)
        assert to(sent, "office@journeychurchsemo.com")
        assert to(sent, DEFAULT_ACCOUNT_REQUEST_EMAIL) == []

    def test_not_ticking_it_sends_nothing(self, db, journey, client):
        client.post("/welcome/", data=CARD, headers=H)

        assert to(queued(db, journey), DEFAULT_ACCOUNT_REQUEST_EMAIL) == []

    def test_the_request_carries_what_making_an_account_needs(self, db, journey,
                                                              client):
        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        ask = to(queued(db, journey), DEFAULT_ACCOUNT_REQUEST_EMAIL)[0]
        assert "Marcus Delgado" in ask.body_text
        assert "marcus@example.com" in ask.body_text

    def test_the_request_does_not_carry_the_note(self, db, journey, client):
        """It leaves the church. A prayer request has no business in it."""
        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        ask = to(queued(db, journey), DEFAULT_ACCOUNT_REQUEST_EMAIL)[0]
        assert "hospital" not in ask.body_text

    def test_the_list_shows_who_asked(self, db, journey, client, staff):
        client.post("/welcome/", data={**CARD, "wants_account": "1"}, headers=H)

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert "Asked for an account" in page


class TestAlertingNeverBreaksTheCard:
    def test_a_card_is_saved_even_when_the_alert_blows_up(self, db, journey,
                                                          client, monkeypatch):
        """The guest has already handed over their details. An error page
        here tells them it did not go through, and they fill it in again."""
        def boom(*args, **kwargs):
            raise RuntimeError("the mail provider is down")

        monkeypatch.setattr(guests, "alert_staff", boom)

        answer = client.post("/welcome/", data=CARD, headers=H)

        assert answer.status_code == 200
        assert "Thank you" in answer.get_data(as_text=True)
        assert db.session.scalars(db.select(GuestCard)).all()

    def test_the_same_card_does_not_alert_twice(self, db, journey, client):
        """A retried request or a double-tapped button is normal, and two
        identical alerts is how somebody starts ignoring them."""
        journey.guest_alert_emails = "welcome@journeychurchsemo.com"
        db.session.commit()

        client.post("/welcome/", data=CARD, headers=H)
        card = db.session.scalars(db.select(GuestCard)).all()[0]
        guests.alert_staff(journey, card, link="https://x.test/people/guests/",
                           path="/people/guests/")
        db.session.commit()

        assert len(to(queued(db, journey), "welcome@journeychurchsemo.com")) == 1


class TestFindingThePage:
    def test_the_roster_has_a_button(self, client, staff):
        """The dashboard tile opens the cards too, but somebody working
        through the roster is not looking at the dashboard."""
        page = client.get("/people/", headers=H).get_data(as_text=True)

        assert "View connect cards" in page
        assert "/people/guests/" in page

    def test_the_button_carries_the_count(self, db, journey, client, staff):
        """So staff can see there is something to read without opening the
        page to find out."""
        client.post("/welcome/", data=CARD, headers=H)

        page = client.get("/people/", headers=H).get_data(as_text=True)

        assert "View connect cards" in page
        assert ">1<" in page or "&middot; 1" in page


class TestChangingWhoIsTold:
    def test_staff_can_set_the_addresses(self, db, journey, client, staff):
        client.post("/settings/guests/",
                    data={"emails": "welcome@journeychurchsemo.com"}, headers=H)

        db.session.refresh(journey)
        assert journey.guest_recipients == ["welcome@journeychurchsemo.com"]

    def test_staff_can_redirect_account_requests(self, db, journey, client,
                                                 staff):
        client.post("/settings/guests/",
                    data={"emails": "", "account_email": "office@journey.test"},
                    headers=H)

        db.session.refresh(journey)
        assert journey.account_request_recipients == ["office@journey.test"]

    def test_clearing_it_falls_back_to_the_platform(self, db, journey, client,
                                                    staff):
        journey.account_request_email = "office@journey.test"
        db.session.commit()

        client.post("/settings/guests/",
                    data={"emails": "", "account_email": ""}, headers=H)

        db.session.refresh(journey)
        assert journey.account_request_recipients == [
            DEFAULT_ACCOUNT_REQUEST_EMAIL]

    def test_the_change_is_audited(self, db, journey, client, staff):
        """Who reads what a guest handed over is the same kind of decision as
        a role change, and is recorded the same way."""
        from app.models import AuditEvent

        client.post("/settings/guests/",
                    data={"emails": "welcome@journeychurchsemo.com"}, headers=H)

        entries = db.session.scalars(
            db.select(AuditEvent).where(AuditEvent.church_id == journey.id)
        ).all()
        assert any("welcome@journeychurchsemo.com" in (e.detail or "")
                   for e in entries)

    def test_a_leader_cannot_change_it(self, db, journey, client, leader):
        client.post("/settings/guests/",
                    data={"emails": "leaked@example.com"}, headers=H)

        db.session.refresh(journey)
        assert journey.guest_recipients == []

    def test_the_settings_page_shows_the_row(self, client, staff):
        page = client.get("/settings/", headers=H).get_data(as_text=True)

        assert "Connect card alerts" in page
        assert "Send account requests to" in page
