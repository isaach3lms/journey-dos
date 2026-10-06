"""The connect card: a public form that writes to the roster.

It is the only form in this product anybody on the internet can post to, and
the only one that creates a person without a human in the loop. Nearly every
test here is about that fact rather than about the fields.

The three failures worth preventing, in order of how much damage they do:

1. **A member demoted to visitor.** Somebody who has attended for ten years
   fills in a card out of politeness and is moved back to the front of a
   follow-up series written for strangers, then emailed as a newcomer.
2. **A roster filling with duplicates.** A family fills a card in September
   and again in January; a guest fills one on their phone and again on the
   welcome desk tablet. A public form is the fastest way yet to end up with
   four records of one person.
3. **A form that feeds nothing.** The number on the dashboard comes from
   `first_seen_on`, so a card that does not set it is a card nobody sees.
"""

from datetime import date, timedelta

import pytest

from app import guests
from app.models import Church, GuestCard, Person, SequenceEnrollment, User
from app.models.base import utcnow
from app.models.guest import HEARD_FRIEND
from tests.conftest import JOURNEY_HOST, PASSWORD, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}

GOOD = {
    "first_name": "Marcus",
    "last_name": "Delgado",
    "email": "marcus@example.com",
    "phone": "(573) 555-0144",
    "heard": HEARD_FRIEND,
    "note": "Visiting with my sister. Would love to know about kids church.",
    "wants_contact": "1",
}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def cards(db, journey):
    return db.session.scalars(
        db.select(GuestCard).where(GuestCard.church_id == journey.id)
    ).all()


def person_named(db, journey, first):
    return db.session.scalar(
        db.select(Person).where(Person.church_id == journey.id,
                               Person.first_name == first)
    )


class TestFillingItIn:
    def test_a_card_creates_somebody_on_the_roster(self, db, journey, client):
        client.post("/welcome/", data=GOOD, headers=H)

        marcus = person_named(db, journey, "Marcus")
        assert marcus is not None
        assert marcus.stage == "visitor"
        assert marcus.email == "marcus@example.com"

    def test_it_sets_the_date_the_dashboard_counts(self, db, journey, client):
        """The tile groups people by `first_seen_on`. A card that leaves it
        empty is a guest the church never sees a number for, and nothing on
        any screen would say why."""
        client.post("/welcome/", data=GOOD, headers=H)

        assert person_named(db, journey, "Marcus").first_seen_on == date.today()

    def test_the_tile_counts_it(self, db, journey, client, staff):
        """End to end, through the real dashboard code rather than the
        column: this is the connection the whole feature exists for."""
        from app.dashboard import guests_tile, week_starts

        client.post("/welcome/", data=GOOD, headers=H)
        starts = week_starts(date.today())

        assert guests_tile(journey.id, starts).value == "1"

    def test_the_card_keeps_what_they_wrote(self, db, journey, client):
        client.post("/welcome/", data=GOOD, headers=H)

        card = cards(db, journey)[0]
        assert card.note.startswith("Visiting with my sister")
        assert card.heard == HEARD_FRIEND
        assert card.wants_contact is True
        assert card.is_new_person is True

    def test_the_card_points_at_the_person_it_made(self, db, journey, client):
        client.post("/welcome/", data=GOOD, headers=H)

        card = cards(db, journey)[0]
        assert card.person_id == person_named(db, journey, "Marcus").id

    def test_a_guest_is_started_on_the_follow_up(self, db, journey, client):
        """A connect card is somebody asking to be contacted. The series
        written for exactly that is the point of entering them at all."""
        client.post("/welcome/", data=GOOD, headers=H)

        marcus = person_named(db, journey, "Marcus")
        assert db.session.scalars(
            db.select(SequenceEnrollment).where(
                SequenceEnrollment.person_id == marcus.id)
        ).all() != []

    def test_the_thank_you_is_shown_without_a_redirect(self, client):
        """A lobby wifi that drops between two requests would show a browser
        error after the card had already been saved."""
        answer = client.post("/welcome/", data=GOOD, headers=H)

        assert answer.status_code == 200
        assert "Thank you" in answer.get_data(as_text=True)

    def test_no_sign_in_is_needed(self, client):
        """The person filling it in has no account and never will."""
        assert client.get("/welcome/", headers=H).status_code == 200


class TestWhatItRefuses:
    def test_a_card_with_no_name_is_refused(self, db, journey, client):
        answer = client.post("/welcome/", data={**GOOD, "first_name": " "},
                             headers=H)

        assert cards(db, journey) == []
        assert "first name" in answer.get_data(as_text=True)

    def test_a_card_with_no_way_to_answer_is_refused(self, db, journey, client):
        """A card nobody can reply to is not a connect card."""
        answer = client.post(
            "/welcome/", data={**GOOD, "email": "", "phone": ""}, headers=H)

        assert cards(db, journey) == []
        assert "no way for anyone to get back to you" in answer.get_data(as_text=True)

    def test_a_refused_card_hands_back_what_they_typed(self, client):
        """Somebody on a phone who has to retype it all does not retype it."""
        answer = client.post("/welcome/", data={**GOOD, "email": "", "phone": ""},
                             headers=H)

        assert "Marcus" in answer.get_data(as_text=True)

    def test_either_a_phone_or_an_email_is_enough(self, db, journey, client):
        client.post("/welcome/", data={**GOOD, "email": ""}, headers=H)

        assert person_named(db, journey, "Marcus") is not None

    def test_an_unknown_heard_value_does_not_refuse_the_card(self, db, journey,
                                                             client):
        """A stale form or a probe. Dropping the value keeps the counts
        meaningful; refusing the card loses a guest over a dropdown."""
        client.post("/welcome/", data={**GOOD, "heard": "carrier pigeon"},
                    headers=H)

        card = cards(db, journey)[0]
        assert card.heard is None
        assert card.person_id is not None


class TestKeepingTheBotsOut:
    def test_a_filled_trap_writes_nothing(self, db, journey, client):
        client.post("/welcome/", data={**GOOD, "website": "http://spam.test"},
                    headers=H)

        assert cards(db, journey) == []
        assert person_named(db, journey, "Marcus") is None

    def test_a_trapped_bot_is_told_it_worked(self, client):
        """Answering a bot with an error teaches it to stop filling the
        field, and the field only works while they keep filling it."""
        answer = client.post("/welcome/",
                             data={**GOOD, "website": "http://spam.test"},
                             headers=H)

        assert "Thank you" in answer.get_data(as_text=True)

    def test_the_trap_is_not_visible_to_a_person(self, client):
        page = client.get("/welcome/", headers=H).get_data(as_text=True)

        assert 'tabindex="-1"' in page
        assert 'aria-hidden="true"' in page

    def test_a_second_card_within_seconds_is_refused(self, db, journey, client):
        client.post("/welcome/", data=GOOD, headers=H)
        client.post("/welcome/", data={**GOOD, "first_name": "Other",
                                       "email": "other@example.com"}, headers=H)

        assert len(cards(db, journey)) == 1

    def test_the_cooldown_is_seconds_not_one_per_person(self, db, journey,
                                                        client, monkeypatch):
        """A welcome desk tablet takes cards one after another. A limit of
        one per browser would stop the second family in the queue."""
        from app.blueprints import public

        monkeypatch.setattr(public, "COOLDOWN_SECONDS", 0)
        client.post("/welcome/", data=GOOD, headers=H)
        client.post("/welcome/", data={**GOOD, "first_name": "Priya",
                                       "email": "priya@example.com"}, headers=H)

        assert len(cards(db, journey)) == 2


class TestItDoesNotWreckTheRoster:
    def test_an_existing_person_is_matched_by_email(self, db, journey, client):
        existing = Person(church_id=journey.id, first_name="Marcus",
                          last_name="Delgado", email="marcus@example.com",
                          stage="member", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        assert db.session.scalar(
            db.select(db.func.count(Person.id)).where(
                Person.church_id == journey.id,
                Person.first_name == "Marcus")
        ) == 1

    def test_a_member_who_fills_one_in_is_not_demoted(self, db, journey, client):
        """The worst thing this form could do. Ten years of attendance
        rewritten to visitor, and a welcome series sent to somebody who runs
        a ministry."""
        existing = Person(church_id=journey.id, first_name="Marcus",
                          last_name="Delgado", email="marcus@example.com",
                          stage="leader", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        db.session.refresh(existing)
        # The card has to have landed on them, not beside them. Without this
        # the test passes when matching is broken entirely: a duplicate is
        # created and the untouched leader still reads as "not demoted".
        assert cards(db, journey)[0].person_id == existing.id
        assert existing.stage == "leader"
        assert db.session.scalars(
            db.select(SequenceEnrollment).where(
                SequenceEnrollment.person_id == existing.id)
        ).all() == []

    def test_a_match_is_recorded_as_a_known_person(self, db, journey, client):
        """"We had 27 guests" and "27 people we had never met" are different
        numbers, and a church eventually asks for the second."""
        db.session.add(Person(church_id=journey.id, first_name="Marcus",
                              last_name="Delgado", email="marcus@example.com",
                              stage="member", approved_at=utcnow()))
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        assert cards(db, journey)[0].is_new_person is False

    def test_a_phone_matches_even_when_it_is_typed_differently(self, db, journey,
                                                               client):
        """Nobody types their own number the same way twice."""
        existing = Person(church_id=journey.id, first_name="Marcus",
                          last_name="Delgado", phone="5735550144",
                          stage="member", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        client.post("/welcome/", data={**GOOD, "email": ""}, headers=H)

        assert cards(db, journey)[0].person_id == existing.id

    def test_a_matched_person_gains_a_missing_detail(self, db, journey, client):
        existing = Person(church_id=journey.id, first_name="Marcus",
                          last_name="Delgado", email="marcus@example.com",
                          stage="member", approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        db.session.refresh(existing)
        assert existing.phone is not None

    def test_a_correction_on_the_roster_is_not_overwritten(self, db, journey,
                                                           client):
        """Staff looked it up. A guest retyping it from memory is not better
        information."""
        existing = Person(church_id=journey.id, first_name="Marcus",
                          last_name="Delgado", email="marcus@example.com",
                          phone="5735559999", stage="member",
                          approved_at=utcnow())
        db.session.add(existing)
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        db.session.refresh(existing)
        assert existing.phone == "5735559999"

    def test_an_archived_person_is_not_matched(self, db, journey, client):
        """Somebody archived and coming back is a new guest, not a revived
        row that staff deliberately put away."""
        db.session.add(Person(church_id=journey.id, first_name="Marcus",
                              last_name="Delgado", email="marcus@example.com",
                              stage="member", is_archived=True,
                              approved_at=utcnow()))
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        assert cards(db, journey)[0].is_new_person is True

    def test_another_churchs_person_is_never_matched(self, db, journey, client):
        """The whole tenancy rule, on the one form a stranger can reach."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = Person(church_id=riverbend.id, first_name="Marcus",
                        last_name="Delgado", email="marcus@example.com",
                        stage="member", approved_at=utcnow())
        db.session.add(theirs)
        db.session.commit()

        client.post("/welcome/", data=GOOD, headers=H)

        card = cards(db, journey)[0]
        assert card.person_id != theirs.id
        assert card.is_new_person is True

    def test_a_card_belongs_to_the_church_whose_address_was_used(
            self, db, journey, client):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))

        client.post("/welcome/", data=GOOD, headers={"Host": RIVERBEND_HOST})

        assert cards(db, journey) == []
        assert db.session.scalar(
            db.select(db.func.count(GuestCard.id)).where(
                GuestCard.church_id == riverbend.id)
        ) == 1


class TestReadingTheCards:
    def test_a_leader_can_see_them(self, db, journey, client, leader):
        client.post("/welcome/", data=GOOD, headers=H)

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert "Marcus Delgado" in page
        assert "Would love to know about kids church" in page

    def test_the_note_is_shown_whole(self, db, journey, client, staff):
        """The reason the page exists. A note cut to fit a row stops being
        read, and it is the one part of a card a pastor needs in full."""
        long_note = "We just moved here from Cape. " * 12
        client.post("/welcome/", data={**GOOD, "note": long_note}, headers=H)

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert long_note.strip() in page

    def test_it_links_to_the_person(self, db, journey, client, staff):
        client.post("/welcome/", data=GOOD, headers=H)
        marcus = person_named(db, journey, "Marcus")

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert f"/people/{marcus.id}/" in page

    def test_it_shows_the_link_guests_fill_in(self, client, staff):
        """Staff have to be able to get the address onto a sign or a QR code,
        and there is nowhere else in the product that tells them it."""
        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert "/welcome/" in page

    def test_a_member_cannot_read_them(self, db, journey, client, member):
        """Cards carry what somebody wrote about themselves."""
        answer = client.get("/people/guests/", headers=H)

        assert answer.status_code in (302, 403, 404)

    def test_a_stranger_cannot_read_them(self, client):
        answer = client.get("/people/guests/", headers=H)

        assert answer.status_code in (302, 401, 403)

    def test_another_churchs_cards_are_not_listed(self, db, journey, client,
                                                  staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        db.session.add(GuestCard(church_id=riverbend.id, first_name="Nadia",
                                 last_name="Osei", email="nadia@example.com"))
        db.session.commit()

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert "Nadia" not in page

    def test_the_dashboard_number_opens_the_list(self, db, journey, client,
                                                 staff):
        """What was asked for: the tile is the way in."""
        client.post("/welcome/", data=GOOD, headers=H)

        page = client.get("/", headers=H).get_data(as_text=True)

        assert "/people/guests/" in page


class TestClearingTheRubbish:
    def test_a_card_can_be_discarded(self, db, journey, client, staff):
        client.post("/welcome/", data=GOOD, headers=H)
        card = cards(db, journey)[0]

        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        db.session.refresh(card)
        assert card.is_discarded is True

    def test_a_discarded_card_leaves_the_list(self, db, journey, client, staff):
        client.post("/welcome/", data=GOOD, headers=H)
        card = cards(db, journey)[0]
        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        page = client.get("/people/guests/", headers=H).get_data(as_text=True)

        assert "Marcus Delgado" not in page

    def test_discarding_a_card_does_not_touch_the_person(self, db, journey,
                                                         client, staff):
        """Two different things. Clearing a junk submission says the card is
        noise, not that somebody should be removed from the church."""
        client.post("/welcome/", data=GOOD, headers=H)
        card = cards(db, journey)[0]
        person_id = card.person_id

        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        person = db.session.get(Person, person_id)
        assert person is not None
        assert person.is_archived is False

    def test_a_discard_is_not_a_delete(self, db, journey, client, staff):
        """A row that vanishes takes with it any evidence of what the form
        was being used for."""
        client.post("/welcome/", data=GOOD, headers=H)
        card = cards(db, journey)[0]
        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        assert db.session.get(GuestCard, card.id) is not None

    def test_a_discarded_card_can_be_put_back(self, db, journey, client, staff):
        client.post("/welcome/", data=GOOD, headers=H)
        card = cards(db, journey)[0]
        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        client.post(f"/people/guests/{card.id}/discard/",
                    data={"restore": "1"}, headers=H)

        db.session.refresh(card)
        assert card.is_discarded is False

    def test_another_churchs_card_cannot_be_discarded(self, db, journey, client,
                                                      staff):
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend"))
        theirs = GuestCard(church_id=riverbend.id, first_name="Nadia",
                           last_name="Osei", email="nadia@example.com")
        db.session.add(theirs)
        db.session.commit()

        answer = client.post(f"/people/guests/{theirs.id}/discard/", headers=H)

        db.session.refresh(theirs)
        assert answer.status_code in (302, 404)
        assert theirs.is_discarded is False

    def test_a_member_cannot_discard(self, db, journey, client, member):
        card = GuestCard(church_id=journey.id, first_name="Marcus",
                         last_name="Delgado", email="marcus@example.com")
        db.session.add(card)
        db.session.commit()

        client.post(f"/people/guests/{card.id}/discard/", headers=H)

        db.session.refresh(card)
        assert card.is_discarded is False
