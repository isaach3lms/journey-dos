"""The menu: narrow it on a desktop, pull it out on a phone.

The sidebar used to be a fixed 236px rail that became a sideways-scrolling
row of tabs under 820px. Nine destinations in a strip with room for four is
how Settings became the one nobody found.

Two changes, one button:

- Wide screen: the rail narrows to its icons and remembers the choice.
- Phone: the rail becomes a drawer behind a menu icon, with the church and
  the city left behind in an app bar.

The behaviour itself is JavaScript, so most of what can be checked here is
the markup it acts on and the shape the stylesheet gives it. The part that
matters most, and that these tests do cover, is what happens when the
script does not run: the old tab strip, untouched.
"""

import pathlib
import re

import pytest

from app import content as content_module
from app.content import NAV_UI
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text()
TEMPLATES = ROOT / "app" / "templates"


def page(client, path="/"):
    return client.get(path, headers=H).data.decode()


class TestTheButtonIsThere:
    def test_the_sidebar_has_one(self, db, staff):
        assert "data-nav-toggle" in page(staff)

    def test_it_starts_hidden(self, db, staff):
        """It does nothing without the script, and a button that does
        nothing is worse than no button. The script unhides it.

        The opening tag itself, not a window after it: a loose search finds
        the `hidden` on the scrim further down the page and passes on a
        button that was never hidden at all.
        """
        markup = page(staff)
        start = markup.index('<button class="navtoggle"')
        tag = markup[start:markup.index(">", start)]
        assert "hidden" in tag, tag

    def test_the_app_bar_has_its_own(self, db, staff):
        assert "appbarbtn" in page(staff)

    def test_the_scrim_is_there_and_starts_hidden(self, db, staff):
        markup = page(staff)
        start = markup.index('<button class="navscrim"')
        tag = markup[start:markup.index(">", start)]
        assert "hidden" in tag, tag

    def test_every_destination_carries_its_name_for_the_tooltip(self, db, staff):
        """The narrowed rail shows icons. The label is what the hover
        tooltip reads, and what a screen reader reads off the clipped
        span, so every row needs it."""
        markup = page(staff)
        for label in ("Dashboard", "People", "Settings"):
            assert f'data-label="{label}"' in markup


class TestWithoutJavaScript:
    """The shape the page has before the script runs, and the shape it
    keeps if the script never runs at all. Everything new hangs off a `js`
    class, so this is one assertion per piece of it."""

    def test_the_drawer_needs_the_js_class(self):
        assert ".js .sidebar{" in CSS.replace("\n", "")

    def test_the_app_bar_is_hidden_until_then(self):
        assert ".appbar{display:none}" in CSS

    def test_the_scrim_is_hidden_until_then(self):
        assert ".navscrim{display:none}" in CSS

    def test_the_old_tab_strip_is_still_in_the_stylesheet(self):
        """It is the no-script fallback, not dead code. Deleting it would
        leave a phone with no navigation at all when the script fails."""
        assert ".sidebar .navstrip{" in CSS

    def test_the_head_script_sets_the_class_before_anything_paints(self):
        """In the head rather than at the end of the body: setting it after
        load means every page opens wide and visibly snaps shut."""
        base = (TEMPLATES / "base.html").read_text()
        head = base[:base.index("</head>")]
        assert 'classList.add("js")' in head
        assert "nav-collapsed" in head

    def test_the_remembered_choice_survives_storage_being_blocked(self):
        """Private browsing throws on localStorage rather than returning
        nothing, and an exception in a head script stops the page."""
        base = (TEMPLATES / "base.html").read_text()
        head = base[:base.index("</head>")]
        assert "try" in head and "catch" in head


class TestTheNarrowedRail:
    def test_it_is_narrower(self):
        assert ".nav-collapsed .sidebar{width:68px" in CSS

    def test_the_labels_are_clipped_rather_than_removed(self):
        """`display:none` takes the word away from a screen reader too, so
        the rail would read as nine unlabelled pictures."""
        start = CSS.index(".nav-collapsed .nav .lbl{")
        rule = CSS[start:CSS.index("}", start)]
        assert "clip-path" in rule
        assert "display:none" not in rule

    def test_a_label_still_appears_on_hover(self):
        assert ".nav-collapsed .nav::after" in CSS
        assert "content:attr(data-label)" in CSS

    def test_the_group_headings_go(self):
        assert ".nav-collapsed .navlbl{display:none}" in CSS


class TestItStartsNarrow:
    """Narrow is the default, and widening it is the thing that is
    remembered. The rail was 236px of mostly empty green beside every
    screen; the icons are legible once you have used them twice.
    """

    def test_a_browser_that_has_never_been_told_gets_the_icons(self):
        """The test that matters: no stored answer means narrow. Written
        against the condition rather than the class name, because
        `=== "1"` and `!== "0"` both mention `nav-collapsed` and only one
        of them starts narrow."""
        base = (TEMPLATES / "base.html").read_text()
        head = base[:base.index("</head>")]
        assert 'localStorage.getItem("nav-collapsed") !== "0"' in head

    def test_widening_it_is_what_gets_remembered(self):
        """`"0"` is the stored answer for wide, so somebody who widened it
        before this change keeps it wide rather than being reset."""
        script = (TEMPLATES / "partials" / "shell_nav.html").read_text()
        assert 'now ? "1" : "0"' in script

    def test_storage_being_blocked_still_gives_the_default(self):
        """A private window throws rather than returning nothing, and the
        catch has to land on narrow too. Falling through to wide would
        make the one setting behave backwards in exactly the window
        somebody is least likely to report."""
        base = (TEMPLATES / "base.html").read_text()
        head = base[:base.index("</head>")]
        catch = head[head.index("catch"):]
        assert "nav-collapsed" in catch[:160]

    def test_the_button_starts_in_the_matching_state(self, db, staff):
        """Rendered before the script runs. A button saying "Narrow the
        menu" above an already narrow menu is a wrong label for one
        frame, and a wrong label for a screen reader that read it first."""
        markup = page(staff)
        start = markup.index('<button class="navtoggle"')
        tag = markup[start:markup.index(">", start)]
        assert 'aria-expanded="false"' in tag


class TestTheNotch:
    """iOS hands a phone's screen over whole and expects the page to keep
    its own content out of the status bar and the home indicator.

    This shell did not ask for the whole screen, and had never needed to:
    nothing was pinned to the top of it. The app bar and the drawer are,
    and the result on an iPhone was both of them sitting about sixty
    pixels down with the page scrolling through the gap above.
    """

    VIEWPORT = re.compile(r'name="viewport" content="([^"]+)"')

    def _viewport(self, name):
        return self.VIEWPORT.search((TEMPLATES / name).read_text()).group(1)

    def test_the_staff_shell_takes_the_whole_screen(self):
        assert "viewport-fit=cover" in self._viewport("base.html")

    def test_so_does_the_member_shell(self):
        """It always did. This is the pair that made the difference
        visible: one shell had it and the other did not."""
        assert "viewport-fit=cover" in self._viewport("member/base.html")

    def test_the_app_bar_covers_the_status_bar_strip(self):
        """Its height grows by the inset and its contents are pushed below
        it, so the clock sits on the church's colour rather than on a band
        of page scrolling underneath."""
        start = CSS.index("  .js .appbar{")
        rule = CSS[start:CSS.index("}", start)]
        assert "height:calc(56px + env(safe-area-inset-top" in rule
        assert "padding:env(safe-area-inset-top" in rule

    def test_the_page_starts_below_all_of_it(self):
        start = CSS.index("  .js .main{")
        rule = CSS[start:CSS.index("}", start)]
        assert "56px + env(safe-area-inset-top" in rule

    def test_the_drawer_clears_both_ends(self):
        """The top for the status bar, the bottom for the home indicator,
        which is where Sign out sits."""
        start = CSS.index("  .js .sidebar{")
        rule = CSS[start:CSS.index("}", start)]
        assert "env(safe-area-inset-top" in rule
        assert "env(safe-area-inset-bottom" in rule

    def test_the_no_script_header_clears_it_too(self):
        """Taking the whole screen applies whether the script runs or not,
        so the tab-strip header needs the inset as much as the drawer."""
        start = CSS.index("  .sidebar{\n    width:100%")
        rule = CSS[start:CSS.index("}", start)]
        assert "env(safe-area-inset-top" in rule

    def test_the_last_card_clears_the_home_indicator(self):
        start = CSS.index("  .content{padding:16px 16px")
        rule = CSS[start:CSS.index("}", start)]
        assert "env(safe-area-inset-bottom" in rule

    def test_every_inset_has_a_fallback(self):
        """A browser that does not know `env()` drops the whole
        declaration, which on the app bar's height would collapse it.

        Comments are stripped first. A comment that quotes a declaration
        while explaining it is prose, not CSS, and failing on it trains
        people to stop writing the explanation.
        """
        rules = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        bare = re.findall(r"env\(safe-area-inset-(?:top|bottom|left|right)\)", rules)
        assert not bare, f"{len(bare)} env() calls with no fallback value"


class TestTheHeaderLinesUp:
    def test_the_button_is_centred_on_the_logo(self):
        """The header aligns its rows to the top, so the button needs to
        opt out of that or it sits above the middle of the mark."""
        start = CSS.index("  .navtoggle{grid-column:2")
        rule = CSS[start:CSS.index("}", start)]
        assert "align-self:center" in rule

    def test_the_gap_under_the_logo_belongs_to_the_grid(self):
        """A bottom margin on the mark is part of its grid row, so
        centring the button in that row put it half the margin too low.
        The row gap does the same spacing without joining the row."""
        start = CSS.index("  .church .mark,.church .crest{")
        rule = CSS[start:CSS.index("}", start)]
        assert "margin-bottom:0" in rule


class TestTheDrawer:
    def test_it_sits_off_the_left_edge_until_opened(self):
        rule = CSS[CSS.index(".js .sidebar{"):]
        assert "translateX(-100%)" in rule[:400]

    def test_and_slides_in(self):
        assert ".js.nav-open .sidebar{transform:translateX(0)" in CSS

    def test_it_is_above_the_scrim(self):
        """A drawer under its own scrim is a drawer nobody can tap."""
        drawer = CSS[CSS.index(".js .sidebar{"):]
        scrim = CSS[CSS.index(".js .navscrim{"):]
        drawer_z = int(re.search(r"z-index:(\d+)", drawer[:400]).group(1))
        scrim_z = int(re.search(r"z-index:(\d+)", scrim[:300]).group(1))
        assert drawer_z > scrim_z

    def test_the_group_headings_and_the_footer_come_back(self):
        """The whole point of the drawer over the tab strip: room for the
        headings, and for signing out."""
        assert ".js .sidebar .navlbl," in CSS
        assert ".sidefoot{display:block}" in CSS.replace("\n  ", "")

    def test_the_page_starts_below_the_app_bar(self):
        start = CSS.index("  .js .main{")
        assert "56px" in CSS[start:CSS.index("}", start)]
        assert ".js .appbar{" in CSS


class TestTheChurchMarkIsWrittenOnce:
    def test_both_the_drawer_and_the_app_bar_include_the_same_partial(self):
        """Two copies drifted the moment one of them learned about
        `logo_reversed`."""
        for name in ("partials/sidebar.html", "partials/appbar.html"):
            assert 'include "partials/church_mark.html"' in (TEMPLATES / name).read_text()

    def test_neither_one_builds_the_logo_itself(self):
        for name in ("partials/sidebar.html", "partials/appbar.html"):
            assert "logo_reversed" not in (TEMPLATES / name).read_text()

    def test_a_church_with_no_logo_gets_its_initials(self, db, staff):
        """Both places, from the one partial. The church column is only
        half of it: a palette can carry a default mark, so the church is
        moved onto the palette that does not."""
        from app.models import Church

        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        church.logo_reversed_path = None
        church.palette_key = "between-sundays"
        db.session.commit()

        markup = page(staff)
        # Once in the drawer, once in the app bar.
        assert markup.count('class="crest"') == 2


class TestTheWordsExist:
    """`partials/` is skipped by the content-key check in
    `test_content_keys_exist.py`, because the dictionary a partial renders
    with cannot be guessed from its directory. These two close that gap for
    the shell, which is the one place every screen renders.
    """

    USES = re.compile(r"\bnav_ui\.([a-z_][a-z0-9_]*)\b")

    def test_every_menu_label_a_template_asks_for_exists(self):
        missing = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            for name in sorted(set(self.USES.findall(path.read_text()))):
                if name not in NAV_UI:
                    missing.append(f"{path.name} asks for NAV_UI[{name!r}]")
        assert not missing, (
            "a label the shell renders by name does not exist, so it renders "
            "as nothing at all:\n  " + "\n  ".join(missing)
        )

    def test_none_of_them_is_blank(self):
        assert all(value.strip() for value in NAV_UI.values())

    def test_the_button_says_what_it_will_do(self, db, staff):
        """Not what state it is in. "Widen the menu" on a narrow menu, not
        "The menu is narrow".

        The markup is written for the default, which is narrow, and the
        script relabels it the moment somebody widens it."""
        markup = page(staff)
        assert NAV_UI["expand"] in markup
        assert NAV_UI["open_menu"] in markup


class TestNothingElseMoved:
    def test_the_sidebar_still_lists_every_destination(self, db, staff):
        markup = page(staff)
        for label in ("Dashboard", "People", "Groups", "Services", "Kids",
                      "Giving", "Resources", "Messages", "Settings"):
            assert f">{label}</span>" in markup

    def test_the_current_page_is_still_marked(self, db, staff):
        assert 'aria-current="page"' in page(staff)

    def test_sign_out_is_still_a_post(self, db, staff):
        markup = page(staff)
        assert 'action="/auth/logout"' in markup
        assert "csrf_token" in markup

    def test_a_leader_still_sees_only_their_own_destinations(self, db, leader):
        markup = page(leader)
        assert ">Settings</span>" not in markup
        assert ">People</span>" in markup

    def test_the_app_bar_takes_its_colour_from_the_church(self):
        """Not a hex code. The bar is the most visible surface on a phone,
        and a second church on this platform must not get Journey's green
        across the top of every screen."""
        start = CSS.index(".js .appbar{")
        rule = CSS[start:CSS.index("}", start)]
        assert "background:var(--chrome)" in rule
        # White text on it is not a brand colour, and the sidebar above has
        # always written it literally. The background is the one that has
        # to come from the church.
        assert not re.search(r"background:\s*#", rule)
