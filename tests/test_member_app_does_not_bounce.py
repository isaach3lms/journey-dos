"""The member app's frame does not move, and nothing shows behind it.

Reported from a phone: flicking the screen in the installed app showed a band
of dark green above and below the app, and the tab bar lifted off the bottom
of the screen while it happened.

Two symptoms, one cause. Both halves of the frame were held in place with
`position:sticky`, which pins a thing to an edge of the DOCUMENT. On iOS a
flick past the end of a list rubber-bands the document itself, so a sticky
element travels with it, and the gap it leaves shows whatever is painted
behind the page. Pinning harder was never going to fix it, because the thing
moving was not the tab bar.

So the document stopped being the scroller. In the app at phone width the body
is exactly the height of the screen with `overflow:hidden`, and `.mbody`
scrolls inside it. The header and the tab bar are then outside the scrolling
box: not pinned, not sticky, just not in the part that moves. There is nothing
left to rubber-band, which is also why no background can be revealed.

The green itself was a second leftover. `theme-color` is what iOS paints its
own chrome with, and it was the dark green for every screen in the system,
which was right when the member app opened onto a green logo band at the top.
That band was removed and the green status bar above a white page was what
remained.

**What these assertions can and cannot prove.** They are CSS and markup facts:
that the page has no scrolling to do and the tab bar is not sticky any more.
They cannot prove iOS does not bounce, because that is a compositor behaviour
no test here can run. The behaviour was verified by driving every member tab
in a browser at phone size and confirming that the page reports nothing to
scroll, the header sits at the top of the viewport and the tab bar's bottom
edge is exactly the bottom of the viewport, before and after scrolling the
list to its end. Reverting this CSS makes that check fail on the two longest
screens, which is how it is known to be checking the right thing.
"""

from pathlib import Path

import pytest

from app.brand import palette_for
from app.models import Church, Person, User
from app.models.base import utcnow
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}
CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text()


def phone_rules() -> str:
    """Just the block that applies at phone width.

    Scoped deliberately. Asserting these strings against the whole stylesheet
    would pass on a rule that lived in the desktop block, where it would lock
    a computer's browser window to the viewport.
    """
    start = CSS.index("@media (max-width:700px){")
    return CSS[start:CSS.index("\n}", start)]


class TestTheScreenStopsScrolling:
    def test_the_app_is_exactly_the_height_of_the_screen(self):
        block = phone_rules()
        assert "body.memberfull{" in block
        rule = block[block.index("body.memberfull{"):]
        rule = rule[:rule.index("}")]
        assert "overflow:hidden" in rule
        assert "height:100dvh" in rule

    def test_it_measures_the_screen_people_can_see(self):
        """`vh` in mobile Safari is the viewport with the toolbars hidden,
        which is taller than what is on screen, so the tab bar would sit
        underneath the toolbar. `dvh` is what is actually visible.

        The `vh` line is kept in front of it for anything too old to know
        `dvh`, where a tab bar slightly low beats an app of zero height."""
        rule = phone_rules()
        rule = rule[rule.index("body.memberfull{"):]
        rule = rule[:rule.index("}")]
        assert rule.index("height:100vh") < rule.index("height:100dvh")

    def test_a_flick_at_the_end_goes_nowhere(self):
        """The property that stops the bounce on a screen with nothing to
        scroll, which is most of them. Without it iOS rubber-bands a page
        that fits, and the band behind it is what somebody sees."""
        rule = phone_rules()
        rule = rule[rule.index("body.memberfull{"):]
        rule = rule[:rule.index("}")]
        assert "overscroll-behavior:none" in rule


class TestOnlyTheListScrolls:
    def test_the_content_area_is_the_scroller(self):
        block = phone_rules()
        rule = block[block.index(".memberfull .mbody{"):]
        rule = rule[:rule.index("}")]
        assert "overflow-y:auto" in rule

    def test_it_can_shrink_to_fit(self):
        """A flex child will not go below its own content height without
        this, so the list would push the tab bar off the bottom of the
        screen instead of scrolling."""
        rule = phone_rules()
        rule = rule[rule.index(".memberfull .mbody{"):]
        rule = rule[:rule.index("}")]
        assert "min-height:0" in rule

    def test_scrolling_past_the_end_stays_in_the_list(self):
        """Without `contain`, a flick that runs out of list hands the rest of
        the gesture to the page behind it, which is the bounce again one
        level up."""
        rule = phone_rules()
        rule = rule[rule.index(".memberfull .mbody{"):]
        rule = rule[:rule.index("}")]
        assert "overscroll-behavior:contain" in rule


class TestTheTabBarIsNotHeldThere:
    def test_it_is_not_sticky_in_the_app_any_more(self):
        """The assertion that would catch somebody putting it back.

        Sticky was the bug, not the fix: it pins to the bottom of the
        document, and the document is what moved. The tab bar is the last
        child of a full-height column now, so it is at the bottom of the
        screen by construction."""
        assert ".memberfull .mtabs{position:static}" in phone_rules()

    def test_the_staff_preview_keeps_the_old_behaviour(self):
        """The preview draws a phone frame under a bar saying whose app it
        is. Locking that page to the viewport would push the bar's height of
        app off the bottom with no way to reach it, so the lock names
        `.memberfull` and the preview is left alone."""
        block = phone_rules()
        assert ".mtabs{position:sticky;bottom:0}" in block
        for selector in ("body.memberfull{", ".memberfull .mbody{",
                         ".memberfull .mtabs{"):
            assert selector in block, selector
        # Nothing in the lock may be written against the body class the
        # preview also carries.
        assert "body.memberbody{height" not in CSS


class TestNothingGreenIsLeftBehindIt:
    def test_the_page_behind_the_app_is_the_colour_of_the_app(self):
        """Belt and braces with the lock above. If anything ever does show
        through, at a rounded corner or mid-navigation, it is white rather
        than the bone the preview's desk is painted in."""
        assert ".memberfull{background:var(--white)}" in CSS

    def test_the_status_bar_matches_the_screen_under_it(self, client, sign_in, db):
        """`theme-color` is what iOS paints its own chrome with. It was the
        dark green on every screen in the system, which was right while the
        member app opened onto a green logo band. The band is gone and this
        is what was left of it."""
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        user = db.session.scalar(db.select(User).where(
            User.church_id == church.id,
            User.email == "member@journeychurchsemo.com"))
        person = Person(church_id=church.id, first_name="Isaac", last_name="Helms",
                        email=user.email, stage="member", approved_at=utcnow())
        db.session.add(person)
        db.session.flush()
        user.person_id = person.id
        db.session.commit()

        sign_in("member@journeychurchsemo.com")
        body = client.get("/me/", headers=H).get_data(as_text=True)
        white = palette_for(church).surface
        assert f'name="theme-color" content="{white}"' in body

    def test_the_staff_app_keeps_the_green_one(self, client, sign_in, db):
        """The other half, and the reason this is per shell rather than a
        change to the token. Staff open onto a dark green sidebar, so a green
        status bar is right there and always was."""
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        sign_in("pastor@journeychurchsemo.com")
        body = client.get("/", headers=H).get_data(as_text=True)
        green = palette_for(church).deep
        assert f'name="theme-color" content="{green}"' in body

    def test_a_shell_that_says_nothing_still_gets_the_green(self, client):
        """Every other screen in the system shares one head partial. Making
        the colour settable must not quietly blank it on the screens that
        never set it, so the default is what they all had."""
        body = client.get("/auth/login", headers=H).get_data(as_text=True)
        assert 'name="theme-color" content="#' in body
