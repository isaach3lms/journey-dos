"""Formatted email: three kinds of list, and the first HTML this app sends.

Two things are being tested here and they fail in very different ways.

**The grammar**, which turns what somebody typed into markup. Its failure mode
is a church seeing asterisks in their newsletter, which is embarrassing and
obvious. Its other failure mode is a stored script in three hundred inboxes,
which is neither, so the escaping tests below are written against what an
attacker would actually put in the box rather than against a polite example.

**The HTML half of every email**, which is new. Until this change `body_html`
was a column nothing wrote and a Resend field nothing filled, and every
message this platform sent was plain text. It now has a second half, built in
the one function every email passes through. The tests that matter most are
the ones proving the *first* half did not move: the plain text body is still
the author's literal characters, because that is the guarantee that makes it
safe to switch this on for twenty kinds of message at once rather than two.
"""

from __future__ import annotations

import re

import pytest

from app.content import FORMAT, SETTINGS
from app.email_html import inline_styles, looks_formatted, render_body
from app.mail import MemoryTransport, queue, send_pending
from app.markup import render
from app.models import Church, OutboxMessage, Person
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


@pytest.fixture
def journey(db):
    return db.session.scalar(db.select(Church).where(Church.slug == "journey"))


@pytest.fixture
def person(db, journey):
    p = Person(church_id=journey.id, first_name="Marcus", last_name="Webb",
               email="marcus@example.com", stage="visitor")
    db.session.add(p)
    db.session.commit()
    return p


def html_of(source: str) -> str:
    return str(render(source))


# ---------------------------------------------------------------------------


class TestTheThreeKindsOfList:
    def test_a_bulleted_list(self):
        assert html_of("- Coffee\n- A Bible") == (
            "<ul><li>Coffee</li><li>A Bible</li></ul>"
        )

    def test_a_numbered_list(self):
        assert html_of("1. Arrive\n2. Set up") == (
            "<ol><li>Arrive</li><li>Set up</li></ol>"
        )

    def test_a_dashed_list(self):
        """Its own marker rather than a style setting on a bulleted list.

        A style would have to be stored, carried through saved templates and
        taught to the email renderer, which is a lot of machinery to choose a
        character. The dash is a real character in the item, which is also
        the only way it survives a mail client: `list-style-type` with a
        custom string is ignored by most of them.
        """
        out = html_of("-- Parking is tight\n-- Doors at 8:45")
        assert 'class="dashed"' in out
        assert out.count("<li>–") == 2

    def test_a_numbered_list_keeps_the_number_it_started_on(self):
        """Somebody continuing a list after a paragraph writes "4." and means
        four. Renumbering them to one is the renderer telling the author they
        were wrong about their own document."""
        assert html_of("4. Doors\n5. Music") == (
            '<ol start="4"><li>Doors</li><li>Music</li></ol>'
        )

    def test_a_list_that_starts_at_one_says_nothing_extra(self):
        assert 'start="1"' not in html_of("1. Doors\n2. Music")

    def test_closing_brackets_number_a_list_too(self):
        assert html_of("1) Doors\n2) Music").startswith("<ol>")

    def test_the_three_lists_do_not_collide(self):
        """`-- a` must not be read as a bulleted item whose text starts with
        a dash, and a bulleted list must not be read as a dashed one."""
        out = html_of("- Bulleted\n\n-- Dashed\n\n1. Numbered")
        assert out.count("<ul>") == 1
        assert out.count('<ul class="dashed">') == 1
        assert out.count("<ol>") == 1


class TestWhatIsNotAList:
    def test_one_dashed_line_inside_a_paragraph_stays_a_paragraph(self):
        """Somebody writing a sentence with a dash in it is writing a
        sentence. Turning that line into a one-item list in the middle of
        their paragraph is the kind of helpfulness that makes people stop
        using the formatting."""
        out = html_of("We meet on Sunday\n- which is the tenth\nat eight")
        assert out.startswith("<p>")
        assert "<ul>" not in out

    def test_one_numbered_line_inside_a_paragraph_stays_a_paragraph(self):
        """A list is a whole block, every line of it.

        This is the counterpart of the dash case above and it fails harder:
        a version that accepted *any* numbered line in a block would reach
        for a match on the lines that have none, and the church would get a
        500 instead of an email. Nothing caught that until this test existed.
        """
        out = html_of("We meet on Sunday\n1. the tenth\nat eight")
        assert out.startswith("<p>")
        assert "<ol" not in out

    def test_a_year_at_the_start_of_a_line_is_not_item_two_thousand(self):
        out = html_of("2026. What a year that was.")
        assert "<ol" not in out
        assert out.startswith("<p>")

    def test_a_bare_dash_with_no_space_is_not_a_list(self):
        assert "<ul>" not in html_of("-Coffee\n-Tea")

    def test_a_blank_line_ends_a_list(self):
        out = html_of("- Coffee\n\nThen we start.")
        assert out == "<ul><li>Coffee</li></ul><p>Then we start.</p>"


class TestNothingATypistWritesBecomesMarkup:
    """The author is a pastor, which is not the same as trusted.

    A leader account is one weak password away from somebody else, and the
    output of this function is displayed in members' browsers and delivered
    to their inboxes. Escaping happens before a single tag is written, so
    these cases are not a filter that has to anticipate anything.
    """

    def test_a_script_tag_is_text(self):
        out = html_of("<script>alert(1)</script>")
        assert "<script" not in out
        assert "&lt;script&gt;" in out

    def test_a_script_tag_inside_a_list_item_is_text(self):
        out = html_of("- <img src=x onerror=alert(1)>")
        assert "<img" not in out
        assert "onerror" in out  # as text, inside the escaped item

    def test_an_attribute_cannot_be_broken_out_of(self):
        """The dashed list writes a class attribute. A quote in the author's
        text must not reach it."""
        out = html_of('-- " onmouseover="alert(1)')
        assert "onmouseover=\"alert" not in out
        assert 'class="dashed"' in out

    def test_bold_markers_cannot_smuggle_a_tag(self):
        out = html_of("**<b>already bold</b>**")
        assert "<b>" not in out
        assert "<strong>" in out

    def test_a_quote_marker_is_matched_after_escaping(self):
        """`>` is escaped to `&gt;` before the grammar looks at it, so the
        blockquote rule matches the escaped form. A change that matched the
        raw `>` instead would quietly stop recognising quotes."""
        assert html_of("> For where two or three").startswith("<blockquote>")


class TestTheHtmlHalfOfAnEmail:
    def test_every_queued_email_gets_one(self, db, journey, person):
        """It is built in `queue`, which every email in this application goes
        through, rather than at each send site. There are fifteen of those
        and thirteen of them are not a screen anybody writes on."""
        message = queue(
            church_id=journey.id, category="announcement",
            subject="Sunday", body_text="- Coffee\n- A Bible", person=person,
        )
        db.session.commit()
        assert message.body_html
        assert "<ul" in message.body_html and "Coffee</li>" in message.body_html

    def test_the_plain_text_half_is_untouched(self, db, journey, person):
        """The guarantee that makes this safe to switch on everywhere at
        once. A message whose wording happens to look like a marker can look
        odd in HTML; it can never lose a word, because the other half is
        still the author's literal characters."""
        body = "Hello Marcus,\n\n- Coffee\n\n**Thanks**\n\n2026. What a year."
        message = queue(
            church_id=journey.id, category="announcement",
            subject="Sunday", body_text=body, person=person,
        )
        db.session.commit()
        assert message.body_text == body

    def test_an_email_with_no_formatting_in_it_still_gets_html(self, db, journey,
                                                               person):
        """"Sometimes HTML, sometimes not" is two code paths through every
        message a church sends, and a plain note still wants a line height
        and a margin."""
        message = queue(
            church_id=journey.id, category="announcement",
            subject="Sunday", body_text="We start at eight.", person=person,
        )
        db.session.commit()
        assert message.body_html
        assert "We start at eight." in message.body_html

    def test_it_carries_the_church_name(self, db, journey, person):
        message = queue(
            church_id=journey.id, category="announcement",
            subject="Sunday", body_text="Hello.", person=person,
        )
        db.session.commit()
        assert journey.name in message.body_html

    def test_a_caller_that_builds_its_own_html_keeps_it(self, db, journey, person):
        message = queue(
            church_id=journey.id, category="announcement",
            subject="Sunday", body_text="Hello.", body_html="<p>mine</p>",
            person=person,
        )
        db.session.commit()
        assert message.body_html == "<p>mine</p>"

    def test_the_transport_sends_it(self, db, journey, person):
        transport = MemoryTransport()
        queue(church_id=journey.id, category="announcement", subject="Sunday",
              body_text="- Coffee", person=person)
        db.session.commit()
        send_pending(transport=transport)

        assert transport.sent
        assert "Coffee</li>" in transport.sent[0].body_html
        assert "<ul" in transport.sent[0].body_html
        assert transport.sent[0].body_text == "- Coffee"

    def test_a_renderer_failure_does_not_cost_the_email(self, db, journey, person,
                                                        monkeypatch):
        """A password reset must not become a 500 because a branding value
        will not parse. The message still goes as plain text, which is what
        every email in this system was until this change."""
        import app.email_html as email_html

        def explode(*args, **kwargs):
            raise RuntimeError("no")

        monkeypatch.setattr(email_html, "render_body", explode)

        message = queue(
            church_id=journey.id, category="account",
            subject="Reset your password", body_text="Here is your link.",
            person=person,
        )
        db.session.commit()
        assert message is not None
        assert message.body_html is None
        assert message.body_text == "Here is your link."


class TestTheHtmlSurvivesAMailClient:
    """Written for Outlook and Gmail rather than for a browser.

    None of this is visible in a test runner, which is exactly why it is
    asserted: the failure arrives a week later as "it looks wrong on my
    phone" with no way to tell which rule went missing.
    """

    def test_every_tag_carries_its_own_style(self, db, journey):
        """Gmail strips `<style>` blocks in some contexts and keeps them in
        others, and Outlook renders with Word. A class alone decides
        nothing."""
        out = render_body(
            "# Heading\n\nA plain sentence.\n\n- Coffee\n\n1. First\n\n> Quote",
            journey,
        )
        for tag in ("<p", "<h3", "<ul", "<ol", "<li", "<blockquote"):
            found = re.findall(re.escape(tag) + r"[^>]*>", out)
            assert found, f"{tag} never appeared"
            for instance in found:
                assert "style=" in instance, f"{instance} has no inline style"

    def test_a_dashed_list_shows_no_bullet(self):
        """It prints its own dash. A marker as well would be two."""
        out = inline_styles(str(render("-- Parking")), "#485B38")
        ul = re.search(r"<ul[^>]*>", out).group(0)
        assert "list-style:none" in ul

    def test_a_bulleted_list_keeps_its_bullet(self):
        out = inline_styles(str(render("- Parking")), "#485B38")
        ul = re.search(r"<ul[^>]*>", out).group(0)
        assert "list-style:none" not in ul

    def test_the_quote_rule_uses_the_church_colour(self, db, journey):
        out = render_body("> For where two or three", journey)
        assert journey.accent_hex.lower() in out.lower()

    def test_it_is_a_whole_document(self, db, journey):
        """A fragment is what a client wraps in its own defaults, and
        Outlook's default is Times New Roman."""
        out = render_body("Hello.", journey)
        assert out.startswith("<!doctype html>")
        assert "<body" in out and "</html>" in out

    def test_the_layout_is_a_table(self, db, journey):
        """Outlook ignores max-width on a div. The one layout element in
        this document is the one every client has agreed on since 1998."""
        assert "<table" in render_body("Hello.", journey)

    def test_it_loads_nothing_from_anywhere(self, db, journey):
        """No web font, no image, no stylesheet. A resource an email client
        has to fetch is a resource it will not fetch, and a logo URL that
        did work would work for anybody who received the message."""
        out = render_body("# Sunday\n\n- Coffee", journey)
        assert "<img" not in out
        assert "<link" not in out
        assert "http://" not in out and "https://" not in out

    def test_a_church_name_with_a_bracket_in_it_is_escaped(self, db, journey):
        journey.name = 'The <script>alert(1)</script> Church'
        out = render_body("Hello.", journey)
        assert "<script" not in out


class TestTheToolbar:
    def test_it_is_on_the_church_email_composer(self, client, sign_in):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/messages/email/", headers=H).get_data(as_text=True)
        assert 'data-formatbar="body"' in page
        assert FORMAT["bullets"] in page
        assert FORMAT["numbers"] in page
        assert FORMAT["dashes"] in page

    def test_it_is_on_the_person_page_email(self, client, sign_in, person):
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        assert 'data-formatbar="person_email_body"' in page
        assert 'id="person_email_body"' in page

    def test_each_bar_names_the_box_it_writes_into(self, client, sign_in, person):
        """By id, so a screen with two composers on it could never have one
        writing into the other."""
        sign_in("pastor@journeychurchsemo.com")
        page = client.get(f"/people/{person.id}/", headers=H).get_data(as_text=True)
        for target in re.findall(r'data-formatbar="([^"]+)"', page):
            assert f'id="{target}"' in page

    def test_the_markers_on_the_buttons_are_the_ones_that_work(self, client,
                                                               sign_in):
        """The button inserts a prefix and the renderer reads a prefix. If
        those two ever disagree, pressing Bullets produces a paragraph that
        starts with a dash and nobody can see why."""
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/messages/email/", headers=H).get_data(as_text=True)
        prefixes = set(re.findall(r'data-prefix="([^"]+)"', page))

        assert "- " in prefixes and html_of("- a").startswith("<ul>")
        assert "-- " in prefixes and 'class="dashed"' in html_of("-- a")
        assert "1. " in prefixes and html_of("1. a").startswith("<ol>")
        assert "# " in prefixes and html_of("# a").startswith("<h3>")
        assert "&gt; " in prefixes and html_of("> a").startswith("<blockquote>")

    def test_the_hint_names_every_marker(self):
        """The buttons are a cramped row on a phone. Somebody who reads this
        line once can format an email without them."""
        for marker in ("- ", "1. ", "-- ", "# ", "> ", "**bold**", "*italic*"):
            assert marker.strip() in FORMAT["hint"]


class TestTheGuideInSettings:
    def test_the_sample_is_rendered_by_the_real_renderer(self, client, sign_in):
        """Not written out as markup. A hand-written sample drifts the first
        time the grammar changes, and the drift is invisible until a church
        is reading the wrong thing."""
        sign_in("pastor@journeychurchsemo.com")
        page = client.get("/settings/?open=email", headers=H).get_data(as_text=True)
        assert str(render(SETTINGS["format_sample"])) in page

    def test_the_sample_uses_all_three_lists(self):
        out = str(render(SETTINGS["format_sample"]))
        assert "<ol" in out
        assert '<ul class="dashed">' in out
        # A plain bulleted list as well as the dashed one.
        assert out.count("<ul") - out.count('<ul class="dashed">') == 1

    def test_a_member_cannot_open_settings(self, client, sign_in):
        sign_in("member@journeychurchsemo.com")
        assert client.get("/settings/", headers=H).status_code == 403


class TestTheTestEmail:
    def test_it_shows_every_kind_of_formatting(self):
        """The button already sends one email to the person pressing it, and
        the only thing that shows how somebody's own mail client renders is
        an email in their own inbox. Two purposes, one email."""
        body = SETTINGS["email_test_body"].format(name="Dana", church="Journey")
        out = str(render(body))
        assert "<h3>" in out
        assert "<ul>" in out
        assert "<ol" in out
        assert '<ul class="dashed">' in out
        assert "<blockquote>" in out
        assert "<strong>" in out and "<em>" in out

    def test_it_still_says_what_the_test_is_for(self):
        """It is a delivery check first. A formatting sample that buried
        "your password resets are reaching people" would have replaced the
        reason the button exists."""
        body = SETTINGS["email_test_body"].format(name="Dana", church="Journey")
        assert "password resets" in body

    def test_it_warns_that_plain_text_is_normal(self):
        body = SETTINGS["email_test_body"].format(name="Dana", church="Journey")
        assert "plain text version" in body


class TestLooksFormatted:
    @pytest.mark.parametrize("source", [
        "- a", "-- a", "1. a", "# a", "> a", "it is **bold**",
    ])
    def test_it_spots_the_grammar(self, source):
        assert looks_formatted(source) is True

    @pytest.mark.parametrize("source", [
        "", None, "Just a sentence.", "2026. What a year.", "a - b",
    ])
    def test_it_leaves_plain_writing_alone(self, source):
        assert looks_formatted(source) is False
