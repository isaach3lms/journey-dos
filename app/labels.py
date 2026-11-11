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

    @property
    def safe_mm(self) -> float:
        """How far in from the edge anything is allowed to be drawn.

        **This is the fix for names coming out cut off.** A Brother QL does
        not print to the edge of its tape and does not centre what it prints
        on the page it is handed: the head is narrower than the roll and
        sits slightly to one side of it. Hand it a 62mm page with a name
        filling 53mm of it and the result is a tag printed a few millimetres
        off, with letters missing from one end. That is exactly what Journey
        saw.

        Nothing in the print path reports that offset, so the tag is drawn
        to survive it instead: six millimetres of clear space on a wide roll
        absorbs the head offset, the unprintable edge, and a roll sitting
        slightly crooked in the machine, all at once.

        A church whose printer is worse than that has `tag_nudge`, which
        moves the whole design rather than making it smaller. This is the
        floor, not the whole answer.

        Narrow rolls get a smaller inset because six millimetres off a 29mm
        label leaves 17mm of usable width and a name nobody can read across
        a room.
        """
        return 6.0 if self.width_mm >= 50 else 3.5

    @property
    def safe_width(self) -> float:
        return self.width - (2 * self.safe_mm * MM)


# The Brother DK rolls a church is actually likely to have, plus a plain paper
# fallback for somebody who has not bought a label printer yet.
#
# DK-2205 is a continuous roll with no fixed length, so a cut length is chosen
# here rather than left to the printer. 90mm is a normal name tag.
# **These are rolls, not printers.** A church looking for its printer model
# here will not find it and will reasonably conclude the printer is
# unsupported: that is what happened with a QL-820NWB. Every Brother QL in
# this range takes the same DK rolls, tops out at 62mm wide, and what the tag
# has to match is the roll, not the machine. The labels below therefore name
# the roll code, which is printed on the box, and the setting's description
# says in words that this is the roll.
#
# DK-2251 is dimensionally identical to DK-2205 and is here anyway, because
# somebody holding a DK-2251 box needs to find DK-2251. A list that silently
# expects you to know two codes are the same size is a list that prints blanks.
SIZES: tuple[LabelSize, ...] = (
    LabelSize("dk1202", "DK-1202 shipping label, 62 x 100mm", 62, 100),
    LabelSize("dk2205", "DK-2205 continuous roll, 62mm wide", 62, 90),
    LabelSize("dk2251", "DK-2251 continuous roll, 62mm wide, black and red",
              62, 90),
    LabelSize("dk1201", "DK-1201 address label, 29 x 90mm", 29, 90),
    LabelSize("dk1208", "DK-1208 large address label, 38 x 90mm", 38, 90),
    LabelSize("letter", "Plain paper, one tag per sheet", 215.9, 279.4),
)

DEFAULT_SIZE = "dk1202"

BY_CODE = {size.code: size for size in SIZES}


def size_for(code: str | None) -> LabelSize:
    """Never raises. An unknown code is a church whose setting predates a
    rename, and printing the default beats printing nothing."""
    return BY_CODE.get((code or "").strip().lower(), BY_CODE[DEFAULT_SIZE])


def render_tags(*, checkins, church, checkin_session, size: LabelSize,
                when: str, nudge=(0.0, 0.0)) -> bytes:
    """One page per tag, plus one pickup tag for the adult.

    The layout is deliberately plain. A tag is read at arm's length by somebody
    holding a toddler, and read again by a volunteer at a door comparing two
    short codes. Big name, big code, nothing else competing with them.

    Nothing from a person's notes appears here, for the same reason it does not
    appear on the HTML tag: a tag gets left on a table.

    **Everything is drawn inside `size.safe_mm`, and everything is fitted to
    it.** Both halves of that matter. The name was already shrunk to fit and
    still came out cut off, because the church name above it was drawn at a
    fixed eight points and never measured at all, and because "fits the page"
    is not the same as "fits what the printer prints". See `LabelSize.safe_mm`.

    `nudge` is (right, up) in millimetres, from the church's setting, for a
    printer that puts the whole tag off to one side. Positive moves the design
    right and up.
    """
    from io import BytesIO

    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(size.width, size.height))
    pdf.setTitle(f"{church.name} check-in tags")

    safe = size.safe_mm * MM
    inner = size.safe_width
    dx, dy = (nudge or (0.0, 0.0))
    dx, dy = dx * MM, dy * MM
    middle = (size.width / 2) + dx

    def fitted(text: str, font: str, biggest: float, smallest: float = 6) -> float:
        """The largest size at which this fits on one line of the safe area.

        A child called Konstantinos gets a smaller name than one called Eli
        rather than a name running off the edge of the label.
        """
        points = biggest
        while points > smallest and pdf.stringWidth(text, font, points) > inner:
            points -= 0.5
        return points

    def fit_text(text: str, font: str, biggest: float,
                 smallest: float = 6) -> tuple:
        """The text and the size to draw it at, both guaranteed to fit.

        **Shrinking alone is not enough and that was a real bug.** `fitted`
        stops at a floor, because six point text on a name tag is not worth
        printing, and then the caller drew the string anyway. A church
        called "Grace Community Fellowship of the Hills" on a 29mm roll
        overran the label on every tag it ever printed, and so did a room
        called "Early Elementary, Kindergarten through Second". The name
        underneath them looked perfect, which is why nobody connected the
        overrun to anything.

        So when the floor is reached and it still does not fit, the text is
        cut to what does. A room name ending in an ellipsis is a room name
        somebody can read. A room name running off the label is ink on the
        backing paper.
        """
        points = fitted(text, font, biggest, smallest)
        if pdf.stringWidth(text, font, points) <= inner:
            return text, points

        ellipsis = "..."
        cut = text
        while cut and pdf.stringWidth(cut + ellipsis, font, points) > inner:
            cut = cut[:-1]
        return ((cut.rstrip() + ellipsis) if cut else text[:1]), points

    def centered(text: str, font: str, points: float, y: float) -> None:
        """Draws at a size and a length that both fit. Every caller goes
        through this now: a line drawn at a size nobody measured is the bug
        this whole module just had."""
        text, points = fit_text(text, font, min(points, fitted(text, font, points)))
        pdf.setFont(font, points)
        pdf.drawCentredString(middle, y + dy, text)

    def name_lines(first: str, cap: float) -> tuple[list, float]:
        """The first name, on one line or two, whichever reads bigger.

        A single long word can only shrink: "Konstantinos" has nowhere to
        break. But "Mary Beth" and "Anna Lucia" are common and fit far larger
        stacked than strung out, and the name is the one thing on this label
        that has to be readable from across a room.
        """
        one = fitted(first, "Helvetica-Bold", cap)
        words = first.split()
        if len(words) < 2:
            return [first], one

        # Two lines eat vertical room, so each can only be so tall before the
        # pair is worse than one line.
        pair_cap = cap * 0.64
        two = min(fitted(word, "Helvetica-Bold", pair_cap) for word in words)
        if two > one * 1.15:
            return words[:2], two
        return [first], one

    # The vertical layout is three bands, measured rather than guessed.
    #
    # Everything used to be placed at a fraction of the label height and the
    # pieces collided: a two line name pushed the surname down onto the room,
    # and the room sat on top of the pickup code. The code and the room are
    # pinned to the bottom, the church name and date to the top, and the name
    # gets the whole of what is left, centred in it.
    code_points = 20.0
    room_points = 12.0
    code_baseline = safe + (2 * MM)
    room_baseline = code_baseline + (code_points * 0.9) + (2.5 * MM)
    header_bottom = size.height - safe - (12 * MM)

    for checkin in checkins:
        top = size.height - safe

        centered(church.name[:40], "Helvetica", 8, top - (3 * MM))
        centered(when, "Helvetica", 8, top - (8 * MM))

        room = checkin.room or checkin_session.name or ""
        band_bottom = room_baseline + (room_points if room else 0) + (2 * MM)

        # The cap is deliberately larger than anything that will be used. A
        # short name should grow to fill the label, not sit at a default
        # surrounded by white space, because this is read across a room by a
        # volunteer at a door. `fitted` only ever shrinks, so the cap decides
        # how big "Eli" gets and the label width decides the rest.
        first = checkin.person.first_name
        lines, name_points = name_lines(first, min(size.height * 0.26, 72))

        last = checkin.person.last_name
        last_points = fitted(last, "Helvetica", min(size.height * 0.1, 22))

        # The block is the name lines plus the surname. Centring the block in
        # the band is what keeps a one line name and a two line name looking
        # like the same tag.
        block = (len(lines) * name_points * 1.02) + (last_points * 1.5 if last else 0)
        cursor = band_bottom + ((header_bottom - band_bottom - block) / 2) + block

        for line in lines:
            cursor -= name_points * 1.02
            centered(line, "Helvetica-Bold", name_points, cursor)

        if last:
            cursor -= last_points * 1.4
            centered(last, "Helvetica", last_points, cursor)

        if room:
            centered(room[:40], "Helvetica", room_points, room_baseline)

        centered(checkin.pickup_code, "Courier-Bold", code_points, code_baseline)

        pdf.showPage()

    # One pickup tag however many children there are: the code belongs to the
    # household, and handing a parent three identical stubs is how one gets
    # lost and the other two get handed to somebody else.
    if checkins:
        centered(_pickup_title(church), "Helvetica-Bold", 10,
                 size.height - safe - (4 * MM))
        centered(when, "Helvetica", 8, size.height - safe - (9 * MM))

        centered("PICKUP CODE", "Helvetica", 9, size.height * 0.62)
        code = checkins[0].pickup_code
        centered(code, "Courier-Bold", min(size.height * 0.2, 48),
                 size.height * 0.40)

        names = ", ".join(c.person.first_name for c in checkins)
        lines = simpleSplit(names, "Helvetica", 10, inner)[:3]
        y = safe + (6 * MM)
        for line in reversed(lines):
            centered(line, "Helvetica", 10, y)
            y += 4.5 * MM

        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def render_alignment_test(*, church, size: LabelSize, nudge=(0.0, 0.0)) -> bytes:
    """One label that shows where the printer actually puts the ink.

    A church reporting "the names are cut off" cannot say by how much, and
    nothing in the print path reports the offset, so the only honest way to
    find it is to print a ruler and look at it.

    **The ruler runs to the edge of the page, not to the safe area.** That is
    the entire point: the number still visible where the paper stops is how
    much is reaching the label on that side. A ruler that stopped inside the
    safe margin would print perfectly and measure nothing.

    The frame does sit on the safe area, so it doubles as the check on the
    layout: if the frame prints whole, a name will too.
    """
    from io import BytesIO

    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(size.width, size.height))
    pdf.setTitle(f"{church.name} label alignment")

    safe = size.safe_mm * MM
    inner = size.safe_width
    dx, dy = (nudge or (0.0, 0.0))
    dx, dy = dx * MM, dy * MM
    middle = (size.width / 2) + dx

    def line_at(text: str, points: float, y: float) -> None:
        """Fitted, like everything on the tag itself. An instruction that
        runs off the label is an instruction nobody can follow."""
        while points > 5 and pdf.stringWidth(text, "Helvetica", points) > inner:
            points -= 0.25
        pdf.setFont("Helvetica", points)
        pdf.drawCentredString(middle, y, text)

    # The frame, on the safe area. A side missing here means the tag is
    # being shifted the other way.
    pdf.setLineWidth(0.8)
    pdf.rect(safe + dx, safe + dy,
             size.width - (2 * safe), size.height - (2 * safe))

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(middle, size.height - safe - (6 * MM), "ALIGNMENT TEST")
    line_at(f"{size.code.upper()}  {size.width_mm:g} x {size.height_mm:g}mm", 7,
            size.height - safe - (11 * MM))

    # The scale, run right off both sides of the page on purpose.
    axis = size.height * 0.5 + dy
    pdf.setLineWidth(0.5)
    pdf.line(0, axis, size.width, axis)

    reach = int((size.width / 2) / MM) + 2
    for mm in range(0, reach + 1, 5):
        for direction in ((1, ) if mm == 0 else (1, -1)):
            x = middle + (direction * mm * MM)
            if x < -MM or x > size.width + MM:
                continue
            tall = 3.4 * MM if mm % 10 == 0 else 1.8 * MM
            pdf.line(x, axis, x, axis - tall)
            if mm % 10 == 0:
                pdf.setFont("Helvetica", 6)
                pdf.drawCentredString(x, axis - tall - (3 * MM), str(mm))

    line_at("Read the number where the paper stops,", 7.5,
            axis - (14 * MM))
    line_at("on the left and on the right.", 7.5, axis - (18.5 * MM))

    line_at("Same number both sides: nothing to change.", 7, safe + dy + (13 * MM))
    line_at("Different: halve the difference and put it", 7, safe + dy + (8.5 * MM))
    line_at("in Settings, minus if the left is bigger.", 7, safe + dy + (4 * MM))

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _pickup_title(church) -> str:
    name = church.name or "Church"
    return f"{name[:28]} kids"
