"""The dashboard rail and the People rail have to say the same thing.

They did not. Youth was added to the roster and landed on the People rail
only, because the dashboard keeps its own copy of the markup rather than using
`partials/rail.html`. Worse, and unnoticed for longer: the dashboard's Kids
number came from `Person.child_count`, every child on the roster, while
People's came from `Person.group_count(KID)`, which stops at thirteen. One
screen said "Kids 23" meaning kids and youth together, the other said "Kids
14, Youth 9", and the label was the same word on both.

That is the failure worth guarding. A missing segment is visible the moment
somebody looks. Two numbers under one label that disagree by the size of a
youth group are not visible at all, and a pastor plans rooms and volunteers
off whichever one he happened to open.

So these tests do not check markup. They put a known roster in and assert both
screens report the same counts for the same segments, which is the only thing
that actually matters and the only thing that stays true when somebody
rewrites either template.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from app.ages import KID, YOUTH
from app.models import Church, Household, Person
from app.models.base import utcnow
from app.stages import KIDS as FILTER_KIDS
from app.stages import YOUTH as FILTER_YOUTH
from app.stages import STAGES
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def years_ago(years):
    return date.today().replace(year=date.today().year - years)


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def roster(db, journey):
    """Three kids, two youth, two adults. Deliberately lopsided.

    The counts have to be different from each other, or a test asserting two
    screens agree would pass on a bug that showed the same wrong number
    twice.
    """
    household = Household(church_id=journey.id, name="The Webbs")
    db.session.add(household)
    db.session.flush()

    people = [
        Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               stage="member", household_id=household.id, approved_at=utcnow()),
        Person(church_id=journey.id, first_name="Dana", last_name="Webb",
               stage="leader", household_id=household.id, approved_at=utcnow()),
    ]
    for n, age in enumerate((4, 7, 9)):
        people.append(Person(church_id=journey.id, first_name=f"Kid{n}",
                             last_name="Webb", stage="member", is_child=True,
                             birthdate=years_ago(age), household_id=household.id))
    for n, age in enumerate((14, 16)):
        people.append(Person(church_id=journey.id, first_name=f"Youth{n}",
                             last_name="Webb", stage="member", is_child=True,
                             birthdate=years_ago(age), household_id=household.id))
    db.session.add_all(people)
    db.session.commit()
    return household


def numbers_on(client, path):
    """Every segment on a rail, as {label: count}.

    Read out of the rendered page rather than out of the context, because the
    context being right while the template reads the wrong variable is exactly
    how the Kids number went wrong.
    """
    page = client.get(path, headers=H)
    assert page.status_code == 200
    body = page.get_data(as_text=True)

    found = {}
    # Both rails put the label and the count in adjacent elements, under
    # different class names: rsname/rscount on the dashboard, sname/scount on
    # People. One pattern, either spelling.
    for label, count in re.findall(
        r'class="r?sname"[^>]*>\s*([^<]+?)\s*</\w+>\s*'
        r'<\w+ class="r?scount[^"]*"[^>]*>\s*(\d+)\s*</\w+>',
        body,
    ):
        found[label.strip()] = int(count)
    return found


class TestBothRailsShowTheSameSegments:
    def test_the_dashboard_has_a_youth_segment(self, db, journey, roster, staff):
        """The thing that was missing."""
        assert "Youth" in numbers_on(staff, "/")

    def test_people_has_a_youth_segment(self, db, journey, roster, staff):
        assert "Youth" in numbers_on(staff, "/people/")

    def test_the_two_rails_carry_identical_segments(
        self, db, journey, roster, staff
    ):
        assert set(numbers_on(staff, "/")) == set(numbers_on(staff, "/people/"))

    def test_the_segments_are_the_stages_plus_kids_and_youth(
        self, db, journey, roster, staff
    ):
        expected = {stage.label for stage in STAGES} | {"Kids", "Youth"}
        assert set(numbers_on(staff, "/")) == expected


class TestBothRailsShowTheSameNumbers:
    def test_every_segment_matches(self, db, journey, roster, staff):
        """The one that would have caught the real bug."""
        assert numbers_on(staff, "/") == numbers_on(staff, "/people/")

    def test_kids_excludes_youth_on_both(self, db, journey, roster, staff):
        """The actual defect: one rail counted every child as a kid.

        Three kids and two youth, so a rail that lumps them reads 5 and this
        fails. Asserting the literal numbers rather than just equality,
        because both rails agreeing on 5 would be both rails being wrong.
        """
        for path in ("/", "/people/"):
            counts = numbers_on(staff, path)
            assert counts["Kids"] == 3, f"{path} counted youth as kids"
            assert counts["Youth"] == 2, f"{path} has the wrong youth count"

    def test_they_match_what_the_model_says(self, db, journey, roster, staff):
        counts = numbers_on(staff, "/")
        assert counts["Kids"] == Person.group_count(journey.id, KID)
        assert counts["Youth"] == Person.group_count(journey.id, YOUTH)

    def test_a_birthday_moves_somebody_on_both_rails_at_once(
        self, db, journey, roster, staff
    ):
        """The rule is an age, so nobody edits anything for this to happen.

        A rail that cached a count, or counted a different way, would drift
        apart from the other one on exactly this.
        """
        before = numbers_on(staff, "/")
        twelve = next(p for p in roster.members if p.first_name == "Kid0")
        twelve.birthdate = years_ago(13) - timedelta(days=1)
        db.session.commit()

        after_dash = numbers_on(staff, "/")
        after_people = numbers_on(staff, "/people/")

        assert after_dash["Kids"] == before["Kids"] - 1
        assert after_dash["Youth"] == before["Youth"] + 1
        assert after_dash == after_people


class TestTheSegmentsLinkSomewhereReal:
    def test_both_kids_segments_point_at_the_same_filter(
        self, db, journey, roster, staff
    ):
        """The dashboard had 'kids' typed into the markup, which is how it
        came to be the rail that never heard about youth."""
        for path in ("/", "/people/"):
            body = staff.get(path, headers=H).get_data(as_text=True)
            assert f"stage={FILTER_KIDS}" in body
            assert f"stage={FILTER_YOUTH}" in body

    def test_the_youth_filter_returns_only_youth(self, db, journey, roster, staff):
        page = staff.get(f"/people/?stage={FILTER_YOUTH}", headers=H)
        body = page.get_data(as_text=True)
        assert "Youth0" in body and "Youth1" in body
        assert "Kid0" not in body

    def test_the_kids_filter_returns_only_kids(self, db, journey, roster, staff):
        page = staff.get(f"/people/?stage={FILTER_KIDS}", headers=H)
        body = page.get_data(as_text=True)
        assert "Kid0" in body
        assert "Youth0" not in body


class TestTheGuardWouldHaveCaught_It:
    """The reader's check: these assert the test above is not vacuous.

    `numbers_on` returning {} would make every equality assertion pass.
    """

    def test_the_scraper_finds_something(self, db, journey, roster, staff):
        assert numbers_on(staff, "/"), "the dashboard rail scraped empty"
        assert numbers_on(staff, "/people/"), "the People rail scraped empty"

    def test_the_scraper_finds_every_segment(self, db, journey, roster, staff):
        # Five stages plus kids and youth.
        assert len(numbers_on(staff, "/")) == len(STAGES) + 2
