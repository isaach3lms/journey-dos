"""The printed roster: who is here, and when did anything last happen.

Four dates from four places, on one sheet that goes into a staff meeting.
Each column is a judgement about what counts, and those judgements are what
these tests hold still:

- A household's last kids check-in belongs to everybody in that household,
  not just to the child who was checked in.
- An assignment somebody declined is not serving.
- A service next Sunday is not "last served".
- Nothing from another church, ever, on a sheet that gets printed and left
  on a table.
"""

from datetime import timedelta

import pytest

from app.models import (
    Checkin,
    CheckinSession,
    Church,
    ContactLog,
    Household,
    Person,
    Service,
    ServiceAssignment,
    ServiceTrack,
)
from app.models.base import utcnow
from app.reports import rows_for, sections_for
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


def house(db, church, name="The Barrett family"):
    home = Household(church_id=church.id, name=name)
    db.session.add(home)
    db.session.commit()
    return home


def person(db, church, first="Amy", last="Davis", **kw):
    row = Person(
        church_id=church.id, first_name=first, last_name=last,
        email=kw.pop("email", f"{first.lower()}.{last.lower()}@example.com"),
        stage=kw.pop("stage", "member"), approved_at=utcnow(), **kw,
    )
    db.session.add(row)
    db.session.commit()
    return row


def checked_in(db, church, kid, when):
    session = CheckinSession(church_id=church.id, name="Sunday", starts_at=when)
    db.session.add(session)
    db.session.flush()
    row = Checkin(
        church_id=church.id, session_id=session.id, person_id=kid.id,
        household_id=kid.household_id, pickup_code=f"P{kid.id:04d}",
    )
    db.session.add(row)
    db.session.flush()
    row.checked_in_at = when
    db.session.commit()
    return row


def scheduled(db, church, who, when, status="accepted"):
    service = Service(church_id=church.id, name="Sunday", starts_at=when)
    db.session.add(service)
    db.session.flush()
    track = db.session.scalars(
        db.select(ServiceTrack).where(ServiceTrack.service_id == service.id)
    ).first()
    if track is None:
        track = ServiceTrack(church_id=church.id, service_id=service.id,
                             name="Worship", position=1)
        db.session.add(track)
        db.session.flush()
    db.session.add(ServiceAssignment(
        church_id=church.id, service_id=service.id, track_id=track.id,
        person_id=who.id, status=status,
    ))
    db.session.commit()


def contacted(db, church, who, when):
    row = ContactLog(church_id=church.id, person_id=who.id, method="call",
                     summary="Checked in")
    db.session.add(row)
    db.session.flush()
    row.occurred_at = when
    db.session.commit()


def row_for(db, church, who):
    return [r for r in rows_for(church.id) if r.person_id == who.id][0]


class TestTheHousehold:
    def test_a_person_in_one_carries_its_name(self, db, journey):
        home = house(db, journey)
        amy = person(db, journey, household_id=home.id)
        assert row_for(db, journey, amy).household == "The Barrett family"
        assert row_for(db, journey, amy).in_household is True

    def test_a_person_in_none_says_so(self, db, journey):
        amy = person(db, journey)
        assert row_for(db, journey, amy).household is None
        assert row_for(db, journey, amy).in_household is False


class TestTheKidsCheckIn:
    def test_it_belongs_to_everyone_in_the_household(self, db, journey):
        """The question is whether this family is still bringing their
        children, so the answer is the same on the parent's row as on the
        child's."""
        home = house(db, journey)
        parent = person(db, journey, "Rob", household_id=home.id)
        kid = person(db, journey, "Ella", is_child=True, household_id=home.id)
        when = utcnow() - timedelta(days=6)
        checked_in(db, journey, kid, when)

        assert row_for(db, journey, parent).last_kid_checkin is not None
        assert row_for(db, journey, kid).last_kid_checkin is not None

    def test_it_is_the_most_recent_one(self, db, journey):
        home = house(db, journey)
        parent = person(db, journey, "Rob", household_id=home.id)
        kid = person(db, journey, "Ella", is_child=True, household_id=home.id)
        checked_in(db, journey, kid, utcnow() - timedelta(days=200))
        checked_in(db, journey, kid, utcnow() - timedelta(days=5))

        age = utcnow() - row_for(db, journey, parent).last_kid_checkin
        assert age.days <= 6

    def test_another_household_does_not_lend_you_theirs(self, db, journey):
        ours = house(db, journey, "The Reed family")
        theirs = house(db, journey, "The Webb family")
        rob = person(db, journey, "Rob", household_id=ours.id)
        kid = person(db, journey, "Ella", is_child=True, household_id=theirs.id)
        checked_in(db, journey, kid, utcnow() - timedelta(days=3))

        assert row_for(db, journey, rob).last_kid_checkin is None

    def test_somebody_with_no_household_has_none(self, db, journey):
        amy = person(db, journey)
        assert row_for(db, journey, amy).last_kid_checkin is None


class TestTheLastTimeTheyServed:
    def test_a_past_service_counts(self, db, journey):
        rob = person(db, journey, "Rob", stage="volunteer")
        scheduled(db, journey, rob, utcnow() - timedelta(days=14))
        assert row_for(db, journey, rob).last_served is not None

    def test_the_most_recent_one_wins(self, db, journey):
        rob = person(db, journey, "Rob", stage="volunteer")
        scheduled(db, journey, rob, utcnow() - timedelta(days=300))
        scheduled(db, journey, rob, utcnow() - timedelta(days=20))
        age = utcnow() - row_for(db, journey, rob).last_served
        assert age.days <= 21

    def test_a_declined_assignment_is_not_serving(self, db, journey):
        """Being asked is not serving. Counting it would show somebody as
        active on the strength of an invitation they turned down."""
        rob = person(db, journey, "Rob", stage="volunteer")
        scheduled(db, journey, rob, utcnow() - timedelta(days=7),
                  status="declined")
        assert row_for(db, journey, rob).last_served is None

    def test_next_sunday_is_not_the_last_time(self, db, journey):
        """Somebody rostered for next week has not served since March, and
        printing next week's date under "last served" makes a person nobody
        has seen in months read as current."""
        rob = person(db, journey, "Rob", stage="volunteer")
        scheduled(db, journey, rob, utcnow() + timedelta(days=4))
        assert row_for(db, journey, rob).last_served is None

    def test_a_future_booking_does_not_hide_a_real_one(self, db, journey):
        rob = person(db, journey, "Rob", stage="volunteer")
        scheduled(db, journey, rob, utcnow() - timedelta(days=60))
        scheduled(db, journey, rob, utcnow() + timedelta(days=4))
        age = utcnow() - row_for(db, journey, rob).last_served
        assert 59 <= age.days <= 61

    def test_somebody_who_never_has_shows_nothing(self, db, journey):
        amy = person(db, journey)
        assert row_for(db, journey, amy).last_served is None


class TestTheLastContact:
    def test_the_most_recent_one_of_any_method(self, db, journey):
        amy = person(db, journey)
        contacted(db, journey, amy, utcnow() - timedelta(days=400))
        contacted(db, journey, amy, utcnow() - timedelta(days=9))
        age = utcnow() - row_for(db, journey, amy).last_contact
        assert age.days <= 10

    def test_nobody_has_called_shows_nothing(self, db, journey):
        amy = person(db, journey)
        assert row_for(db, journey, amy).last_contact is None


class TestWhoIsOnIt:
    def test_an_archived_person_is_not(self, db, journey):
        amy = person(db, journey)
        amy.is_archived = True
        db.session.commit()
        assert [r for r in rows_for(journey.id) if r.person_id == amy.id] == []

    def test_another_church_is_not(self, db, journey):
        """This sheet gets printed and left on a table."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        theirs = person(db, riverbend, "Sam", "Okafor")
        home = house(db, riverbend, "The Okafor family")
        theirs.household_id = home.id
        db.session.commit()
        contacted(db, riverbend, theirs, utcnow() - timedelta(days=2))

        names = {r.name for r in rows_for(journey.id)}
        assert "Sam Okafor" not in names

    def test_their_contact_does_not_land_on_our_row(self, db, journey):
        """The aggregate is grouped by person, so a missing church filter
        would not duplicate rows, it would quietly attach the wrong date
        to the right name."""
        riverbend = db.session.scalar(
            db.select(Church).where(Church.slug == "riverbend")
        )
        ours = person(db, journey, "Amy")
        theirs = person(db, riverbend, "Amy", "Other")
        contacted(db, riverbend, theirs, utcnow() - timedelta(days=1))
        assert row_for(db, journey, ours).last_contact is None


class TestTheSections:
    def test_unfiltered_it_is_one_per_stage(self, db, journey):
        person(db, journey, "Amy", stage="visitor")
        person(db, journey, "Rob", stage="volunteer")
        labels = [s.label for s in sections_for(journey)]
        assert "Visitor" in labels and "Volunteer" in labels

    def test_a_stage_with_nobody_in_it_is_left_out(self, db, journey):
        person(db, journey, "Amy", stage="visitor")
        labels = [s.label for s in sections_for(journey)]
        assert "Disciple" not in labels

    def test_filtered_it_is_one_section(self, db, journey):
        person(db, journey, "Amy", stage="visitor")
        person(db, journey, "Rob", stage="volunteer")
        sections = sections_for(journey, stage="visitor")
        assert len(sections) == 1
        assert sections[0].label == "Visitor"
        assert [r.name for r in sections[0].rows] == ["Amy Davis"]

    def test_the_sections_add_up_to_everyone(self, db, journey):
        for i, stage in enumerate(("visitor", "member", "volunteer", "leader")):
            person(db, journey, f"P{i}", stage=stage)
        total = sum(s.count for s in sections_for(journey))
        assert total == len(rows_for(journey.id))

    def test_a_retired_stage_still_prints(self, db, journey, monkeypatch):
        """Somebody on a stage the church no longer lists is still a person
        the church has. Dropping them is the one thing this report must
        never do.

        Written with a patched stage list rather than a row carrying a dead
        code, because a check constraint on `person.stage` refuses to store
        one. That constraint is the reason this is a transition rather than
        a steady state: `app/stages.py` says stages become per-church, and
        between a church retiring one and the rows being moved off it, the
        list and the data disagree. This is that window.
        """
        import app.reports as reports
        from app.stages import STAGES

        person(db, journey, "Amy", stage="visitor")
        person(db, journey, "Rob", stage="member")

        monkeypatch.setattr(
            reports, "stages_for",
            lambda church=None: tuple(s for s in STAGES if s.code != "visitor"),
        )

        sections = sections_for(journey)
        names = [r.name for s in sections for r in s.rows]
        assert "Amy Davis" in names, "a retired stage dropped a person"
        assert "Rob Davis" in names
        # Under a heading of its own, at the end, rather than folded into
        # somebody else's stage.
        assert sections[-1].label == "Visitor"


class TestItDoesNotAskOncePerPerson:
    def test_the_roster_costs_a_fixed_number_of_queries(self, db, journey):
        """A loop that asks each person for their last contact is 127
        queries on Journey today and 2,000 on a church four times the
        size, on a request that returns a PDF."""
        from sqlalchemy import event

        home = house(db, journey)
        for i in range(30):
            person(db, journey, f"P{i}", household_id=home.id)

        seen = []
        engine = db.session.get_bind()

        def count(conn, cursor, statement, params, context, many):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        try:
            rows = rows_for(journey.id)
        finally:
            event.remove(engine, "before_cursor_execute", count)

        assert len(rows) >= 30
        assert len(seen) <= 2, f"{len(seen)} queries for {len(rows)} people"


class TestTheRoute:
    def test_a_leader_can_download_it(self, db, journey, leader):
        person(db, journey, "Amy", stage="visitor")
        response = leader.get("/people/report.pdf", headers=H)
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data[:4] == b"%PDF"

    def test_a_member_cannot(self, db, journey, member):
        response = member.get("/people/report.pdf", headers=H)
        assert response.status_code in (302, 403, 404)

    def test_signed_out_cannot(self, db, journey, client):
        response = client.get("/people/report.pdf", headers=H)
        assert response.status_code in (302, 401, 403)

    def test_it_is_not_cached(self, db, journey, leader):
        """Names, households, and when each family was last seen."""
        response = leader.get("/people/report.pdf", headers=H)
        assert "no-store" in response.headers.get("Cache-Control", "")

    def test_a_stage_filter_works(self, db, journey, leader):
        person(db, journey, "Amy", stage="visitor")
        response = leader.get("/people/report.pdf?stage=visitor", headers=H)
        assert response.status_code == 200

    def test_an_invented_stage_is_refused(self, db, journey, leader):
        """Rather than silently reporting on everyone under a heading that
        says otherwise."""
        assert leader.get("/people/report.pdf?stage=banana",
                          headers=H).status_code == 404

    def test_the_kids_filter_works(self, db, journey, leader):
        home = house(db, journey)
        person(db, journey, "Ella", is_child=True, household_id=home.id)
        assert leader.get("/people/report.pdf?stage=kids",
                          headers=H).status_code == 200

    def test_the_roster_page_links_to_it(self, db, journey, staff):
        body = staff.get("/people/", headers=H).data.decode()
        assert "/people/report.pdf" in body

    def test_the_link_carries_the_filter(self, db, journey, staff):
        """So the sheet matches whatever list somebody is looking at."""
        body = staff.get("/people/?stage=visitor", headers=H).data.decode()
        assert "report.pdf?stage=visitor" in body


class TestWhatIsActuallyInThePdf:
    def _text(self, pdf_bytes):
        import io

        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() for page in reader.pages)

    def test_the_names_and_households_are_on_it(self, db, journey, leader):
        home = house(db, journey, "The Reed family")
        person(db, journey, "Amy", "Davis", stage="visitor", household_id=home.id)

        text = self._text(leader.get("/people/report.pdf", headers=H).data)
        assert "Amy Davis" in text
        assert "The Reed family" in text
        assert "Visitor" in text

    def test_somebody_with_no_household_is_said_so_not_left_blank(
        self, db, journey, leader
    ):
        person(db, journey, "Amy", "Davis", stage="visitor")
        text = self._text(leader.get("/people/report.pdf", headers=H).data)
        assert "No household" in text

    def test_the_legend_explains_every_column(self, db, journey, leader):
        """Each column is a decision about what counts. A reader deciding
        who to call this week deserves to know which decisions."""
        person(db, journey, "Amy", stage="visitor")
        text = self._text(leader.get("/people/report.pdf", headers=H).data)
        for phrase in ("did not decline", "still to come are not counted",
                       "any child of that household"):
            assert phrase in text

    def test_a_person_with_no_history_reads_as_no_record(
        self, db, journey, leader
    ):
        person(db, journey, "Amy", stage="visitor")
        text = self._text(leader.get("/people/report.pdf", headers=H).data)
        assert "no record" in text

    def test_the_church_name_is_the_title(self, db, journey, leader):
        import io

        import pypdf

        person(db, journey, "Amy", stage="visitor")
        data = leader.get("/people/report.pdf", headers=H).data
        reader = pypdf.PdfReader(io.BytesIO(data))
        assert "Journey" in (reader.metadata.title or "")


class TestTheWording:
    def test_one_day_is_not_one_days(self):
        from app.roster_pdf import _count

        assert _count(1, "day") == "1 day"
        assert _count(2, "day") == "2 days"
        assert _count(1, "year") == "1 year"

    def test_a_missing_accent_does_not_break_the_page(self):
        """`accent_hex` is nullable, and a church without one still prints."""
        from app.roster_pdf import _hex_to_rgb

        assert _hex_to_rgb(None) == _hex_to_rgb("not a colour")
        assert _hex_to_rgb("#485B38") != _hex_to_rgb(None)


class TestTheAggregatesAreScopedToTheChurch:
    """The `church_id` filters on the two aggregates, pinned structurally.

    These cannot be tested through behaviour. Both aggregates are joined
    against a person list already scoped to one church, on `person_id` and
    `household_id`, and ids do not collide between churches: delete either
    filter and every test in this file still passes. That was checked, not
    assumed.

    They are tested anyway, because the reason they exist is real even
    though it is not correctness. The aggregate runs before the join, so
    without them every church's contact log and every church's check-ins
    are grouped on every request. These two tests are what stops somebody
    deleting a filter that looks redundant, reads as redundant, and is.
    """

    def _sql(self, maker):
        from app.reports import (
            _last_checkin_by_household,
            _last_contact_by_person,
        )

        which = {"contacts": _last_contact_by_person,
                 "checkins": _last_checkin_by_household}[maker]
        return str(which(1).element.compile(
            compile_kwargs={"literal_binds": True}
        ))

    def test_the_contact_aggregate_filters_by_church(self):
        assert "contact_log.church_id = 1" in self._sql("contacts")

    def test_the_checkin_aggregate_filters_by_church(self):
        assert "checkin.church_id = 1" in self._sql("checkins")

    def test_they_group_rather_than_scanning_per_person(self):
        """One grouped row per person, not one query per person."""
        assert "GROUP BY" in self._sql("contacts")
        assert "GROUP BY" in self._sql("checkins")
