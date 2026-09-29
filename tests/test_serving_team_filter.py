"""Filtering the serving picker to one team.

A church with forty people on the roster should not scroll past all forty to
find a drummer. The filter narrows both lists on the card: who to ask, and
what to ask them to do.
"""

import pytest

from app.models import (
    Church, Person, Service, Team, TeamMembership, TeamPosition,
)
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def staff(client, sign_in):
    sign_in("pastor@journeychurchsemo.com")
    return client


@pytest.fixture
def rota(db):
    """Two teams, a person on each, one on neither, and a child."""
    from datetime import timedelta

    church = journey(db)
    band = Team(church_id=church.id, name="Band")
    kids = Team(church_id=church.id, name="Kids")
    db.session.add_all([band, kids])
    db.session.flush()
    db.session.add_all([
        TeamPosition(church_id=church.id, team_id=band.id, name="Acoustic"),
        TeamPosition(church_id=church.id, team_id=band.id, name="Electric"),
        TeamPosition(church_id=church.id, team_id=kids.id, name="Check-in desk"),
    ])

    people = {}
    for first, team in (("Kaela", band), ("Christopher", band), ("Ruth", kids),
                        ("Nobody", None)):
        person = Person(church_id=church.id, first_name=first, last_name="Menz",
                        stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        if team is not None:
            db.session.add(TeamMembership(church_id=church.id, team_id=team.id,
                                          person_id=person.id))
        people[first] = person

    child = Person(church_id=church.id, first_name="Ella", last_name="Menz",
                   is_child=True, stage="member")
    db.session.add(child)

    service = Service(church_id=church.id, name="Sunday",
                      starts_at=utcnow() + timedelta(days=4))
    db.session.add(service)
    db.session.commit()
    return service, band, kids, people


def card(staff, service):
    page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
    start = page.index("Who is serving")
    return page[start:page.index("</section>", start)]


def picker(staff, service):
    """Just the person dropdown, which is where the option markup lives."""
    block = card(staff, service)
    start = block.index("data-people")
    return block[start:block.index("</select>", start)]


def option_for(staff, service, name):
    text = picker(staff, service)
    return text[text.rindex("<option", 0, text.index(name)):text.index(name)]


class TestTheFilterIsThere:
    def test_a_team_dropdown_sits_above_the_picker(self, db, staff, rota):
        service, band, kids, _ = rota
        block = card(staff, service)
        assert "data-team-filter" in block
        assert "Everyone" in block
        assert "Band" in block and "Kids" in block

    def test_it_is_not_part_of_what_gets_posted(self, db, staff, rota):
        """It narrows the lists. It is never sent anywhere."""
        service, _, _, _ = rota
        block = card(staff, service)
        picker = block[block.index("data-team-filter"):]
        picker = picker[:picker.index("</select>")]
        assert "name=" not in picker

    def test_no_teams_means_no_filter(self, db, staff):
        from datetime import timedelta

        service = Service(church_id=journey(db).id, name="Sunday",
                          starts_at=utcnow() + timedelta(days=4))
        db.session.add(service)
        db.session.commit()
        assert "data-team-filter" not in card(staff, service)


class TestWhatEachOptionCarries:
    def test_a_person_carries_their_teams(self, db, staff, rota):
        service, band, kids, people = rota
        assert f'data-teams="{band.id}"' in option_for(staff, service, "Kaela")

    def test_somebody_on_two_teams_carries_both(self, db, staff, rota):
        service, band, kids, people = rota
        db.session.add(TeamMembership(church_id=people["Kaela"].church_id,
                                      team_id=kids.id, person_id=people["Kaela"].id))
        db.session.commit()

        kaela = option_for(staff, service, "Kaela")
        assert str(band.id) in kaela and str(kids.id) in kaela

    def test_somebody_on_no_team_carries_none(self, db, staff, rota):
        """They stay in the full list and drop out of a filtered one."""
        service, _, _, _ = rota
        assert 'data-teams=""' in option_for(staff, service, "Nobody Menz")

    def test_a_position_carries_its_team(self, db, staff, rota):
        service, band, _, _ = rota
        block = card(staff, service)
        acoustic = block[block.rindex("<option", 0, block.index("Acoustic")):block.index("Acoustic")]
        assert f'data-teams="{band.id}"' in acoustic

    def test_the_blank_serving_row_belongs_to_no_team(self, db, staff, rota):
        """A placeholder has to survive every filter, or picking a team
        leaves no way to ask somebody without naming a position."""
        service, _, _, _ = rota
        block = card(staff, service)
        assert '<option value="">Serving</option>' in block


class TestWhoIsInThePickerAtAll:
    def test_children_are_not(self, db, staff, rota):
        """A five year old is not on a serving rota."""
        service, _, _, _ = rota
        block = card(staff, service)
        picker = block[block.index("data-people"):]
        picker = picker[:picker.index("</select>")]
        assert "Ella" not in picker

    def test_adults_are(self, db, staff, rota):
        service, _, _, _ = rota
        picker = card(staff, service)
        for name in ("Kaela", "Christopher", "Ruth", "Nobody"):
            assert name in picker

    def test_an_archived_person_is_not(self, db, staff, rota):
        service, _, _, people = rota
        people["Ruth"].is_archived = True
        db.session.commit()
        block = card(staff, service)
        picker = block[block.index("data-people"):]
        picker = picker[:picker.index("</select>")]
        assert "Ruth" not in picker


class TestTheScriptThatDoesIt:
    from pathlib import Path as _Path

    TEMPLATE = (_Path(__file__).resolve().parent.parent / "app" / "templates"
                / "services" / "plan.html").read_text()

    def test_options_are_rebuilt_rather_than_hidden(self):
        """Safari ignores the hidden attribute on an option, which would
        leave a filtered list that still scrolls past everybody."""
        assert "createElement(\"option\")" in self.TEMPLATE
        assert "innerHTML = \"\"" in self.TEMPLATE

    def test_choosing_everyone_puts_them_all_back(self):
        assert "snapshot" in self.TEMPLATE

    def test_an_empty_team_says_so(self, db, staff, rota):
        """The copy is handed to the script rather than written into it."""
        service, _, _, _ = rota
        page = staff.get(f"/services/{service.id}/", headers=H).data.decode()
        assert "Nobody is on this team yet" in page

    def test_it_only_runs_when_the_filter_exists(self):
        assert 'if (!filter) { return; }' in self.TEMPLATE

    def test_only_the_blank_row_survives_a_filter(self):
        """Somebody on no team is not on this team either, so they drop out
        until the filter goes back to Everyone. The blank "Serving" row is a
        placeholder and stays, or picking a team would leave no way to ask
        somebody without also naming a position."""
        assert 'if (option.value === "") { return true; }' in self.TEMPLATE


class TestAssigningStillWorks:
    def test_a_person_can_still_be_asked(self, db, staff, rota):
        """The filter is a convenience in front of a form that did not
        change."""
        service, band, _, people = rota
        position = db.session.scalar(
            db.select(TeamPosition).where(TeamPosition.name == "Acoustic"))
        staff.post(f"/services/{service.id}/assignments/",
                   data={"person_id": people["Kaela"].id, "position_id": position.id},
                   headers=H, follow_redirects=True)

        db.session.refresh(service)
        assert [a.person.first_name for a in service.assignments] == ["Kaela"]
        assert service.assignments[0].position_name == "Acoustic"

    def test_asking_somebody_with_no_position(self, db, staff, rota):
        service, _, _, people = rota
        staff.post(f"/services/{service.id}/assignments/",
                   data={"person_id": people["Nobody"].id, "position_id": ""},
                   headers=H, follow_redirects=True)
        db.session.refresh(service)
        assert len(service.assignments) == 1
