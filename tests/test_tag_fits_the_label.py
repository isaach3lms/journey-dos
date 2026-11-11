"""Nothing on a name tag runs off the label.

The bug: tags came out of the Journey printer shifted sideways with letters
missing off the names. The roll was the right one and the page was the right
size, which is what made it confusing.

A Brother QL does not centre what it prints on the page it is handed. The
print head is narrower than the tape and sits to one side of it, and how far
depends on the machine and on how the roll is seated. Hand it a 62mm page
with a name filling 53mm of it and a few millimetres of drift takes the ends
off the name.

Two things were wrong on our side, and both are covered here:

**Only some of the text was measured.** The first name was shrunk to fit and
the church name above it was drawn at a fixed eight points, measured against
nothing. On a narrow roll that line alone overran the label.

**"Fits the page" is not "fits what prints".** Everything now sits inside
`LabelSize.safe_mm`, far enough in to absorb the head offset and the
unprintable edge together.

And for the printer that is worse than average there is `tag_nudge`, which
moves the design rather than shrinking it, plus an alignment label that turns
"it looks shifted" into a number to type in.
"""

import pytest

from app.labels import BY_CODE, MM, render_alignment_test, render_tags, size_for
from app.models import Church
from tests.conftest import JOURNEY_HOST

H = {"Host": JOURNEY_HOST}


class FakePerson:
    def __init__(self, first, last):
        self.first_name, self.last_name = first, last


class FakeCheckin:
    def __init__(self, first, last, code="4821", room="Elementary"):
        self.person = FakePerson(first, last)
        self.pickup_code = code
        self.room = room


class FakeChurch:
    def __init__(self, name="The Journey Church"):
        self.name = name


class FakeSession:
    def __init__(self, name="Sunday 9:30am"):
        self.name = name


def ink_box(pdf_bytes, page=0):
    """Where the ink actually lands, by rendering the page and looking.

    Measured off a raster rather than off the text boxes on purpose. A span's
    box is the font's box: it includes the room a descender would occupy
    whether or not the word has one, so "4821" measures as though the 4 had a
    tail. The printer puts down marks, not font metrics, and a test of
    whether a mark falls off the label has to measure marks.

    Returns (page rect, ink rect) in points, both the right way up.
    """
    pymupdf = pytest.importorskip("pymupdf")

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    rendered = document[page]
    dpi = 200
    pixmap = rendered.get_pixmap(dpi=dpi)
    scale = 72.0 / dpi

    samples = pixmap.samples
    width, height, stride = pixmap.width, pixmap.height, pixmap.stride
    channels = pixmap.n

    left, right, top, bottom = width, -1, height, -1
    for y in range(height):
        row = samples[y * stride:y * stride + width * channels]
        for x in range(width):
            # Anything meaningfully darker than the label.
            if row[x * channels] < 200:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y

    assert right >= 0, "nothing was drawn on this page at all"
    return (
        rendered.rect,
        pymupdf.Rect(left * scale, top * scale,
                     (right + 1) * scale, (bottom + 1) * scale),
    )


def text_box(pdf_bytes, page=0):
    """Where the words sit, ignoring lines and rules.

    Only for the alignment label, whose scale runs off both edges of the
    page deliberately. Everywhere else `ink_box` is the honest measure.
    """
    pymupdf = pytest.importorskip("pymupdf")

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    rect = None
    for block in document[page].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                here = pymupdf.Rect(span["bbox"])
                rect = here if rect is None else rect | here
    return document[page].rect, rect


def clearances_mm(pdf_bytes, page=0):
    """Clear space on each side, in millimetres: (left, right, top, bottom).

    The raster is measured from the top, which is upside down from the way
    the PDF was drawn, so `top` here is the gap above the highest ink as a
    reader sees it.
    """
    page_rect, ink = ink_box(pdf_bytes, page)
    return (
        ink.x0 / MM,
        (page_rect.width - ink.x1) / MM,
        ink.y0 / MM,
        (page_rect.height - ink.y1) / MM,
    )


def tags_for(first, last, code="dk1202", church_name="The Journey Church",
             nudge=(0.0, 0.0), room="Elementary"):
    return render_tags(
        checkins=[FakeCheckin(first, last, room=room)],
        church=FakeChurch(church_name),
        checkin_session=FakeSession(),
        size=size_for(code),
        when="Oct 12, 9:30am",
        nudge=nudge,
    )


# Names chosen to attack the layout rather than to be realistic: the longest
# single word that cannot be broken, a double name that can, and a short one
# that the fitter is allowed to grow very large.
HARD_NAMES = [
    ("Eli", "Webb"),
    ("Mary Beth", "Slinkard"),
    ("Konstantinos", "Papadopoulos"),
    ("Bartholomew", "Fotopoulos-Williams"),
    ("Anne-Marguerite", "Vandermolen"),
]


class TestNothingTouchesTheEdge:
    @pytest.mark.parametrize("code", sorted(BY_CODE))
    @pytest.mark.parametrize("first,last", HARD_NAMES)
    def test_every_name_on_every_roll(self, code, first, last):
        """The check that would have caught this before it reached a church."""
        size = size_for(code)
        pdf = tags_for(first, last, code)
        left, right, top, bottom = clearances_mm(pdf)

        for side, got in (("left", left), ("right", right),
                          ("top", top), ("bottom", bottom)):
            assert got >= size.safe_mm - 0.6, (
                f"{first} {last} on {code}: only {got:.1f}mm clear on the "
                f"{side}, and the safe area is {size.safe_mm}mm. A Brother "
                f"shifts the page by more than that."
            )

    @pytest.mark.parametrize("code", sorted(BY_CODE))
    def test_the_pickup_tag_too(self, code):
        """It is a second page with its own layout, and it was never looked
        at when the first one was fixed."""
        size = size_for(code)
        pdf = render_tags(
            checkins=[FakeCheckin("Konstantinos", "Papadopoulos")],
            church=FakeChurch(),
            checkin_session=FakeSession(),
            size=size_for(code),
            when="Oct 12, 9:30am",
        )
        left, right, top, bottom = clearances_mm(pdf, page=1)
        assert min(left, right, top, bottom) >= size.safe_mm - 0.6

    def test_a_long_church_name_on_the_narrowest_roll(self):
        """The line that was never measured at all.

        It was drawn at a fixed eight points. On a 29mm roll, a church with
        a long name overran the label on every tag it ever printed, and the
        name underneath it looked fine, which is why nobody connected the
        two.
        """
        size = size_for("dk1201")
        pdf = tags_for("Eli", "Webb", "dk1201",
                       church_name="Grace Community Fellowship of the Hills")
        left, right, _, _ = clearances_mm(pdf)
        assert min(left, right) >= size.safe_mm - 0.6

    def test_a_long_room_name(self):
        size = size_for("dk1201")
        pdf = tags_for("Eli", "Webb", "dk1201",
                       room="Early Elementary, Kindergarten through Second")
        left, right, _, _ = clearances_mm(pdf)
        assert min(left, right) >= size.safe_mm - 0.6


class TestTheNameIsStillReadable:
    """A tag nobody can read across a room is not a fixed tag.

    The easy way to pass the test above is to shrink everything to nothing,
    so this is the other half of the vice.
    """

    def test_a_short_name_grows(self):
        pymupdf = pytest.importorskip("pymupdf")

        pdf = tags_for("Eli", "Webb")
        document = pymupdf.open(stream=pdf, filetype="pdf")
        sizes = [
            span["size"]
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if "Eli" in span["text"]
        ]
        assert sizes and max(sizes) >= 24, (
            f"the name printed at {max(sizes, default=0):.0f}pt on a 62mm "
            "label, which is not a name a volunteer reads at a door"
        )

    def test_a_two_word_name_uses_two_lines(self):
        """"Mary Beth" strung out on one line has to shrink to about half
        what it manages stacked, and the name is the whole point of the
        tag."""
        pymupdf = pytest.importorskip("pymupdf")

        pdf = tags_for("Mary Beth", "Slinkard")
        document = pymupdf.open(stream=pdf, filetype="pdf")
        text = document[0].get_text()
        assert "Mary\nBeth" in text or ("Mary" in text and "Beth" in text)

        stacked = [
            span["size"]
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip() in ("Mary", "Beth")
        ]
        assert stacked and max(stacked) >= 24

    def test_an_unbreakable_name_shrinks_rather_than_clipping(self):
        """A single long word has nowhere to break, so the only honest
        answer is a smaller name. Smaller beats missing."""
        pdf = tags_for("Konstantinos", "Papadopoulos")
        left, right, _, _ = clearances_mm(pdf)
        assert min(left, right) >= size_for("dk1202").safe_mm - 0.6


class TestTheNudge:
    def test_it_moves_the_tag(self):
        plain = clearances_mm(tags_for("Konstantinos", "Papadopoulos"))
        moved = clearances_mm(
            tags_for("Konstantinos", "Papadopoulos", nudge=(4.0, 0.0)))

        assert moved[0] > plain[0] + 3, "the tag did not move right"
        assert moved[1] < plain[1] - 3

    def test_up_moves_it_up(self):
        plain = clearances_mm(tags_for("Eli", "Webb"))
        moved = clearances_mm(tags_for("Eli", "Webb", nudge=(0.0, 3.0)))
        # Index 3 is the gap below the lowest text, which grows as the
        # design rises.
        assert moved[3] > plain[3] + 2

    def test_zero_changes_nothing(self):
        assert (clearances_mm(tags_for("Eli", "Webb"))
                == clearances_mm(tags_for("Eli", "Webb", nudge=(0.0, 0.0))))

    def test_the_church_clamps_a_silly_value(self, db):
        """A number read wrong off the test label must not produce a blank
        tag nobody can explain on a Sunday morning."""
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        church.kids_tag_nudge_x = 400.0
        church.kids_tag_nudge_y = -400.0
        db.session.commit()

        x, y = church.tag_nudge
        assert x == Church.NUDGE_LIMIT_MM
        assert y == -Church.NUDGE_LIMIT_MM

    def test_a_church_that_never_touches_it_is_centred(self, db):
        church = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        assert church.tag_nudge == (0.0, 0.0)


class TestTheAlignmentLabel:
    def test_the_ruler_runs_to_the_paper_edge(self):
        """The whole point of it. A ruler that stopped inside the safe
        margin would print perfectly and measure nothing, because the
        number you need is the one where the paper stops."""
        pymupdf = pytest.importorskip("pymupdf")

        size = size_for("dk1202")
        pdf = render_alignment_test(church=FakeChurch(), size=size)
        document = pymupdf.open(stream=pdf, filetype="pdf")
        page = document[0]

        widest = 0.0
        for drawing in page.get_drawings():
            rect = drawing["rect"]
            widest = max(widest, rect.x1 - rect.x0)

        assert widest >= size.width - 1, (
            "the scale does not reach both edges of the label, so there is "
            "nothing to read where the paper stops"
        )

    def test_the_instructions_stay_on_the_label(self):
        """Written at a fixed size they overran the label, which is the
        same bug as the one being diagnosed, printed on the diagnostic."""
        pymupdf = pytest.importorskip("pymupdf")

        size = size_for("dk1202")
        pdf = render_alignment_test(church=FakeChurch(), size=size)
        document = pymupdf.open(stream=pdf, filetype="pdf")
        page = document[0]

        # The sentences only. The scale and its numbers run to the paper
        # edge on purpose, which is the one thing on this label that is
        # supposed to be cut off.
        words = None
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not any(ch.isalpha() for ch in span["text"]):
                        continue
                    here = pymupdf.Rect(span["bbox"])
                    words = here if words is None else words | here

        assert words is not None
        assert words.x0 / MM >= size.safe_mm - 0.6
        assert (page.rect.width - words.x1) / MM >= size.safe_mm - 0.6

    def test_it_names_the_roll_it_was_made_for(self):
        """Somebody holding a test label needs to know whether the app and
        the printer even agree about what is loaded."""
        pymupdf = pytest.importorskip("pymupdf")

        pdf = render_alignment_test(church=FakeChurch(), size=size_for("dk1202"))
        text = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
        assert "DK1202" in text
        assert "62 x 100mm" in text

    def test_the_nudge_moves_it_too(self):
        """It has to show the effect of the setting, or somebody dials in a
        correction and the next test print looks identical."""
        plain = text_box(render_alignment_test(
            church=FakeChurch(), size=size_for("dk1202")))[1]
        moved = text_box(render_alignment_test(
            church=FakeChurch(), size=size_for("dk1202"), nudge=(4.0, 0.0)))[1]
        assert moved.x0 > plain.x0 + 3


class TestTheRoute:
    def test_staff_can_print_the_alignment_label(self, db, client, sign_in):
        from app.labeltoken import sign_sample

        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        sign_in("pastor@journeychurchsemo.com")
        token = sign_sample(church_id=journey.id)

        response = client.get(f"/kids/tags/alignment/{token}/", headers=H)
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"

    def test_a_forged_token_gets_nothing(self, client):
        response = client.get("/kids/tags/alignment/not-a-token/", headers=H)
        assert response.status_code == 404

    def test_another_church_token_does_not_work_here(self, db, client):
        from app.labeltoken import sign_sample

        other = db.session.scalar(db.select(Church).where(Church.slug == "riverbend"))
        token = sign_sample(church_id=other.id)
        assert client.get(
            f"/kids/tags/alignment/{token}/", headers=H).status_code == 404

    def test_the_settings_page_offers_the_test_label(self, db, client, sign_in):
        # The printer settings live behind the kiosk account, because a
        # church with no tablet has no printer either.
        sign_in("pastor@journeychurchsemo.com")
        client.post("/settings/kiosk/", headers=H)
        body = client.get("/settings/", headers=H).get_data(as_text=True)
        assert "Print the test label" in body
        assert "Names printing off the edge" in body

    def test_saving_a_nudge(self, db, client, sign_in):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        sign_in("pastor@journeychurchsemo.com")

        response = client.post("/settings/kiosk/nudge/",
                               data={"nudge_x": "3", "nudge_y": "-1.5"},
                               headers=H)
        assert response.status_code == 302
        db.session.refresh(journey)
        assert journey.tag_nudge == (3.0, -1.5)

    def test_nonsense_in_the_box_does_not_500(self, db, client, sign_in):
        """A Sunday morning is not the moment for a form error, let alone a
        stack trace."""
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        sign_in("pastor@journeychurchsemo.com")

        response = client.post("/settings/kiosk/nudge/",
                               data={"nudge_x": "left a bit", "nudge_y": ""},
                               headers=H)
        assert response.status_code == 302
        db.session.refresh(journey)
        assert journey.tag_nudge == (0.0, 0.0)

    def test_a_huge_number_is_clamped_rather_than_refused(self, db, client, sign_in):
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        sign_in("pastor@journeychurchsemo.com")

        client.post("/settings/kiosk/nudge/",
                    data={"nudge_x": "90", "nudge_y": "0"}, headers=H)
        db.session.refresh(journey)
        assert journey.tag_nudge[0] == Church.NUDGE_LIMIT_MM

    def test_a_leader_cannot_change_it(self, db, client, sign_in):
        sign_in("leader@journeychurchsemo.com")
        response = client.post("/settings/kiosk/nudge/",
                               data={"nudge_x": "3", "nudge_y": "0"}, headers=H)
        assert response.status_code in (302, 403)
        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        db.session.refresh(journey)
        assert journey.tag_nudge == (0.0, 0.0)


class TestTheRealTagsUseTheSetting:
    def test_a_printed_tag_carries_the_church_nudge(self, db, client, sign_in):
        """The setting existing and the tag honouring it are two different
        things, and the second one is what the church sees."""
        from app.labeltoken import sign_sample

        journey = db.session.scalar(db.select(Church).where(Church.slug == "journey"))
        journey.kids_tag_nudge_x = 5.0
        db.session.commit()

        sign_in("pastor@journeychurchsemo.com")
        token = sign_sample(church_id=journey.id)
        shifted = client.get(f"/kids/tags/sample/{token}/", headers=H).data

        journey.kids_tag_nudge_x = 0.0
        db.session.commit()
        plain = client.get(f"/kids/tags/sample/{token}/", headers=H).data

        assert ink_box(shifted)[1].x0 > ink_box(plain)[1].x0 + 3
