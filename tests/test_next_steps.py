"""Putting your hand up: Baptism, Volunteer, and whatever comes next.

Two tiles on the member home screen, a short form behind each, and a staff
list of people waiting to hear back.

The things most worth holding in place are the ones that are decisions rather
than mechanics:

**Signing up does not write a next step.** The "Your next step" card is what
staff decided somebody should do. A card that fills itself in the moment
anybody taps a tile is a card people stop reading.

**Two taps is one ask.** Somebody who taps Baptism, reads the form, backs out
and taps it again has asked once, or the staff count stops being "people
waiting" and becomes "times a tile was tapped".

**The alert ignores the opt-out.** A staff member who turned off next-step
email turned off the drip series, not the message that somebody asked to be
baptised.

**Offers are a tuple, so the feature has to survive one being retired.** Rows
outlive the tuple, and a sign-up for something the church stopped running
still has to render.
"""

from __future__ import annotations

import re

import pytest

from app.models import Church, NextStepSignup, OutboxMessage, Person, User
from app.models.base import utcnow
from app.next_steps import BY_CODE, OFFERS, get, icon_path, label_for
from tests.conftest import JOURNEY_HOST, PASSWORD

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def linked(db, journey):
    """A member with a login, which is what the home screen requires."""
    person = Person(church_id=journey.id, first_name="Alicia", last_name="Romero",
                    stage="member", email="member@journeychurchsemo.com",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user = db.session.scalar(
        db.select(User).where(
            User.church_id == journey.id,
            User.email == "member@journeychurchsemo.com",
        )
    )
    user.person_id = person.id
    db.session.commit()
    return person


@pytest.fixture
def member(client, sign_in, linked):
    sign_in("member@journeychurchsemo.com")
    return client


def signups(db, journey):
    return db.session.scalars(
        db.select(NextStepSignup).where(NextStepSignup.church_id == journey.id)
    ).all()


def alerts(db, journey):
    return db.session.scalars(
        db.select(OutboxMessage).where(
            OutboxMessage.church_id == journey.id,
            OutboxMessage.category == "signup",
        )
    ).all()


class TestTheOffers:
    def test_baptism_and_volunteer_are_both_there(self):
        assert {o.code for o in OFFERS} == {"baptism", "volunteer"}

    def test_every_offer_is_complete(self):
        """A tile missing a subtitle renders a blank line under its name, and
        a form missing a prompt renders a box with no question."""
        for offer in OFFERS:
            for field in ("title", "subtitle", "icon", "tint", "heading",
                          "blurb", "prompt", "placeholder", "staff_line"):
                assert getattr(offer, field), f"{offer.code} has no {field}"

    def test_every_icon_resolves(self):
        """A tile with no glyph is a tile with a hole in it."""
        for offer in OFFERS:
            assert icon_path(offer.icon), offer.code

    def test_an_unknown_icon_does_not_crash(self):
        """Empty rather than raising: a missing glyph is not worth a 500 on
        somebody's home screen."""
        assert icon_path("not-a-glyph") == ""

    def test_the_tints_are_distinguishable(self):
        """The icon is the only colour on a tile, which is what makes six of
        them scannable without reading a word. Two offers sharing one defeats
        that."""
        tints = [o.tint for o in OFFERS]
        assert len(set(tints)) == len(tints)

    def test_a_retired_offer_still_has_a_name(self):
        """Rows outlive the tuple. A sign-up for something the church stopped
        running has to render as something a human can read."""
        assert get("child_dedication") is None
        assert label_for("child_dedication") == "Child Dedication"

    def test_codes_are_unique(self):
        assert len(BY_CODE) == len(OFFERS)


class TestTheTilesOnHome:
    def test_both_tiles_are_on_the_home_screen(self, app, db, journey, member):
        body = member.get("/me/", headers=H).get_data(as_text=True)
        assert "Take the next step" in body
        assert "Baptism" in body
        assert "Volunteer" in body

    def test_each_tile_links_to_its_own_form(self, app, db, journey, member):
        body = member.get("/me/", headers=H).get_data(as_text=True)
        for offer in OFFERS:
            assert f"/me/next-step/{offer.code}/" in body

    def test_a_tile_says_so_once_you_have_asked(
        self, app, db, journey, member, linked
    ):
        """Said rather than hidden. A tile that disappears reads as a bug,
        and somebody who asked last week wants to see the app still knows."""
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        body = member.get("/me/", headers=H).get_data(as_text=True)
        assert "You have asked" in body

    def test_the_grid_is_driven_by_the_tuple(self, app, db, journey, member):
        """Adding Child Dedication later has to be four lines in one file.
        If the titles were in the template this count would not move."""
        body = member.get("/me/", headers=H).get_data(as_text=True)
        assert body.count('class="nstile"') == len(OFFERS)


class TestSigningUp:
    def test_the_form_opens(self, app, db, journey, member):
        page = member.get("/me/next-step/baptism/", headers=H)
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Get baptised" in body
        assert "Anything you would like us to know?" in body

    def test_an_unknown_offer_is_a_404(self, app, db, journey, member):
        assert member.get("/me/next-step/nonsense/", headers=H).status_code == 404

    def test_submitting_records_the_ask(self, app, db, journey, member, linked):
        page = member.post("/me/next-step/baptism/",
                           data={"note": "I have been thinking about it."},
                           headers=H, follow_redirects=True)
        assert page.status_code == 200

        rows = signups(db, journey)
        assert len(rows) == 1
        assert rows[0].offer == "baptism"
        assert rows[0].person_id == linked.id
        assert rows[0].note == "I have been thinking about it."

    def test_the_note_is_optional(self, app, db, journey, member, linked):
        """The ask itself is the message. A required box is a toll on it."""
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        rows = signups(db, journey)
        assert len(rows) == 1
        assert rows[0].note is None

    def test_they_are_thanked(self, app, db, journey, member, linked):
        page = member.post("/me/next-step/volunteer/", data={"note": ""},
                           headers=H, follow_redirects=True)
        assert "Somebody will be in touch" in page.get_data(as_text=True)

    def test_it_lands_on_their_timeline(self, app, db, journey, member, linked):
        from app.models import PersonEvent

        member.post("/me/next-step/baptism/", data={"note": "a note"},
                    headers=H, follow_redirects=True)
        events = db.session.scalars(
            PersonEvent.for_person(journey.id, linked.id)
        ).all()
        assert any("Baptism" in e.summary for e in events)

    def test_the_note_is_not_copied_onto_the_timeline(
        self, app, db, journey, member, linked
    ):
        """One copy of what somebody wrote is enough, and the timeline is
        read by more people than the sign-up list is."""
        from app.models import PersonEvent

        secret = "something I would rather not repeat"
        member.post("/me/next-step/baptism/", data={"note": secret},
                    headers=H, follow_redirects=True)
        events = db.session.scalars(
            PersonEvent.for_person(journey.id, linked.id)
        ).all()
        assert not any(secret in (e.detail or "") for e in events)


class TestItDoesNotWriteANextStep:
    """The decision this feature turns on."""

    def test_the_next_step_card_stays_empty(
        self, app, db, journey, member, linked
    ):
        from app.models import NextStep

        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)

        assigned = db.session.scalars(
            NextStep.open_for_person(journey.id, linked.id)
        ).all()
        assert not assigned, (
            "signing up assigned a next step. That card is what staff "
            "decided somebody should do; one that fills itself in whenever a "
            "tile is tapped stops being read."
        )

    def test_the_home_screen_still_says_nothing_is_on_their_list(
        self, app, db, journey, member, linked
    ):
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        body = member.get("/me/", headers=H).get_data(as_text=True)
        assert "Nothing on your list" in body


class TestTwoTapsIsOneAsk:
    def test_asking_twice_makes_one_row(self, app, db, journey, member, linked):
        for _ in range(3):
            member.post("/me/next-step/baptism/", data={"note": ""},
                        headers=H, follow_redirects=True)
        assert len(signups(db, journey)) == 1

    def test_the_second_time_says_so(self, app, db, journey, member, linked):
        """Thanking somebody twice for something they did last week reads as
        nobody having looked."""
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        again = member.post("/me/next-step/baptism/", data={"note": ""},
                            headers=H, follow_redirects=True)
        assert "already asked" in again.get_data(as_text=True)

    def test_a_note_added_on_the_second_visit_is_kept(
        self, app, db, journey, member, linked
    ):
        """They came back to add something. Dropping it loses the only new
        information in the second visit."""
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        member.post("/me/next-step/baptism/", data={"note": "one more thing"},
                    headers=H, follow_redirects=True)
        assert signups(db, journey)[0].note == "one more thing"

    def test_two_different_offers_are_two_asks(
        self, app, db, journey, member, linked
    ):
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        member.post("/me/next-step/volunteer/", data={"note": ""},
                    headers=H, follow_redirects=True)
        assert len(signups(db, journey)) == 2

    def test_asking_again_after_it_is_handled_is_a_new_ask(
        self, app, db, journey, member, linked
    ):
        """Somebody baptised last year asking again is a new conversation,
        not a duplicate of a closed one."""
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        signups(db, journey)[0].handle()
        db.session.commit()

        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        assert len(signups(db, journey)) == 2


class TestStaffAreTold:
    def test_an_alert_goes_out(self, app, db, journey, member, linked):
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        assert alerts(db, journey), "nobody was told"

    def test_it_names_the_person_and_the_offer(
        self, app, db, journey, member, linked
    ):
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        subject = alerts(db, journey)[0].subject
        assert "Alicia" in subject
        assert "Baptism" in subject

    def test_what_they_wrote_is_not_in_the_alert_body(
        self, app, db, journey, member, linked
    ):
        """Behind the link, the same way a pastoral request works. A copy of
        somebody's words sitting in four inboxes cannot be taken back."""
        secret = "I am terrified of water and have never told anyone"
        member.post("/me/next-step/baptism/", data={"note": secret},
                    headers=H, follow_redirects=True)
        for message in alerts(db, journey):
            assert secret not in message.body_text

    def test_the_category_ignores_the_opt_out(self):
        """A staff member who turned off next-step email turned off the drip
        series, not the message that somebody asked to be baptised."""
        from app.categories import is_transactional

        assert is_transactional("signup")

    def test_a_second_ask_does_not_alert_again(
        self, app, db, journey, member, linked
    ):
        before = None
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        before = len(alerts(db, journey))
        member.post("/me/next-step/baptism/", data={"note": "more"},
                    headers=H, follow_redirects=True)
        assert len(alerts(db, journey)) == before

    def test_a_failing_alert_does_not_lose_the_ask(
        self, app, db, journey, member, linked, monkeypatch
    ):
        """A member tapping a tile and getting an error page learns the app
        is broken, when their ask was recorded and only the alert failed."""
        def explode(*args, **kwargs):
            raise RuntimeError("the alert machinery is having an afternoon")

        monkeypatch.setattr("app.alerts.tell_staff", explode)

        page = member.post("/me/next-step/baptism/", data={"note": ""},
                           headers=H, follow_redirects=True)
        assert page.status_code == 200
        assert len(signups(db, journey)) == 1


class TestTheStaffList:
    """Seeded through the model rather than through the member client.

    `member` and `staff` are the same underlying test client, so asking for
    both in one test signs in twice and whichever resolves last owns the
    session. That is the trap conftest describes, and it made these tests
    post as the pastor, create nothing, and then assert against an empty
    list. What is under test here is the staff screen, so the ask is seeded
    directly and only one identity is ever signed in.
    """

    def _ask(self, db, journey, person, offer="baptism", note="ready"):
        row = NextStepSignup(church_id=journey.id, person_id=person.id,
                             offer=offer, note=note)
        db.session.add(row)
        db.session.commit()
        return row

    def test_staff_can_see_who_is_waiting(
        self, app, db, journey, linked, staff
    ):
        self._ask(db, journey, linked)

        page = staff.get("/people/signups/", headers=H)
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Alicia" in body
        assert "Baptism" in body
        assert "ready" in body

    def test_it_is_linked_from_people(self, app, db, journey, staff):
        body = staff.get("/people/", headers=H).get_data(as_text=True)
        assert "/people/signups/" in body

    def test_the_count_is_on_the_button(
        self, app, db, journey, linked, staff
    ):
        """So staff can see there is something waiting without opening the
        page to find out."""
        self._ask(db, journey, linked)
        body = staff.get("/people/", headers=H).get_data(as_text=True)
        assert re.search(r"Sign-ups[^<]*&middot;\s*1", body)

    def test_marking_one_done_takes_it_off_the_list(
        self, app, db, journey, linked, staff
    ):
        row = self._ask(db, journey, linked)

        staff.post(f"/people/signups/{row.id}/done/", headers=H,
                   follow_redirects=True)
        db.session.refresh(row)
        assert row.is_handled

        body = staff.get("/people/signups/", headers=H).get_data(as_text=True)
        assert "Alicia" not in body

    def test_it_records_who_dealt_with_it(
        self, app, db, journey, linked, staff
    ):
        row = self._ask(db, journey, linked)
        staff.post(f"/people/signups/{row.id}/done/", headers=H,
                   follow_redirects=True)
        db.session.refresh(row)
        assert row.handled_by_name == "Pastor Reed"

    def test_marking_it_twice_does_not_rewrite_who(
        self, app, db, journey, linked, staff
    ):
        """A double-tapped button must not rewrite who dealt with it to
        whoever happened to tap second."""
        row = self._ask(db, journey, linked)
        staff.post(f"/people/signups/{row.id}/done/", headers=H,
                   follow_redirects=True)
        db.session.refresh(row)
        first = row.handled_at

        staff.post(f"/people/signups/{row.id}/done/", headers=H,
                   follow_redirects=True)
        db.session.refresh(row)
        assert row.handled_at == first

    def test_done_ones_are_a_click_away_not_mixed_in(
        self, app, db, journey, linked, staff
    ):
        row = self._ask(db, journey, linked)
        staff.post(f"/people/signups/{row.id}/done/", headers=H,
                   follow_redirects=True)

        body = staff.get("/people/signups/?done=1", headers=H).get_data(as_text=True)
        assert "Alicia" in body

    def test_a_member_cannot_see_the_staff_list(
        self, app, db, journey, member, linked
    ):
        page = member.get("/people/signups/", headers=H, follow_redirects=False)
        assert page.status_code in (302, 403)


class TestTenancy:
    def test_another_church_cannot_see_the_signup(
        self, app, db, journey, member, linked, staff
    ):
        from tests.conftest import RIVERBEND_HOST

        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)
        other = staff.get("/people/signups/",
                          headers={"Host": RIVERBEND_HOST},
                          follow_redirects=False)
        assert other.status_code in (302, 403, 404)

    def test_the_alert_does_not_reach_another_church(
        self, app, db, journey, member, linked
    ):
        member.post("/me/next-step/baptism/", data={"note": ""},
                    headers=H, follow_redirects=True)

        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        leaked = db.session.scalars(
            db.select(OutboxMessage).where(
                OutboxMessage.church_id == riverbend.id,
                OutboxMessage.category == "signup",
            )
        ).all()
        assert not leaked
