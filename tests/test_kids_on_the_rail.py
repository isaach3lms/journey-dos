"""Children counted on their own, and the Guest stage retired.

A child carries their family's stage so check-in and the roster have
something to sort by. Counting that into the rail told staff that 32 adults
had committed to the church when nine of them were in the kids room.
"""

import pytest

from app import dashboard
from app.models import Church, Household, Person
from app.models.base import utcnow
from app.stages import KIDS, STAGE_BY_CODE, STAGES, STAGE_CODES
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def family(db):
    """Two adult members and three children in one household."""
    church = journey(db)
    home = Household(church_id=church.id, name="The Romero family")
    db.session.add(home)
    db.session.flush()

    people = []
    for first, child in (("Alicia", False), ("Marco", False),
                         ("Sofia", True), ("Mateo", True), ("Lucia", True)):
        person = Person(church_id=church.id, first_name=first, last_name="Romero",
                        stage="member", household_id=home.id, is_child=child,
                        approved_at=utcnow())
        db.session.add(person)
        people.append(person)
    db.session.commit()
    return people


class TestTheGuestStageIsGone:
    def test_it_is_not_a_stage(self):
        assert "guest" not in STAGE_CODES
        assert STAGE_BY_CODE.get("guest") is None

    def test_the_rail_is_five_stages(self):
        """Six until Attender was retired too. Spelled out rather than
        derived, so a stage disappearing is a test failure somebody reads
        rather than a tautology that passes on any list."""
        assert [s.code for s in STAGES] == [
            "visitor", "member", "volunteer", "disciple", "leader"]

    def test_the_order_has_no_hole_in_it(self):
        assert [s.order for s in STAGES] == list(range(len(STAGES)))

    def test_the_database_refuses_it(self, db):
        """The check constraint is what stops an import file putting somebody
        on a stage the application no longer has."""
        import sqlalchemy

        church = journey(db)
        db.session.add(Person(church_id=church.id, first_name="Old", last_name="Guest",
                              stage="guest"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_the_follow_up_sequence_did_eventually_die(self):
        """It moved to Attender when Guest went, and went when Attender did.

        Kept as a test rather than deleted because the thing worth asserting
        is that it is gone from the registry entirely. A sequence left in
        `SEQUENCES` pointing at a stage that no longer exists would sit on the
        dashboard's automation list looking live while the engine's trigger
        check silently never matched.
        """
        from app.sequences import SEQUENCE_CODES, get

        assert get("guest_follow_up") is None
        assert "guest_follow_up" not in SEQUENCE_CODES

    def test_nothing_recommends_a_next_step_for_it(self):
        from app.stages import NEXT_STEP_BY_STAGE

        assert "guest" not in NEXT_STEP_BY_STAGE
        assert set(NEXT_STEP_BY_STAGE) == set(STAGE_CODES)


class TestTheAttenderStageIsGone:
    """The second stage retired for the same reason as the first.

    Guest sat between Visitor and Attender and nobody used it. Attender sat
    between Visitor and Member and did the same: "here most Sundays" is
    something a church knows about somebody months after it is true, so the
    column stayed at zero while the people it described sat in Visitor.

    Written as the twin of the class above on purpose. This has now happened
    twice, so the next time somebody retires a stage there is a shape to copy
    and a list of the four places that have to be checked.
    """

    def test_it_is_not_a_stage(self):
        assert "attender" not in STAGE_CODES
        assert STAGE_BY_CODE.get("attender") is None

    def test_the_order_still_has_no_hole_in_it(self):
        """`next_stage` indexes STAGES by order + 1, so a gap left where
        Attender used to be would hand back the wrong stage rather than
        failing."""
        assert [s.order for s in STAGES] == list(range(len(STAGES)))

    def test_the_rail_closed_up_behind_it(self):
        from app.stages import next_stage

        assert next_stage("visitor").code == "member"

    def test_the_database_refuses_it(self, db):
        import sqlalchemy

        church = journey(db)
        db.session.add(Person(church_id=church.id, first_name="Old",
                              last_name="Attender", stage="attender"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_nothing_recommends_a_next_step_for_it(self):
        from app.stages import NEXT_STEP_BY_STAGE

        assert "attender" not in NEXT_STEP_BY_STAGE
        assert set(NEXT_STEP_BY_STAGE) == set(STAGE_CODES)

    def test_no_sequence_points_at_it(self):
        """The failure mode that is invisible rather than loud.

        A sequence whose trigger stage does not exist never raises. It simply
        never fires, while still appearing on the dashboard's automation list
        as something the church has running.
        """
        from app.sequences import SEQUENCES

        for sequence in SEQUENCES:
            assert sequence.trigger_stage in STAGE_CODES, sequence.code
            assert sequence.target_stage in STAGE_CODES, sequence.code

    def test_the_welcome_series_kept_its_closing_note(self):
        """The half of the retired follow-up that did not depend on knowing
        somebody attends regularly moved here rather than being dropped."""
        from app.sequences import WELCOME

        assert WELCOME.steps[-1].day == 21
        assert "last note like this" in WELCOME.steps[-1].body

    def test_only_visitor_is_transitional_now(self):
        from app.stages import TRANSITIONAL_STAGES

        assert [s.code for s in TRANSITIONAL_STAGES] == ["visitor"]


class TestChildrenAreNotInTheStageCounts:
    def test_the_adults_are_counted_and_the_children_are_not(self, db, family):
        counts = Person.stage_counts(journey(db).id)
        assert counts["member"] == 2

    def test_the_children_are_counted_on_their_own(self, db, family):
        assert Person.child_count(journey(db).id) == 3

    def test_the_total_is_still_everybody(self, db, family):
        """Adults by stage plus kids adds up to the number in the heading."""
        church = journey(db)
        total = Person.total_for_church(church.id)
        counted = sum(Person.stage_counts(church.id).values()) + Person.child_count(church.id)
        assert counted == total == 5

    def test_an_archived_child_falls_out_of_both(self, db, family):
        child = next(p for p in family if p.is_child)
        child.is_archived = True
        db.session.commit()
        church = journey(db)
        assert Person.child_count(church.id) == 2
        assert Person.total_for_church(church.id) == 4

    def test_a_church_with_no_children(self, db):
        assert Person.child_count(journey(db).id) == 0


class TestTheRail:
    def test_the_dashboard_carries_the_kids_number(self, db, family):
        assert dashboard.build(journey(db))["kids_count"] == 3

    def test_kids_are_the_last_segment(self, db, family, staff):
        page = staff.get("/", headers=H).data.decode()
        rail = page[page.index('class="railrow"'):page.index("railfoot")]
        assert rail.rindex("Kids") > rail.rindex("Leader")
        assert "Guest" not in rail

    def test_the_segment_links_to_the_children(self, db, family, staff):
        page = staff.get("/", headers=H).data
        assert b'href="/people/?stage=kids"' in page

    def test_the_people_rail_carries_it_too(self, db, family, staff):
        page = staff.get("/people/", headers=H).data.decode()
        start = page.index('class="stages"')
        stages = page[start:page.index("</section>", start)]
        assert "Kids" in stages
        assert "Guest" not in stages

    def test_a_church_with_no_children_still_renders_the_segment(self, db, staff):
        """Zero is the number staff need to see when they are wondering
        whether anybody has added their kids yet."""
        page = staff.get("/", headers=H).data.decode()
        rail = page[page.index('class="railrow"'):page.index("railfoot")]
        assert "Kids" in rail


class TestFilteringToTheChildren:
    def test_the_filter_shows_only_children(self, db, family, staff):
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        table = page[page.index("<tbody>"):page.index("</tbody>")]
        for name in ("Sofia", "Mateo", "Lucia"):
            assert name in table
        assert "Alicia" not in table
        assert "Marco" not in table

    def test_a_stage_filter_leaves_the_children_out(self, db, family, staff):
        """Their stage is their family's, so a list of Members that includes
        a five year old is not a list of members."""
        page = staff.get("/people/?stage=member", headers=H).data.decode()
        table = page[page.index("<tbody>"):page.index("</tbody>")]
        assert "Alicia" in table
        assert "Sofia" not in table

    def test_no_filter_still_shows_everybody(self, db, family, staff):
        page = staff.get("/people/", headers=H).data.decode()
        table = page[page.index("<tbody>"):page.index("</tbody>")]
        assert "Alicia" in table and "Sofia" in table

    def test_the_child_shows_their_own_name_and_their_household(self, db, family, staff):
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        row = page[page.index("Sofia"):]
        row = row[:row.index("</tr>")]
        assert "The Romero family" in row

    def test_a_child_is_labelled_a_child_not_a_member(self, db, family, staff):
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        row = page[page.index("Sofia"):]
        row = row[:row.index("</tr>")]
        assert "Child" in row
        assert "Member" not in row

    def test_an_adult_still_shows_their_stage(self, db, family, staff):
        page = staff.get("/people/?stage=member", headers=H).data.decode()
        row = page[page.index("Alicia"):]
        row = row[:row.index("</tr>")]
        assert "Member" in row

    def test_the_filter_is_in_the_dropdown(self, db, family, staff):
        page = staff.get("/people/", headers=H).data.decode()
        # The filter above the table, not the stage picker in "Add someone".
        select = page[page.index('name="stage" aria-label="Stage"'):]
        select = select[:select.index("</select>")]
        assert f'value="{KIDS}"' in select
        assert "Guest" not in select

    def test_the_dropdown_remembers_it(self, db, family, staff):
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        assert 'value="kids" selected' in page

    def test_searching_inside_the_children(self, db, family, staff):
        page = staff.get("/people/?stage=kids&q=Sofia", headers=H).data.decode()
        table = page[page.index("<tbody>"):page.index("</tbody>")]
        assert "Sofia" in table
        assert "Mateo" not in table

    @pytest.mark.parametrize("bad", ["guest", "child", "<script>", "kids2"])
    def test_a_stage_that_does_not_exist_is_still_refused(self, db, staff, bad):
        assert staff.get(f"/people/?stage={bad}", headers=H).status_code == 404


class TestChildrenAreStillNotFollowUp:
    def test_a_child_never_shows_as_stuck(self, db, family):
        """Already true, and the reason the rail can leave them out without
        losing anybody: nothing else counts them either."""
        from datetime import timedelta

        child = next(p for p in family if p.is_child)
        child.stage = "visitor"
        child.stage_since = utcnow() - timedelta(days=200)
        db.session.commit()
        assert child.is_stuck is False


class TestWhatTheLastColumnSaysAboutAChild:
    """Time in stage is a number about a stage a child is not really on."""

    def test_a_child_with_a_birthday_shows_their_age(self, db, family, staff):
        from datetime import date

        child = next(p for p in family if p.first_name == "Sofia")
        today = date.today()
        child.birthdate = date(today.year - 6, today.month, today.day)
        db.session.commit()

        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        row = page[page.index("Sofia"):]
        row = row[:row.index("</tr>")]
        assert "6 yrs" in row

    def test_a_birthday_that_has_not_come_round_yet_this_year(self, db, family, staff):
        from datetime import date, timedelta

        child = next(p for p in family if p.first_name == "Sofia")
        tomorrow = date.today() + timedelta(days=1)
        child.birthdate = date(tomorrow.year - 7, tomorrow.month, tomorrow.day)
        db.session.commit()
        assert child.age == 6

    def test_a_child_with_no_birthday_says_so(self, db, family, staff):
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        row = page[page.index("Sofia"):]
        row = row[:row.index("</tr>")]
        assert "No birthday on file" in row

    def test_one_year_old_is_not_1_yrs(self, db, family, staff):
        from datetime import date

        child = next(p for p in family if p.first_name == "Sofia")
        today = date.today()
        child.birthdate = date(today.year - 1, today.month, today.day)
        db.session.commit()
        page = staff.get("/people/?stage=kids", headers=H).data.decode()
        row = page[page.index("Sofia"):]
        row = row[:row.index("</tr>")]
        assert "1 yr" in row and "1 yrs" not in row

    def test_an_adult_still_shows_time_in_stage(self, db, family, staff):
        page = staff.get("/people/?stage=member", headers=H).data.decode()
        row = page[page.index("Alicia"):]
        row = row[:row.index("</tr>")]
        assert "Today" in row
