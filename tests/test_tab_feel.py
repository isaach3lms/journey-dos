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
    def test_the_header_has_no_image_left_to_wait_for(self):
        """This used to pin `height:42px` on the header logo, because
        `height:auto` is zero until a file decodes, so the header grew a
        moment after every page load and pushed the whole screen down.

        The logo is gone from the member header; the avatar in its place is
        two letters in a fixed box, so there is nothing to decode and the
        jump cannot come back by that route. What is worth guarding now is
        that the box is fixed and that no image has crept back in."""
        rule = CSS[CSS.index("\n.mavatar{"):]
        rule = rule[:rule.index("}")]
        assert "width:36px" in rule
        assert "height:36px" in rule
        assert "height:auto" not in rule

        header = CSS[CSS.index("\n.mhead{"):]
        header = header[:header.index("}")]
        assert "background-image" not in header

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


class TestTheComposerSitsOnTheTabBar:
    """Where the message box is on a phone.

    It was floating in the middle of the thread with the conversation
    running on underneath it. The cause was an offset left behind by an
    earlier fix: when the DOCUMENT was the scroller, the tab bar was
    pinned over the bottom of it and the composer had to clear the tab
    bar's height to stay visible. The member app stopped scrolling the
    document, `.mbody` became the scroller and the tab bar moved outside
    it, and that 66px then measured from a bottom edge that already had
    the tabs below it. The composer floated a tab bar's height up the
    screen.
    """

    def _rule(self, selector):
        start = CSS.index(selector)
        return CSS[start:CSS.index("}", start)]

    def test_in_the_real_app_it_sits_at_the_bottom_of_the_list(self):
        assert ".memberfull .composer{bottom:0}" in CSS

    def test_the_staff_preview_still_clears_the_pinned_tab_bar(self):
        """The preview is not `.memberfull`. There the document still
        scrolls and the tab bar is still pinned over it, so the offset
        that was wrong in the app is right here."""
        rule = self._rule(".composer{bottom:calc(66px")
        assert "66px" in rule
        assert "env(safe-area-inset-bottom" in rule

    def test_the_list_gives_up_its_bottom_padding_for_a_composer(self):
        """A sticky box cannot leave its containing block, so the padding
        under the list would park the composer 24px up with messages
        scrolling through the gap."""
        assert ".memberfull .mbody:has(.composer){--mbody-pad-b:0px}" in CSS

    def test_and_keeps_it_everywhere_else(self):
        """Home, Serve and Grow have no composer and still need room at
        the bottom. Scoping it with `:has` is what keeps them."""
        rule = self._rule(".mbody{")
        assert "--mbody-pad-b:24px" in rule
        assert "var(--mbody-pad-b)" in rule

    def test_the_composer_pads_itself_instead(self):
        assert ".memberfull .mbody:has(.composer) .composer{padding-bottom:10px}" in CSS
