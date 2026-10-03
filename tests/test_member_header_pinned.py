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
        """Taller than the header, or the row lands underneath it anyway."""
        import re

        match = re.search(r"\.mbody \[id\]\{scroll-margin-top:calc\((\d+)px", CSS)
        assert match, "scroll-margin-top is no longer a calc in px"
        # Header is a 42px logo plus 16px of padding top and bottom.
        assert int(match.group(1)) >= 74


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
