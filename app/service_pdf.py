"""The weekly services report: who served, and how many were in the room.

Portrait, unlike the roster report, because this one is a list of names under
a date rather than a table of columns. Names set in three columns under each
Sunday fit a portrait page and read down, which is how somebody checks
whether they are on it.

Drawn on the canvas directly, like `app/labels.py` and `app/roster_pdf.py`.
The layout is a fixed grid and a page break on a counter; Platypus is for
documents whose shape is not known in advance.
"""

from __future__ import annotations

from datetime import datetime, timezone

MM = 72.0 / 25.4

# US Letter, portrait.
PAGE_WIDTH = 8.5 * 72.0
PAGE_HEIGHT = 11 * 72.0

MARGIN = 16 * MM
HEADER_HEIGHT = 22 * MM
NAME_HEIGHT = 5.2 * MM
NAME_COLUMNS = 3

INK = (0.09, 0.10, 0.08)
MUTED = (0.42, 0.45, 0.40)
RULE = (0.85, 0.86, 0.83)
BAND = (0.95, 0.96, 0.94)


def _hex_to_rgb(value: str | None, fallback=(0.28, 0.36, 0.22)):
    if not value:
        return fallback
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return fallback


def _count(number: int, noun: str) -> str:
    """"1 person", not "1 persons". It is a sheet people read."""
    if noun == "person":
        return "1 person" if number == 1 else f"{number} people"
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def render_services(*, points, church, fmt, now=None, subtitle=None) -> bytes:
    """The sheet.

    `points` are `app.attendance.Point`s, oldest first, so the report reads
    forward through the weeks the way somebody would say them out loud.

    `fmt` formats a UTC datetime on the church's clock, passed in rather than
    imported so this file does nothing with time zones itself.
    """
    from io import BytesIO

    from reportlab.pdfgen import canvas

    now = now or datetime.now(timezone.utc)
    accent = _hex_to_rgb(getattr(church, "accent_hex", None))

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle(f"{church.name} services report")
    pdf.setAuthor(church.name)

    usable = PAGE_WIDTH - (2 * MARGIN)
    column_width = usable / NAME_COLUMNS
    state = {"page": 0, "y": 0.0}

    def new_page():
        if state["page"]:
            pdf.showPage()
        state["page"] += 1

        pdf.setFillColorRGB(*accent)
        pdf.rect(0, PAGE_HEIGHT - (4 * MM), PAGE_WIDTH, 4 * MM, stroke=0, fill=1)

        top = PAGE_HEIGHT - MARGIN
        pdf.setFillColorRGB(*INK)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(MARGIN, top - (5 * MM), f"{church.name} services")

        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(MARGIN, top - (10 * MM), subtitle or "Who served, and how many came")
        pdf.drawRightString(
            PAGE_WIDTH - MARGIN, top - (10 * MM),
            f"{fmt(now)}  ·  page {state['page']}",
        )
        state["y"] = top - HEADER_HEIGHT

    def room_for(points_needed: float) -> bool:
        return state["y"] - points_needed > MARGIN

    def clipped(text: str, font: str, size: float, width: float) -> str:
        text = text or ""
        if pdf.stringWidth(text, font, size) <= width:
            return text
        while text and pdf.stringWidth(text + "…", font, size) > width:
            text = text[:-1]
        return text + "…" if text else ""

    new_page()

    if not points:
        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica-Oblique", 10)
        pdf.drawString(MARGIN, state["y"], "No services in this range.")

    for point in points:
        week = point.week
        rows = -(-max(len(week.servers), 1) // NAME_COLUMNS)  # ceiling
        block = (14 * MM) + (rows * NAME_HEIGHT)

        # Keep a Sunday's heading with at least its first row of names. A
        # date alone at the foot of a page is a date somebody has to turn
        # over to make sense of.
        if not room_for(min(block, (14 * MM) + NAME_HEIGHT)):
            new_page()

        pdf.setFillColorRGB(*INK)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(MARGIN, state["y"], fmt(week.starts_at))

        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(
            MARGIN + pdf.stringWidth(fmt(week.starts_at), "Helvetica-Bold", 11) + (3 * MM),
            state["y"], week.name,
        )

        # The head count on the right, in the church's colour when it is
        # there and in words when it is not. A blank reads as nought, and
        # nought is a claim about a Sunday rather than an absence of one.
        if week.headcount is None:
            pdf.setFillColorRGB(*MUTED)
            pdf.setFont("Helvetica-Oblique", 9)
            pdf.drawRightString(PAGE_WIDTH - MARGIN, state["y"], "no count taken")
        else:
            pdf.setFillColorRGB(*accent)
            pdf.setFont("Helvetica-Bold", 11)
            label = _count(week.headcount, "person")
            pdf.drawRightString(PAGE_WIDTH - MARGIN, state["y"], label)

            if point.change is not None:
                pdf.setFillColorRGB(*MUTED)
                pdf.setFont("Helvetica", 8)
                arrow = "+" if point.change > 0 else ""
                pdf.drawRightString(
                    PAGE_WIDTH - MARGIN, state["y"] - (4 * MM),
                    f"{arrow}{point.change} on the week before",
                )

        state["y"] -= 5.5 * MM
        pdf.setStrokeColorRGB(*RULE)
        pdf.setLineWidth(0.6)
        pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
        state["y"] -= 5 * MM

        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawString(MARGIN, state["y"], _count(week.served_count, "VOLUNTEER").upper())
        state["y"] -= 5 * MM

        if not week.servers:
            pdf.setFillColorRGB(*MUTED)
            pdf.setFont("Helvetica-Oblique", 9)
            pdf.drawString(MARGIN, state["y"], "Nobody was on the schedule.")
            state["y"] -= NAME_HEIGHT + (6 * MM)
            continue

        # Down the columns rather than across, so a list of names reads in
        # alphabetical order the way a person scans it.
        rows = -(-len(week.servers) // NAME_COLUMNS)
        for index, server in enumerate(week.servers):
            column, row = divmod(index, rows)
            x = MARGIN + (column * column_width)
            y = state["y"] - (row * NAME_HEIGHT)

            if y < MARGIN:
                continue

            pdf.setFillColorRGB(*INK)
            pdf.setFont("Helvetica", 8.5)
            name = clipped(server.name, "Helvetica", 8.5, column_width - (6 * MM))
            pdf.drawString(x, y, name)

            if server.role:
                pdf.setFillColorRGB(*MUTED)
                pdf.setFont("Helvetica", 7.2)
                used = pdf.stringWidth(name, "Helvetica", 8.5)
                pdf.drawString(
                    x + used + (2 * MM), y,
                    clipped(server.role, "Helvetica", 7.2,
                            column_width - used - (8 * MM)),
                )

        state["y"] -= (rows * NAME_HEIGHT) + (6 * MM)

    # The one note this sheet needs, for the same reason the roster report
    # carries four: the column is a judgement about what counts.
    if not room_for(12 * MM):
        new_page()
    state["y"] -= 2 * MM
    pdf.setStrokeColorRGB(*RULE)
    pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
    state["y"] -= 5 * MM
    pdf.setFillColorRGB(*MUTED)
    pdf.setFont("Helvetica", 7.2)
    for line in (
        "Volunteers: everybody who was on the schedule and did not decline. "
        "The same rule the roster report uses for last served.",
        "Head count: what somebody entered on the service plan. A Sunday "
        "nobody counted says so rather than showing a zero.",
    ):
        pdf.drawString(MARGIN, state["y"], line)
        state["y"] -= 3.4 * MM

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
