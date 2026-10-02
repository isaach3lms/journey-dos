"""Name tags as a PDF, at the exact size of the label in the printer.

**Why this exists at all.** The check-in screens printed name tags by calling
`window.print()` on an HTML page. That works in a desktop browser and does
nothing at all inside the iOS app: Apple's web view has no print support, so
the button was silently dead on exactly the device the church runs check-in
on. There is no CSS fix for a function the engine does not implement.

**Why a PDF rather than making the HTML print better.** A Brother QL is a
label printer: it holds a roll of a known physical size and expects a page of
that size. AirPrint handed an HTML page has to guess, and the guesses are all
bad in the same way. A PDF carries real physical dimensions, so a 62mm label
prints at 62mm and the tag lands on the label instead of across four of them.

**Why the sizes are a Python structure rather than a free-text setting.** A
church setting the width in millimetres is a church printing one blank label
after another until somebody works out that the roll is 29mm. The roll has a
code printed on the box. Pick the code.

Adding a size is a line here, not a migration, which is the same reason
stages and sequences live in Python.
"""

from __future__ import annotations

from dataclasses import dataclass

MM = 72.0 / 25.4  # points per millimetre


@dataclass(frozen=True)
class LabelSize:
    code: str
    label: str
    width_mm: float
    height_mm: float

    @property
    def width(self) -> float:
        return self.width_mm * MM

    @property
    def height(self) -> float:
        return self.height_mm * MM


# The Brother DK rolls a church is actually likely to have, plus a plain paper
# fallback for somebody who has not bought a label printer yet.
#
# DK-2205 is a continuous roll with no fixed length, so a cut length is chosen
# here rather than left to the printer. 90mm is a normal name tag.
SIZES: tuple[LabelSize, ...] = (
    LabelSize("dk1202", "Brother DK-1202 shipping label, 62 x 100mm", 62, 100),
    LabelSize("dk2205", "Brother DK-2205 continuous roll, 62mm wide", 62, 90),
    LabelSize("dk1201", "Brother DK-1201 address label, 29 x 90mm", 29, 90),
    LabelSize("dk1208", "Brother DK-1208 large address label, 38 x 90mm", 38, 90),
    LabelSize("letter", "Plain paper, one tag per sheet", 215.9, 279.4),
)

DEFAULT_SIZE = "dk1202"

BY_CODE = {size.code: size for size in SIZES}


def size_for(code: str | None) -> LabelSize:
    """Never raises. An unknown code is a church whose setting predates a
    rename, and printing the default beats printing nothing."""
    return BY_CODE.get((code or "").strip().lower(), BY_CODE[DEFAULT_SIZE])


def render_tags(*, checkins, church, checkin_session, size: LabelSize,
                when: str) -> bytes:
    """One page per tag, plus one pickup tag for the adult.

    The layout is deliberately plain. A tag is read at arm's length by somebody
    holding a toddler, and read again by a volunteer at a door comparing two
    short codes. Big name, big code, nothing else competing with them.

    Nothing from a person's notes appears here, for the same reason it does not
    appear on the HTML tag: a tag gets left on a table.
    """
    from io import BytesIO

    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(size.width, size.height))
    pdf.setTitle(f"{church.name} check-in tags")

    margin = 4 * MM
    inner = size.width - (2 * margin)

    def fitted(text: str, font: str, biggest: float, smallest: float = 7) -> float:
        """The largest size at which this fits on one line.

        A child called Konstantinos gets a smaller name than one called Eli
        rather than a name running off the edge of the label.
        """
        points = biggest
        while points > smallest and pdf.stringWidth(text, font, points) > inner:
            points -= 0.5
        return points

    def centered(text: str, font: str, points: float, y: float) -> None:
        pdf.setFont(font, points)
        pdf.drawCentredString(size.width / 2, y, text)

    for checkin in checkins:
        top = size.height - margin

        centered(church.name[:40], "Helvetica", 8, top - (3 * MM))
        centered(when, "Helvetica", 8, top - (8 * MM))

        # The cap is deliberately larger than anything that will be used. A
        # short name should grow to fill the label, not sit at a default
        # surrounded by white space, because this is read across a room by a
        # volunteer at a door. `fitted` only ever shrinks, so the cap decides
        # how big "Eli" gets and the label width decides the rest.
        first = checkin.person.first_name
        name_points = fitted(first, "Helvetica-Bold", min(size.height * 0.26, 72))
        centered(first, "Helvetica-Bold", name_points, size.height * 0.46)

        last = checkin.person.last_name
        last_points = fitted(last, "Helvetica", min(size.height * 0.1, 22))
        centered(last, "Helvetica", last_points,
                 size.height * 0.46 - (last_points * 1.5))

        room = checkin.room or checkin_session.name or ""
        if room:
            centered(room[:40], "Helvetica", fitted(room[:40], "Helvetica", 12),
                     margin + (8 * MM))

        centered(checkin.pickup_code, "Courier-Bold",
                 fitted(checkin.pickup_code, "Courier-Bold", 20), margin + (2 * MM))

        pdf.showPage()

    # One pickup tag however many children there are: the code belongs to the
    # household, and handing a parent three identical stubs is how one gets
    # lost and the other two get handed to somebody else.
    if checkins:
        centered(_pickup_title(church), "Helvetica-Bold", 10,
                 size.height - margin - (4 * MM))
        centered(when, "Helvetica", 8, size.height - margin - (9 * MM))

        centered("PICKUP CODE", "Helvetica", 9, size.height * 0.62)
        code = checkins[0].pickup_code
        centered(code, "Courier-Bold",
                 fitted(code, "Courier-Bold", min(size.height * 0.2, 48)),
                 size.height * 0.40)

        names = ", ".join(c.person.first_name for c in checkins)
        lines = simpleSplit(names, "Helvetica", 10, inner)[:3]
        y = margin + (6 * MM)
        for line in reversed(lines):
            centered(line, "Helvetica", 10, y)
            y += 4.5 * MM

        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def _pickup_title(church) -> str:
    name = church.name or "Church"
    return f"{name[:28]} kids"
