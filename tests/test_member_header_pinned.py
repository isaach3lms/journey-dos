"""The member header stays on screen.

The tab bar at the bottom was already pinned and the header was not, so on a
phone the church's logo slid off the top the moment anybody scrolled and the
app lost half its own frame. Both halves now behave the same way.

A CSS fact is tested as a CSS fact. These assertions cannot prove the header
renders where it should, only that the rule saying so has not been deleted;
the rendering was verified by driving every member tab in a browser at phone
width and confirming nothing paints over the header. What this file is for is
the next person who reorganises the stylesheet.
"""

from pathlib import Path

import pytest

from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text()


def _rule(selector: str) -> str:
    """The declaration block for a top-level rule, by its exact selector."""
    start = CSS.index(f"\n{selector}{{")
    return CSS[start:CSS.index("}", start)]


class TestTheHeaderIsPinned:
    def test_it_is_sticky_at_the_top(self):
        block = _rule(".mhead")
        assert "position:sticky" in block
        assert "top:0" in block

    def test_it_sits_above_the_content_scrolling_under_it(self):
        """Without a stacking order the content wins and the header is a
        transparent strip with words sliding through it."""
        assert "z-index:6" in _rule(".mhead")

    def test_it_allows_for_the_notch(self):
        """The app runs in an iOS wrapper with viewport-fit=cover. The inset
        is zero where the wrapper already handles it, so this costs nothing
        there and is the whole difference where it does not."""
        block = _rule(".mhead")
        assert "env(safe-area-inset-top" in block
        # With a fallback, so a browser that does not know the function still
        # gets padding rather than none.
        assert "safe-area-inset-top, 0px" in block

    def test_the_tab_bar_is_still_pinned_too(self):
        """The change was to make the two halves of the frame agree, not to
        trade one for the other."""
        assert ".mtabs{position:sticky;bottom:0}" in CSS.replace("\n  ", "")


class TestAnchorsStillClearIt:
    def test_jump_targets_have_a_scroll_margin(self):
        """Saving a detail on the You tab redirects to #details. Without this
        the browser puts that row exactly where the header now is, and the
        person lands on a screen that looks like it did nothing."""
        assert ".mbody [id]{scroll-margin-top:" in CSS

    def test_the_margin_clears_the_header(self):
        """Taller than the header, or the row lands underneath it anyway.

        The figure came down with the logo band: the header is now a 36px
        avatar with 10px above it and 6px below, so 52px. It was 74px when
        the header was a 42px logo in 16px of padding."""
        import re

        match = re.search(r"\.mbody \[id\]\{scroll-margin-top:calc\((\d+)px", CSS)
        assert match, "scroll-margin-top is no longer a calc in px"
        assert int(match.group(1)) >= 52


class TestTheStaffPreviewKeepsTheFrame:
    def test_the_preview_header_does_not_stick(self):
        """The preview draws a phone frame on a computer. That frame is not a
        scroll container, so a sticky header inside it detaches from the top
        of the frame and travels down the page, which reads as a rendering
        fault rather than as a preview of a pinned header."""
        assert ".memberbody:not(.memberfull) .mhead{position:static}" in CSS

    def test_only_above_phone_width(self):
        """On a phone there is no frame to stay inside, so the preview and
        the real thing behave the same."""
        start = CSS.index(".memberbody:not(.memberfull) .mhead")
        before = CSS[:start]
        assert before.rstrip().endswith("@media (min-width:701px){")


class TestItReachesTheRealPages:
    """The rules above are only worth anything if they apply to the element
    the member app actually renders."""

    @pytest.fixture
    def signed_in(self, db, client, sign_in):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(db.select(User).where(
            User.church_id == church.id,
            User.email == "member@journeychurchsemo.com",
        ))
        person = Person(church_id=church.id, first_name="Alicia", last_name="Romero",
                        email=user.email, stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        return client

    @pytest.mark.parametrize("path", ["/me/", "/me/serve/", "/me/read/",
                                      "/me/groups/", "/me/chat/", "/me/you/"])
    def test_every_tab_renders_the_header(self, signed_in, path):
        page = signed_in.get(path, headers=H)
        assert page.status_code == 200
        assert 'class="mhead"' in page.get_data(as_text=True)


class TestTheHeaderIsTheMember:
    """What is in the header, now that it is not a logo.

    The logo and its green band are gone. The header holds one control, the
    member's own avatar, and that avatar is the only route to the profile:
    the You tab was removed in the same change. So these assertions are
    load-bearing in a way the old header's were not. Losing the avatar does
    not cost the app a decoration, it strands the profile with no way in.
    """

    @pytest.fixture
    def signed_in(self, db, client, sign_in):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(db.select(User).where(
            User.church_id == church.id,
            User.email == "member@journeychurchsemo.com",
        ))
        person = Person(church_id=church.id, first_name="Alicia", last_name="Romero",
                        email=user.email, stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        db.session.commit()
        sign_in("member@journeychurchsemo.com")
        return client

    def header(self, client, path="/me/") -> str:
        page = client.get(path, headers=H)
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        return body[body.index('class="mhead"'):body.index("</header>")]

    def tabs(self, client, path="/me/") -> str:
        body = client.get(path, headers=H).get_data(as_text=True)
        return body[body.index('class="mtabs"'):body.index("</nav>")]

    def test_the_avatar_is_there_and_opens_the_profile(self, signed_in):
        head = self.header(signed_in)
        assert 'class="mavatar"' in head
        assert 'href="/me/you/"' in head

    def test_it_carries_the_members_own_initials(self, signed_in):
        """Two letters of their own name rather than an outline of a person.
        A member has to recognise it as themselves before they will believe
        it is where their account lives."""
        assert ">AR<" in self.header(signed_in)

    def test_the_logo_is_gone_from_it(self, signed_in):
        """The band of colour on every screen was the whole point of the
        change, so this is the assertion that would catch somebody putting
        it back by habit."""
        head = self.header(signed_in)
        assert "<img" not in head
        assert "mark" not in head

    def test_the_avatar_is_marked_on_the_profile_itself(self, signed_in):
        """It is a navigation control, so it says where you are, the same as
        a tab does."""
        assert "aria-current" in self.header(signed_in, "/me/you/")
        assert "aria-current" not in self.header(signed_in, "/me/")

    def test_it_is_reachable_without_sight(self, signed_in):
        """The only label is two letters inside a circle, which a screen
        reader would otherwise read out as two letters."""
        assert 'aria-label="You"' in self.header(signed_in)

    def test_there_is_no_you_tab_any_more(self, signed_in):
        """Two doors to one screen, one of them taking a sixth of the tab
        bar. The avatar is the door now."""
        assert "/me/you/" not in self.tabs(signed_in)

    def test_four_tabs_remain(self, signed_in):
        """Home, Serve, Grow, Chat. Giving is a fifth only where the church
        has set a giving page, and it leaves the app, so it is not counted
        here."""
        bar = self.tabs(signed_in)
        for path in ("/me/", "/me/serve/", "/me/read/", "/me/chat/"):
            assert f'href="{path}"' in bar, path
        assert bar.count("<a ") == 4

    @pytest.mark.parametrize("path", ["/me/", "/me/serve/", "/me/read/",
                                      "/me/groups/", "/me/chat/", "/me/you/"])
    def test_a_sign_in_with_no_person_never_reaches_the_avatar(self, signed_in, db, path):
        """The avatar reads `person.initials`, so a page rendered with no
        person at all would be a 500 on the header rather than on the
        content.

        Every member route already turns that case away before it renders,
        and this is the test that says so. The template keeps a fallback to
        the church's initial as well, but a fallback nobody can reach is not
        a thing to rely on: the guard is.

        Written as a real request per tab rather than as an assertion about
        the template, because what matters is that no route forgot it.
        """
        user = db.session.scalar(db.select(User).where(
            User.email == "member@journeychurchsemo.com"))
        user.person_id = None
        db.session.commit()
        page = signed_in.get(path, headers=H)
        assert page.status_code != 500
        assert 'class="mavatar"' not in page.get_data(as_text=True)
