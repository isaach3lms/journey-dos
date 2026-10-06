"""The two markings under a person's snapshot.

Both exist because the system cannot work them out. Giving is mirrored from
an outside platform and misses anybody who gives by bank transfer or in an
envelope, so a tither flag derived from the gift rows would be wrong about
exactly the families staff most want to know about. A background check
happens entirely outside this app.

The one that needs care is the background check. It is a safeguarding record,
and the question asked after something has gone wrong is "who said this was
done, and when", which a column holding only the current value cannot answer.
So it is a date, not a boolean, and every change goes on the timeline.
"""

from datetime import date, timedelta

import pytest

from app.models import Church, Person, PersonEvent
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST, RIVERBEND_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def marta(db, journey):
    person = Person(church_id=journey.id, first_name="Marta", last_name="Reyes",
                    email="marta@example.com", stage="volunteer",
                    approved_at=utcnow())
    db.session.add(person)
    db.session.commit()
    return person


def events(db, person):
    return db.session.scalars(
        db.select(PersonEvent).where(PersonEvent.person_id == person.id)
    ).all()


class TestTheBoxesAreThere:
    def test_the_snapshot_offers_both(self, client, staff, marta):
        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        assert "Regular tither" in page
        assert "Background check ordered" in page

    def test_they_are_editable_without_opening_anything(self, client, staff,
                                                        marta):
        """These exist to be ticked on the day the thing happens. One extra
        click is the difference between a record staff keep and one they mean
        to keep."""
        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        assert f"/people/{marta.id}/flags/" in page
        assert 'name="is_regular_giver"' in page
        assert 'name="background_check_ordered"' in page

    def test_a_ticked_box_comes_back_ticked(self, db, client, staff, marta):
        """Parsed rather than matched against the raw markup. The first
        version of this test fell back to looking for "checked" anywhere on
        the page, which passes on any page with any ticked box on it and
        would have told me nothing."""
        import re

        marta.is_regular_giver = True
        db.session.commit()

        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        def box(name):
            match = re.search(r"<input[^>]*name=\"" + name + r"\"[^>]*>", page)
            assert match, f"no {name} box on the page"
            return "checked" in match.group(0)

        assert box("is_regular_giver") is True
        assert box("background_check_ordered") is False


class TestSettingThem:
    def test_the_tither_box_saves(self, db, client, staff, marta):
        client.post(f"/people/{marta.id}/flags/",
                    data={"is_regular_giver": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.is_regular_giver is True

    def test_unticking_it_saves_too(self, db, client, staff, marta):
        """A form posts nothing for an unticked box, so the absence has to
        mean false rather than "leave it alone"."""
        marta.is_regular_giver = True
        db.session.commit()

        client.post(f"/people/{marta.id}/flags/", data={}, headers=H)

        db.session.refresh(marta)
        assert marta.is_regular_giver is False

    def test_the_background_check_records_the_day_it_was_ordered(
            self, db, client, staff, marta):
        """The point of it being a date. A tick on its own cannot tell a
        check ordered on Friday from one sitting since March."""
        client.post(f"/people/{marta.id}/flags/",
                    data={"background_check_ordered": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.background_check_ordered_on == date.today()
        assert marta.background_check_ordered is True

    def test_unticking_the_check_clears_the_date(self, db, client, staff, marta):
        """A date left behind on an unticked box is a record that says two
        things."""
        marta.background_check_ordered_on = date.today() - timedelta(days=40)
        db.session.commit()

        client.post(f"/people/{marta.id}/flags/", data={}, headers=H)

        db.session.refresh(marta)
        assert marta.background_check_ordered_on is None
        assert marta.background_check_ordered is False

    def test_both_can_be_set_at_once(self, db, client, staff, marta):
        client.post(f"/people/{marta.id}/flags/",
                    data={"is_regular_giver": "1",
                          "background_check_ordered": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.is_regular_giver is True
        assert marta.background_check_ordered is True

    def test_re_ticking_does_not_move_the_date(self, db, client, staff, marta):
        """Somebody opening the page and pressing save must not reset the
        clock on a check that has been waiting six weeks."""
        ordered = date.today() - timedelta(days=42)
        marta.background_check_ordered_on = ordered
        db.session.commit()

        client.post(f"/people/{marta.id}/flags/",
                    data={"background_check_ordered": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.background_check_ordered_on == ordered


class TestTheTimelineRecordsIt:
    def test_ordering_a_check_is_on_the_timeline(self, db, client, staff, marta):
        """A safeguarding record. "Who said this was done, and when" is the
        question asked after something has gone wrong, and a column holding
        only the current value answers nothing."""
        before = len(events(db, marta))

        client.post(f"/people/{marta.id}/flags/",
                    data={"background_check_ordered": "1"}, headers=H)

        after = events(db, marta)
        assert len(after) == before + 1
        assert "ordered" in after[-1].detail.lower()

    def test_the_timeline_says_who_did_it(self, db, client, staff, marta):
        client.post(f"/people/{marta.id}/flags/",
                    data={"background_check_ordered": "1"}, headers=H)

        assert events(db, marta)[-1].actor_name

    def test_removing_a_check_is_recorded_too(self, db, client, staff, marta):
        """Taking a safeguarding marking off is at least as worth recording
        as putting one on."""
        marta.background_check_ordered_on = date.today()
        db.session.commit()
        before = len(events(db, marta))

        client.post(f"/people/{marta.id}/flags/", data={}, headers=H)

        after = events(db, marta)
        assert len(after) == before + 1
        assert "no longer" in after[-1].detail.lower()

    def test_saving_with_nothing_changed_records_nothing(self, db, client,
                                                         staff, marta):
        """A timeline filling with "nothing changed" is a timeline nobody
        reads."""
        before = len(events(db, marta))

        client.post(f"/people/{marta.id}/flags/", data={}, headers=H)

        assert len(events(db, marta)) == before


class TestWhatTheSnapshotShows:
    def test_it_shows_when_the_check_was_ordered(self, db, client, staff, marta):
        marta.background_check_ordered_on = date(2026, 3, 3)
        db.session.commit()

        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        assert "March 3, 2026" in page

    def test_it_shows_how_long_it_has_been_waiting(self, db, client, staff,
                                                   marta):
        """The number anybody actually wants. A check ordered six weeks ago
        and still open is the thing to chase."""
        marta.background_check_ordered_on = date.today() - timedelta(days=42)
        db.session.commit()

        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        assert "Waiting 42 days" in page

    def test_a_check_ordered_today_does_not_say_waiting(self, db, client, staff,
                                                        marta):
        """"Waiting 0 days" reads as a fault."""
        marta.background_check_ordered_on = date.today()
        db.session.commit()

        page = client.get(f"/people/{marta.id}/", headers=H).get_data(as_text=True)

        assert "Waiting 0 days" not in page


class TestWhoCanChangeThem:
    def test_a_leader_can(self, db, client, leader, marta):
        """A leader running the kids team is exactly who ticks a background
        check, and leaders already see everything else on this page."""
        client.post(f"/people/{marta.id}/flags/",
                    data={"background_check_ordered": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.background_check_ordered is True

    def test_a_member_cannot(self, db, client, member, marta):
        client.post(f"/people/{marta.id}/flags/",
                    data={"is_regular_giver": "1"}, headers=H)

        db.session.refresh(marta)
        assert marta.is_regular_giver is False

    def test_a_stranger_cannot(self, db, client, marta):
        answer = client.post(f"/people/{marta.id}/flags/",
                             data={"is_regular_giver": "1"}, headers=H)

        db.session.refresh(marta)
        assert answer.status_code in (302, 401, 403)
        assert marta.is_regular_giver is False

    def test_another_churchs_person_cannot_be_marked(self, db, journey, client,
                                                     staff, marta):
        """A person id is a number in a URL, and the only thing stopping one
        church marking another's volunteer is the tenant check."""
        answer = client.post(f"/people/{marta.id}/flags/",
                             data={"background_check_ordered": "1"},
                             headers={"Host": RIVERBEND_HOST})

        db.session.refresh(marta)
        assert answer.status_code in (302, 404)
        assert marta.background_check_ordered is False


class TestNothingDerivesTheTitherFlag:
    def test_a_person_with_gifts_is_not_marked_automatically(self, db, journey,
                                                             client, staff,
                                                             marta):
        """It is what staff know, not what the gift list adds up to. Deriving
        it would be wrong about exactly the families who give by bank
        transfer or in an envelope, which is the group staff most want to
        know about."""
        from app.models import ExternalGift

        db.session.add(ExternalGift(
            church_id=journey.id, person_id=marta.id, amount_cents=10000,
            received_on=date.today(), provider="test", provider_txn_id="g1",
        ))
        db.session.commit()

        db.session.refresh(marta)
        assert marta.is_regular_giver is False
