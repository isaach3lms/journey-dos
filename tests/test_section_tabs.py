"""The pages inside a section, and the sidebar that names them.

**What this replaces.** People carried a row of five outline buttons: connect
cards, account requests, sign-ups, prayer check-ins, download report. Four of
those are places and one is an action, and all five looked identical. The
result was that the section's own contents were invisible: you could not tell
what People contained without opening each page, and once on one of them
nothing said the other three existed, so each was a dead end you reversed out
of with a Back to people button.

**And the sidebar.** It opened as unlabelled icons, by a default whose own
comment said the destinations are "legible as icons once you have used them
twice". That describes whoever built it. A volunteer opening this monthly
never reaches the second time, and a church buys this product to hand to
people who do not work in it daily.

Neither of these was a visual problem. Both were navigation problems wearing
visual clothes, which is why the fix is a tab strip and a default rather than
a restyle.
"""

import pytest

from app.content import PEOPLE
from app.sections import PEOPLE_TABS, people_tabs, tabs_for
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

# Every page in the section, with the endpoint that should be marked current.
PAGES = (
    ("/people/", "people.index"),
    ("/people/guests/", "people.guests"),
    ("/people/requests/", "people.requests"),
    ("/people/signups/", "people.signups"),
    ("/people/prayer/", "people.prayer_list"),
)


class TestTheSidebarOpensWithLabels:
    def test_only_an_explicit_choice_collapses_it(self, staff):
        """The default changed, and both explicit answers still stand. The
        toggle writes "1" for icons and "0" for labels, so anybody who has
        chosen keeps their choice and only people who never had an opinion
        see the change."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)

        assert 'localStorage.getItem("nav-collapsed") === "1"' in page

    def test_the_old_default_is_gone(self, staff):
        """`!== "0"` was the old test, and it collapsed everybody who had
        never touched the toggle."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)

        assert '!== "0"' not in page

    def test_storage_being_blocked_gives_labels(self, staff):
        """The fallback should be the readable state rather than the one
        that happens to be shorter to write. A browser with storage blocked
        used to get icons, which is the opposite of the default."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)
        catch = page.split("} catch (e) {")[1].split("}")[0]

        assert "nav-collapsed" not in catch


class TestTheSectionStripIsEverywhere:
    @pytest.mark.parametrize("path,endpoint", PAGES)
    def test_every_page_in_the_section_carries_it(self, staff, path,
                                                  endpoint):
        """The half that fixes the dead ends. A strip on the roster alone is
        not navigation, it is a menu that exists on one page."""
        page = staff.get(path, headers=H).get_data(as_text=True)

        assert 'class="sectabs"' in page

    @pytest.mark.parametrize("path,endpoint", PAGES)
    def test_the_page_you_are_on_is_marked(self, staff, path,
                                           endpoint):
        """Both ways. The class draws it and `aria-current` says it, because
        the highlight is the entire point of the strip and a reader that
        cannot tell which tab is current is back to the dead ends."""
        page = staff.get(path, headers=H).get_data(as_text=True)
        strip = page.split('class="sectabs"')[1].split("</nav>")[0]

        assert strip.count('aria-current="page"') == 1
        assert strip.count("sectab on") == 1

    def test_every_page_in_the_section_is_reachable_from_every_other(
            self, staff):
        """The property that matters, rather than the markup that provides
        it. Each of the five has to link to the other four."""
        hrefs = {p for p, _ in PAGES}

        for path, _ in PAGES:
            page = staff.get(path, headers=H).get_data(as_text=True)
            strip = page.split('class="sectabs"')[1].split("</nav>")[0]

            for target in hrefs:
                assert f'href="{target}"' in strip, f"{path} cannot reach {target}"


class TestTheButtonRowIsGone:
    def test_the_old_row_of_outline_buttons_is_not_rendered(self, staff):
        """They were destinations dressed as actions, which is what made the
        section's contents invisible."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)

        assert "View connect cards" not in page

    def test_the_report_moved_to_the_top_line(self, staff):
        """The one thing in that row that really was an action. It keeps the
        stage filter it always followed."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)
        topline = page.split('class="toplineactions"')[1].split("</div>")[0]

        assert "report.pdf" in topline

    @pytest.mark.parametrize("path", ("/people/guests/", "/people/requests/",
                                      "/people/signups/"))
    def test_the_back_buttons_are_gone(self, staff, path):
        """The Roster tab is that link. Two controls for one destination
        sitting inches apart is the clutter this set out to remove."""
        page = staff.get(path, headers=H).get_data(as_text=True)

        assert "Back to people" not in page


class TestTheCounts:
    def test_a_waiting_card_shows_its_number(self, db, staff):
        """The counts were on the buttons for a reason written down at the
        time: a page you must open to learn whether it is empty is a page
        that gets opened on Wednesdays."""
        from app.models import Church, GuestCard

        church = db.session.query(Church).filter_by(slug="journey").one()
        db.session.add(GuestCard(church_id=church.id,
                                 first_name="Ana", last_name="Reyes",
                                 email="ana@example.com"))
        db.session.commit()

        page = staff.get("/people/", headers=H).get_data(as_text=True)
        strip = page.split('class="sectabs"')[1].split("</nav>")[0]

        assert 'class="sectabn">1<' in strip

    def test_an_empty_inbox_shows_no_badge(self, staff):
        """Zero is the normal state, and a row of noughts reads as broken
        rather than as finished.

        Suppression happens at two layers: `sections.py` turns 0 into None and
        the partial renders the badge only on a truthy count. Breaking either
        one alone is survivable by design, so the mutation that proves this
        test is both at once, which it catches. The single-layer mutations are
        equivalent rather than uncaught."""
        page = staff.get("/people/", headers=H).get_data(as_text=True)
        strip = page.split('class="sectabs"')[1].split("</nav>")[0]

        assert "sectabn" not in strip

    def test_the_count_follows_you_to_the_other_pages(self, db, staff):
        """A badge that is right on the roster and stale everywhere else is
        worse than no badge: somebody trusts it and stops checking."""
        from app.models import Church, GuestCard

        church = db.session.query(Church).filter_by(slug="journey").one()
        db.session.add(GuestCard(church_id=church.id,
                                 first_name="Ana", last_name="Reyes",
                                 email="ana@example.com"))
        db.session.commit()

        page = staff.get("/people/signups/", headers=H).get_data(as_text=True)
        strip = page.split('class="sectabs"')[1].split("</nav>")[0]

        assert 'class="sectabn">1<' in strip


class TestOnlyTheStripPagesPayForIt:
    """The context processor runs on every template render in the blueprint,
    and most of them show no tabs. A person's detail page is the most opened
    screen in the section, so four counts it throws away is four for nothing
    on the busiest page here."""

    def test_a_page_without_tabs_runs_no_counts(self, app, db):
        from app.blueprints.people import _section_tabs
        from app.models import Church

        church = db.session.query(Church).filter_by(slug="journey").one()

        with app.test_request_context("/people/1/", headers=H):
            from flask import g

            g.church = church
            assert _section_tabs() == {}

    def test_a_page_with_tabs_still_gets_them(self, app, db):
        from app.blueprints.people import _section_tabs
        from app.models import Church

        church = db.session.query(Church).filter_by(slug="journey").one()

        with app.test_request_context("/people/guests/", headers=H):
            from flask import g

            g.church = church
            assert _section_tabs()["tabs"]


class TestBuildingTheStrip:
    def test_a_tab_whose_page_has_moved_is_dropped_not_raised_on(self, app):
        """Navigation furniture. Taking a whole screen down because one
        destination moved is a worse failure than showing four tabs where
        there were five."""
        with app.test_request_context(headers=H):
            spec = (("people.index", "tab_roster", None),
                    ("people.does_not_exist", "tab_roster", None))

            tabs = tabs_for(spec, PEOPLE, "people.index")

        assert len(tabs) == 1

    def test_current_is_matched_on_the_endpoint(self, app):
        """Not on a position. A section gains and loses pages, and a
        positional answer silently marks the wrong one when it does."""
        with app.test_request_context(headers=H):
            tabs = people_tabs(PEOPLE, "people.signups")

        current = [t for t in tabs if t["current"]]
        assert len(current) == 1
        assert current[0]["label"] == PEOPLE["signups_view"]

    def test_nothing_is_current_on_a_page_outside_the_section(self, app):
        """A person detail page is in People but is not one of the tabs.
        Marking one anyway would say they are somewhere they are not."""
        with app.test_request_context(headers=H):
            tabs = people_tabs(PEOPLE, "people.detail")

        assert not any(t["current"] for t in tabs)

    def test_the_labels_are_nouns_rather_than_instructions(self, app):
        """"View connect cards" is a button label. A tab is a place you are
        standing, and a verb reads as an instruction among four nouns."""
        with app.test_request_context(headers=H):
            labels = [t["label"] for t in people_tabs(PEOPLE, "people.index")]

        assert "Connect cards" in labels
        assert not any(label.startswith("View ") for label in labels)

    def test_every_tab_in_the_spec_names_real_copy(self):
        """A missing key would render its own name on screen."""
        for _, key, _ in PEOPLE_TABS:
            assert key in PEOPLE, key
