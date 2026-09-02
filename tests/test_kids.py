"""Increment 11: kids check-in.

The safety claims are the tests that matter. A PIN must never authorize a
pickup, a pickup code must never be reusable across sessions, and every
collection must leave a record.
"""

from datetime import timedelta

import pytest

from app.models import Checkin, CheckinSession, Church, Household, OutboxMessage, Person
from app.models.base import utcnow
from app.pickup import ALPHABET, CODE_LENGTH, generate_pickup_code, normalize
from tests.conftest import JOURNEY_HOST


@pytest.fixture
def journey(db):
    church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
    church.timezone = "America/Chicago"
    db.session.commit()
    return church


@pytest.fixture
def webbs(db, journey):
    household = Household(church_id=journey.id, name="Marcus and Dana Webb")
    db.session.add(household)
    db.session.flush()

    people = [
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               email="marcus@example.com", stage="member", household_id=household.id),
        Person(church_id=journey.id, first_name="Ellie", last_name="Webb",
               stage="member", household_id=household.id, is_child=True),
        Person(church_id=journey.id, first_name="Nate", last_name="Webb",
               stage="member", household_id=household.id, is_child=True),
    ]
    db.session.add_all(people)
    household.ensure_checkin_pin()
    db.session.commit()
    return household


@pytest.fixture
def sunday(db, journey):
    session = CheckinSession(
        church_id=journey.id, name="Sunday 9:30", starts_at=utcnow()
    )
    db.session.add(session)
    db.session.commit()
    return session


class TestPickupCodes:
    def test_a_code_is_letters_from_the_safe_alphabet(self):
        code = generate_pickup_code(lambda c: False)
        assert len(code) == CODE_LENGTH
        assert all(ch in ALPHABET for ch in code)

    def test_ambiguous_characters_are_excluded(self):
        """On a printed label I, O, S and Z read as 1, 0, 5 and 2."""
        for ch in "IOSZ01":
            assert ch not in ALPHABET

    def test_a_code_is_letters_not_digits(self):
        """A four-digit PIN and a four-digit pickup code on one label get
        confused, and the confusion runs in the dangerous direction."""
        assert generate_pickup_code(lambda c: False).isalpha()

    def test_a_taken_code_is_skipped(self):
        taken = {"ABCD"}
        for _ in range(60):
            assert generate_pickup_code(lambda c: c in taken) != "ABCD"

    def test_running_out_raises_rather_than_duplicating(self):
        with pytest.raises(RuntimeError, match="session scoping"):
            generate_pickup_code(lambda c: True)

    def test_codes_vary(self):
        assert len({generate_pickup_code(lambda c: False) for _ in range(40)}) > 1

    def test_normalize_handles_what_a_parent_types(self):
        assert normalize(" ab cd ") == "ABCD"
        assert normalize("abcd") == "ABCD"
        assert normalize(None) == ""
        assert normalize("") == ""


class TestTheTwoCodesAreNotInterchangeable:
    """The whole safety model of this increment."""

    def test_a_pin_is_digits_and_a_pickup_code_is_letters(self, db, webbs):
        assert webbs.checkin_pin.isdigit()
        assert generate_pickup_code(lambda c: False).isalpha()

    def test_a_household_pin_does_not_check_anyone_out(self, db, webbs, sunday, staff):
        """A permanent code must never authorize a collection.

        If it could, anyone who ever saw a label, a phone screen, or a sticker
        on a coat could collect a child weeks later.
        """
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [p.id for p in webbs.members if p.is_child]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        assert sunday.present_count == 2

        matches = Checkin.by_pickup_code(
            webbs.church_id, sunday.id, webbs.checkin_pin
        )
        assert matches == []

    def test_a_pickup_code_does_not_open_a_family_at_the_kiosk(
        self, db, webbs, sunday, staff
    ):
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [webbs.members[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        code = sunday.checkins[0].pickup_code

        r = staff.post(
            "/kids/kiosk/", data={"pin": code},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"do not recognise that code" in r.data

    def test_a_pickup_code_is_scoped_to_one_session(self, db, journey, webbs, sunday, staff):
        """Two families sharing a code six months apart is fine. On the same
        Sunday it is a child handed to the wrong adult."""
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [webbs.members[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        code = sunday.checkins[0].pickup_code

        sunday.close()
        later = CheckinSession(
            church_id=journey.id, name="Next Sunday",
            starts_at=utcnow() + timedelta(days=7),
        )
        db.session.add(later)
        db.session.commit()

        assert Checkin.by_pickup_code(journey.id, later.id, code) == []


class TestCheckIn:
    def test_siblings_share_one_code(self, db, webbs, sunday, staff):
        """A parent carries one code, not three."""
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [p.id for p in webbs.members if p.is_child]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        codes = {c.pickup_code for c in sunday.checkins}
        assert len(sunday.checkins) == 2
        assert len(codes) == 1

    def test_a_second_check_in_reuses_the_same_code(self, db, webbs, sunday, staff):
        children = [p for p in webbs.members if p.is_child]
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [children[0].id]},
            headers={"Host": JOURNEY_HOST},
        )
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [children[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        assert len({c.pickup_code for c in sunday.checkins}) == 1

    def test_a_child_cannot_be_checked_in_twice(self, db, webbs, sunday):
        """A double-tapped kiosk button must not make two children to account for."""
        child = [p for p in webbs.members if p.is_child][0]
        code = sunday.issue_pickup_code(webbs.id)
        for _ in range(2):
            db.session.add(
                Checkin(
                    church_id=webbs.church_id, session_id=sunday.id,
                    person_id=child.id, household_id=webbs.id, pickup_code=code,
                )
            )
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

    def test_the_route_ignores_a_repeat_rather_than_erroring(
        self, db, webbs, sunday, staff
    ):
        child = [p for p in webbs.members if p.is_child][0]
        for _ in range(2):
            staff.post(
                f"/kids/kiosk/family/{webbs.id}/",
                data={"person_id": [child.id]},
                headers={"Host": JOURNEY_HOST},
            )
        db.session.refresh(sunday)
        assert len(sunday.checkins) == 1

    def test_somebody_from_another_household_cannot_be_added(
        self, db, journey, webbs, sunday, staff
    ):
        """Otherwise a child lands under another family's pickup code."""
        other_household = Household(church_id=journey.id, name="The Vaughns")
        db.session.add(other_household)
        db.session.flush()
        stranger = Person(
            church_id=journey.id, first_name="Josiah", last_name="Vaughn",
            stage="member", household_id=other_household.id, is_child=True,
        )
        db.session.add(stranger)
        db.session.commit()

        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [stranger.id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        assert sunday.checkins == []

    def test_the_household_name_is_copied_not_derived(self, db, webbs, sunday, staff):
        """A child moving households later must not rewrite this Sunday."""
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [webbs.members[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        checkin = sunday.checkins[0]
        assert checkin.household_name == "Marcus and Dana Webb"

        webbs.name = "The Webb Family"
        db.session.commit()
        db.session.refresh(checkin)
        assert checkin.household_name == "Marcus and Dana Webb"

    def test_no_open_session_means_no_check_in(self, db, webbs, sunday, staff):
        sunday.close()
        db.session.commit()
        r = staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [webbs.members[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        assert r.status_code == 404


class TestCheckOut:
    """The half the demo omitted."""

    def _check_in(self, webbs, sunday, staff):
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [p.id for p in webbs.members if p.is_child]},
            headers={"Host": JOURNEY_HOST},
        )

    def test_a_collection_leaves_a_record(self, db, webbs, sunday, staff):
        self._check_in(webbs, sunday, staff)
        db.session.refresh(sunday)
        code = sunday.checkins[0].pickup_code

        staff.post(
            "/kids/checkout/",
            data={
                "code": code,
                "checkin_id": [c.id for c in sunday.checkins],
                "collected_by": "Dana Webb",
            },
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        for checkin in sunday.checkins:
            assert checkin.checked_out_at is not None
            assert checkin.collected_by == "Dana Webb"
            assert checkin.checked_out_by_user_id is not None

    def test_who_collected_can_be_somebody_not_on_the_roster(
        self, db, webbs, sunday, staff
    ):
        """It is often a grandparent. A name written down beats a dropdown
        that cannot express the truth."""
        self._check_in(webbs, sunday, staff)
        db.session.refresh(sunday)
        code = sunday.checkins[0].pickup_code

        staff.post(
            "/kids/checkout/",
            data={"code": code, "checkin_id": [sunday.checkins[0].id],
                  "collected_by": "Grandma Webb"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        assert sunday.checkins[0].collected_by == "Grandma Webb"

    def test_the_wrong_code_finds_nobody(self, db, webbs, sunday, staff):
        self._check_in(webbs, sunday, staff)
        assert Checkin.by_pickup_code(webbs.church_id, sunday.id, "ZZZZ") == []

    def test_ids_alone_cannot_check_a_child_out(self, db, journey, webbs, sunday, staff):
        """A posted id without the code would collect a child whose code the
        person at the desk never had."""
        self._check_in(webbs, sunday, staff)
        db.session.refresh(sunday)
        target = sunday.checkins[0]

        staff.post(
            "/kids/checkout/",
            data={"code": "ZZZZ", "checkin_id": [target.id],
                  "collected_by": "Somebody"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(target)
        assert target.checked_out_at is None

    def test_checking_out_twice_does_not_overwrite_the_first_record(
        self, db, webbs, sunday, staff
    ):
        self._check_in(webbs, sunday, staff)
        db.session.refresh(sunday)
        checkin = sunday.checkins[0]
        code = checkin.pickup_code

        staff.post(
            "/kids/checkout/",
            data={"code": code, "checkin_id": [checkin.id], "collected_by": "Dana Webb"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(checkin)
        first_time, first_by = checkin.checked_out_at, checkin.collected_by

        staff.post(
            "/kids/checkout/",
            data={"code": code, "checkin_id": [checkin.id], "collected_by": "Someone Else"},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(checkin)
        assert checkin.checked_out_at == first_time
        assert checkin.collected_by == first_by

    def test_closing_a_session_does_not_check_anyone_out(
        self, db, webbs, sunday, staff
    ):
        """A child still in a room is exactly what staff need to see."""
        self._check_in(webbs, sunday, staff)
        staff.post(
            f"/kids/sessions/{sunday.id}/toggle/", headers={"Host": JOURNEY_HOST}
        )
        db.session.refresh(sunday)
        assert not sunday.is_open
        assert sunday.present_count == 2

    def test_present_and_collected_counts(self, db, webbs, sunday, staff):
        self._check_in(webbs, sunday, staff)
        db.session.refresh(sunday)
        assert (sunday.present_count, sunday.collected_count) == (2, 0)

        sunday.checkins[0].check_out(collected_by="Dana Webb")
        db.session.commit()
        db.session.refresh(sunday)
        assert (sunday.present_count, sunday.collected_count) == (1, 1)


class TestKioskSecurity:
    def test_the_kiosk_needs_a_signed_in_volunteer(self, client):
        """A tablet accepting PIN attempts from anyone eventually enumerates
        every household code in the church."""
        for path in ("/kids/kiosk/", "/kids/checkout/"):
            r = client.get(path, headers={"Host": JOURNEY_HOST})
            assert r.status_code == 302
            assert "/auth/login" in r.headers["Location"]

    def test_a_member_cannot_open_the_kiosk(self, member):
        assert member.get("/kids/kiosk/", headers={"Host": JOURNEY_HOST}).status_code == 403

    def test_repeated_wrong_pins_lock_the_kiosk(self, db, webbs, sunday, staff):
        from app.blueprints.kids import MAX_PIN_ATTEMPTS

        for _ in range(MAX_PIN_ATTEMPTS):
            staff.post("/kids/kiosk/", data={"pin": "0000"}, headers={"Host": JOURNEY_HOST})

        r = staff.post(
            "/kids/kiosk/", data={"pin": webbs.checkin_pin},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"Too many tries" in r.data

    def test_a_correct_pin_clears_the_attempt_counter(self, db, webbs, sunday, staff):
        staff.post("/kids/kiosk/", data={"pin": "0000"}, headers={"Host": JOURNEY_HOST})
        staff.post(
            "/kids/kiosk/", data={"pin": webbs.checkin_pin}, headers={"Host": JOURNEY_HOST}
        )
        r = staff.post("/kids/kiosk/", data={"pin": "0000"}, headers={"Host": JOURNEY_HOST},
                       follow_redirects=True)
        assert b"Too many tries" not in r.data

    def test_a_pin_from_another_church_does_not_open_a_family(
        self, db, journey, staff
    ):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Household(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.flush()
        theirs.ensure_checkin_pin()
        db.session.add(
            CheckinSession(church_id=journey.id, name="Sunday", starts_at=utcnow())
        )
        db.session.commit()

        r = staff.post(
            "/kids/kiosk/", data={"pin": theirs.checkin_pin},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"do not recognise that code" in r.data

    def test_a_household_from_another_church_is_a_404(self, db, journey, sunday, staff):
        riverbend = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        theirs = Household(church_id=riverbend.id, name="Theirs")
        db.session.add(theirs)
        db.session.commit()

        r = staff.get(
            f"/kids/kiosk/family/{theirs.id}/", headers={"Host": JOURNEY_HOST}
        )
        assert r.status_code == 404


class TestForgotTheCode:
    def test_it_emails_the_pin(self, db, webbs, sunday, staff):
        staff.post(
            "/kids/kiosk/forgot/",
            data={"email": "marcus@example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        message = db.session.scalars(db.select(OutboxMessage)).one()
        assert webbs.checkin_pin in message.body_text
        # Transactional, so it reaches a parent who unsubscribed from the rest.
        assert message.category == "kids_checkin"

    def test_the_email_says_the_pin_is_not_a_pickup_code(self, db, webbs, sunday, staff):
        staff.post(
            "/kids/kiosk/forgot/",
            data={"email": "marcus@example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        body = db.session.scalars(db.select(OutboxMessage)).one().body_text
        assert "does not authorize a pickup" in body

    def test_an_unknown_address_gets_the_same_answer(self, db, webbs, sunday, staff):
        known = staff.post(
            "/kids/kiosk/forgot/", data={"email": "marcus@example.com"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        unknown = staff.post(
            "/kids/kiosk/forgot/", data={"email": "nobody@example.com"},
            headers={"Host": JOURNEY_HOST}, follow_redirects=True,
        )
        assert b"If we have that address" in known.data
        assert b"If we have that address" in unknown.data

    def test_an_unknown_address_queues_nothing(self, db, webbs, sunday, staff):
        staff.post(
            "/kids/kiosk/forgot/", data={"email": "nobody@example.com"},
            headers={"Host": JOURNEY_HOST},
        )
        assert db.session.scalars(db.select(OutboxMessage)).all() == []


class TestSessions:
    def test_the_kiosk_uses_the_most_recent_open_session(self, db, journey):
        old = CheckinSession(
            church_id=journey.id, name="Early", starts_at=utcnow() - timedelta(hours=3)
        )
        new = CheckinSession(
            church_id=journey.id, name="Later", starts_at=utcnow() - timedelta(minutes=5)
        )
        db.session.add_all([old, new])
        db.session.commit()
        assert CheckinSession.open_session(journey.id).name == "Later"

    def test_a_closed_session_is_not_picked_up(self, db, journey, sunday):
        sunday.close()
        db.session.commit()
        assert CheckinSession.open_session(journey.id) is None

    def test_still_present_counts_only_open_sessions(self, db, webbs, sunday, staff):
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [p.id for p in webbs.members if p.is_child]},
            headers={"Host": JOURNEY_HOST},
        )
        assert Checkin.still_present(webbs.church_id) == 2

        db.session.refresh(sunday)
        sunday.close()
        db.session.commit()
        assert Checkin.still_present(webbs.church_id) == 0

    def test_deleting_a_session_takes_its_checkins(self, db, webbs, sunday, staff):
        staff.post(
            f"/kids/kiosk/family/{webbs.id}/",
            data={"person_id": [webbs.members[1].id]},
            headers={"Host": JOURNEY_HOST},
        )
        db.session.refresh(sunday)
        db.session.delete(sunday)
        db.session.commit()
        assert db.session.scalars(db.select(Checkin)).all() == []
