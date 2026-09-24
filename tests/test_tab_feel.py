"""Moving between tabs: no flash, no jump, no waiting for feedback.

Every tab is a real page load. These are the three things that made that
read as broken on a phone, and the guards that keep them fixed.
"""

from pathlib import Path

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text()

# Every tab on the member bar, and the tab each one should land on.
TABS = ["/me/", "/me/serve/", "/me/read/", "/me/chat/", "/me/you/"]


@pytest.fixture
def signed_in(db, client, sign_in):
    user = db.session.scalar(db.select(User).where(User.email == "member@journeychurchsemo.com"))
    person = Person(church_id=user.church_id, first_name="Alicia", last_name="Romero",
                    email=user.email, stage="member", approved_at=utcnow())
    db.session.add(person)
    db.session.flush()
    user.person_id = person.id
    db.session.commit()
    sign_in("member@journeychurchsemo.com")
    return client


class TestNoFlashBetweenPages:
    def test_the_browser_holds_the_old_page_until_the_new_one_paints(self):
        assert "@view-transition{navigation:auto}" in CSS.replace(" ", "")

    def test_the_header_and_tab_bar_are_named_so_they_do_not_move(self):
        assert "view-transition-name:mhead" in CSS
        assert "view-transition-name:mtabs" in CSS

    def test_neither_of_them_animates(self):
        block = CSS[CSS.index("::view-transition-group(mhead)"):]
        assert "animation:none" in block[:200]

    def test_somebody_who_asked_for_less_motion_gets_none(self):
        assert "prefers-reduced-motion:reduce" in CSS
        guard = CSS[CSS.index("::view-transition-group(*)"):]
        assert "animation:none !important" in guard[:220]


class TestNoJumpWhileLoading:
    def test_the_logo_has_a_reserved_box(self):
        """height:auto is zero until the file decodes, which grew the header a
        moment after every page load and pushed the whole screen down."""
        rule = CSS[CSS.index(".mhead .mark{"):]
        rule = rule[:rule.index("}")]
        assert "height:42px" in rule
        assert "object-fit:contain" in rule  # any church's logo, undistorted
        assert "height:auto" not in rule

    def test_the_sign_in_logo_too(self):
        rule = CSS[CSS.index(".authbrand .mark{"):]
        rule = rule[:rule.index("}")]
        assert "height:53px" in rule
        assert "height:auto" not in rule

    def test_the_header_is_on_every_tab(self, db, signed_in):
        """The named elements have to exist on both sides of a navigation, or
        the transition has nothing to hold still."""
        for path in TABS:
            page = signed_in.get(path, headers=H).data
            assert b'class="mhead"' in page, path
            assert b'class="mtabs"' in page, path


class TestInstantFeedback:
    def test_the_tap_moves_the_highlight(self, db, signed_in):
        page = signed_in.get("/me/", headers=H).data.decode()
        assert "tabs.addEventListener('click'" in page
        assert "setAttribute('aria-current', 'page')" in page

    def test_the_giving_tab_never_takes_the_highlight(self, db, signed_in):
        """It opens the church's own page in the browser, so it is not a
        place inside the app."""
        page = signed_in.get("/me/", headers=H).data.decode()
        assert "link.target === '_blank'" in page

    def test_the_grey_ios_tap_box_is_gone(self):
        rule = CSS[CSS.index(".mtabs a{"):]
        rule = rule[:rule.index("}")]
        assert "-webkit-tap-highlight-color:transparent" in rule
        assert ".mtabs a:active{opacity:.55}" in CSS

    def test_the_current_tab_is_still_marked_server_side(self, db, signed_in):
        """The script is a nicety. Without JavaScript the right tab is still
        the one marked when the page arrives."""
        page = signed_in.get("/me/serve/", headers=H).data.decode()
        marked = page[page.index('class="mtabs"'):page.index("</nav>")]
        assert marked.count('aria-current="page"') == 1
        serve = marked[marked.index("/me/serve/"):]
        assert 'aria-current="page"' in serve[:120]
