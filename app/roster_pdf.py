"""The roster report as a printed sheet.

A table of people with four dates each, grouped by stage, on US Letter
landscape because seven columns on portrait leaves the household column
three characters wide.

**Why a PDF and not the screen.** This one goes into a staff meeting. The
screen already does the sortable, clickable version of this; what it cannot
do is sit on a table in front of four people who are deciding who to call
this week. That is the whole brief, and it is why the page is built to be
read on paper: a line under every row, the date columns right where the eye
expects them, and a gap of more than a year said in words rather than left
as a date somebody has to do arithmetic on.

Drawn with the canvas directly, like `app/labels.py`, rather than with
Platypus tables. The layout is a fixed grid of columns and a page break on
a counter; the flowable machinery is for documents whose shape is not known
in advance, and it would be more code here, not less.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.volunteers import (
    BUSY_SUNDAY_SHARE,
    CONCENTRATION_FLAG,
    CONCENTRATION_SHARE,
    LONG_STREAK,
    MANY_POSITIONS,
    WINDOW_MONTHS,
)

MM = 72.0 / 25.4

# US Letter, landscape. The church prints on what is in the tray.
PAGE_WIDTH = 11 * 72.0
PAGE_HEIGHT = 8.5 * 72.0

MARGIN = 14 * MM
HEADER_HEIGHT = 20 * MM
ROW_HEIGHT = 6.6 * MM

# Column widths as a share of the usable width. Name and household get the
# room because they are the only two that vary in length; a date is a date.
COLUMNS = (
    ("Name", 0.22),
    ("Household", 0.21),
    ("Kids last in", 0.14),
    ("Last served", 0.14),
    ("Last contact", 0.14),
    ("Quietest", 0.15),
)

INK = (0.09, 0.10, 0.08)
MUTED = (0.42, 0.45, 0.40)
RULE = (0.85, 0.86, 0.83)
BAND = (0.95, 0.96, 0.94)


def _hex_to_rgb(value: str | None, fallback=(0.28, 0.36, 0.22)):
    """The church's accent, for the one bar of colour on the page."""
    if not value:
        return fallback
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return fallback


def _age(value: datetime | None, now: datetime) -> int | None:
    """Whole days since something, or None if it never happened."""
    if value is None:
        return None
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, (now - when).days)


def _said(value: datetime | None, now: datetime, fmt) -> str:
    """A date, with how long ago under it when that is the point.

    A bare date makes the reader do subtraction on every row. The report
    exists to answer "how long has it been", so it answers it.
    """
    if value is None:
        return "never"
    days = _age(value, now)
    if days is None:
        return "never"
    if days == 0:
        return "today"
    if days < 14:
        return f"{fmt(value)}  ({days}d)"
    if days < 365:
        return f"{fmt(value)}  ({days // 7}w)"
    return f"{fmt(value)}  ({days // 365}y+)"


def _count(number: int, noun: str) -> str:
    """"1 day", not "1 days". It is a sheet people read, not a log."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _quietest(row, now: datetime) -> tuple[str, bool]:
    """How long since ANY of the three things happened, and whether to flag it.

    The column the report is really for. Three dates across a row is three
    things to compare; this is the one number that says how long this person
    has been out of contact with the church by every measure it holds.

    Flagged past a year, because that is the threshold at which a church
    would say it has lost touch rather than that somebody has been quiet.
    """
    ages = [
        _age(value, now) for value in
        (row.last_kid_checkin, row.last_served, row.last_contact)
        if value is not None
    ]
    if not ages:
        return "no record", True
    days = min(ages)
    if days == 0:
        return "today", False
    if days < 14:
        return _count(days, "day"), False
    if days < 365:
        return _count(days // 7, "week"), days >= 180
    return _count(days // 365, "year") + "+", True


def render_roster(
    *, sections, church, fmt, now=None, subtitle=None, volunteers=None
) -> bytes:
    """The sheet.

    `fmt` formats a UTC datetime on the church's clock, passed in rather
    than imported so this file does nothing with time zones itself and can
    be tested with a fixed formatter.

    `volunteers` is an optional `app.volunteers.VolunteerReport`. When it is
    there the same file continues into the volunteer pages, because the
    request was one button and one document: a roster and a volunteer report
    that arrive as two downloads are two documents somebody has to staple
    together before a meeting.
    """
    from io import BytesIO

    from reportlab.pdfgen import canvas

    now = now or datetime.now(timezone.utc)
    accent = _hex_to_rgb(getattr(church, "accent_hex", None))

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle(f"{church.name} roster report")
    pdf.setAuthor(church.name)

    usable = PAGE_WIDTH - (2 * MARGIN)
    xs, running = [], MARGIN
    for _, share in COLUMNS:
        xs.append(running)
        running += usable * share

    # The title and strap live in state because the volunteer half of the
    # document changes both. A page headed "roster" over a table of burnout
    # flags is the kind of thing nobody notices until it is printed.
    state = {
        "page": 0,
        "y": 0.0,
        "title": f"{church.name} roster",
        "strap": subtitle or "Everyone, by stage",
    }

    def new_page():
        if state["page"]:
            pdf.showPage()
        state["page"] += 1

        pdf.setFillColorRGB(*accent)
        pdf.rect(0, PAGE_HEIGHT - (4 * MM), PAGE_WIDTH, 4 * MM, stroke=0, fill=1)

        top = PAGE_HEIGHT - MARGIN
        pdf.setFillColorRGB(*INK)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(MARGIN, top - (5 * MM), state["title"])

        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica", 8.5)
        line = state["strap"]
        pdf.drawString(MARGIN, top - (10 * MM), line)
        pdf.drawRightString(
            PAGE_WIDTH - MARGIN, top - (10 * MM),
            f"{fmt(now)}  ·  page {state['page']}",
        )
        state["y"] = top - HEADER_HEIGHT

    def column_heads():
        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica-Bold", 7)
        for (title, _), x in zip(COLUMNS, xs):
            pdf.drawString(x, state["y"], title.upper())
        state["y"] -= 2.2 * MM
        pdf.setStrokeColorRGB(*RULE)
        pdf.setLineWidth(0.6)
        pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
        state["y"] -= ROW_HEIGHT * 0.75

    def room_for(points: float) -> bool:
        return state["y"] - points > MARGIN

    def clipped(text: str, font: str, points: float, width: float) -> str:
        """Cut to fit, with an ellipsis, so nothing runs into the next column."""
        text = text or ""
        if pdf.stringWidth(text, font, points) <= width:
            return text
        while text and pdf.stringWidth(text + "…", font, points) > width:
            text = text[:-1]
        return text + "…" if text else ""

    new_page()

    for section in sections:
        # Room for the heading, the column heads AND two rows. Checking for
        # the heading alone let a section title and its column rule print at
        # the foot of a page with every one of its people on the next one,
        # which is how "Volunteer, 33 people" appeared above nothing.
        if not room_for((18 * MM) + (2 * ROW_HEIGHT)):
            new_page()

        pdf.setFillColorRGB(*INK)
        pdf.setFont("Helvetica-Bold", 10.5)
        pdf.drawString(MARGIN, state["y"], section.label)
        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(
            MARGIN + pdf.stringWidth(section.label, "Helvetica-Bold", 10.5) + (3 * MM),
            state["y"],
            f"{section.count} {'person' if section.count == 1 else 'people'}",
        )
        state["y"] -= 5.5 * MM
        column_heads()

        if not section.rows:
            pdf.setFillColorRGB(*MUTED)
            pdf.setFont("Helvetica-Oblique", 8.5)
            pdf.drawString(MARGIN, state["y"], "Nobody at this stage.")
            state["y"] -= ROW_HEIGHT * 1.6
            continue

        for index, row in enumerate(section.rows):
            if not room_for(ROW_HEIGHT):
                new_page()
                # Say which stage again. A page of names under bare column
                # heads is a page the reader has to flip back from to find
                # out what they are looking at.
                pdf.setFillColorRGB(*INK)
                pdf.setFont("Helvetica-Bold", 10.5)
                pdf.drawString(MARGIN, state["y"], f"{section.label} (continued)")
                state["y"] -= 5.5 * MM
                column_heads()

            if index % 2:
                pdf.setFillColorRGB(*BAND)
                pdf.rect(MARGIN - (1.5 * MM), state["y"] - (1.8 * MM),
                         usable + (3 * MM), ROW_HEIGHT, stroke=0, fill=1)

            widths = [usable * share - (3 * MM) for _, share in COLUMNS]
            quiet, flagged = _quietest(row, now)

            cells = (
                (row.name, "Helvetica-Bold", INK),
                # A person with no household says so, rather than leaving a
                # gap that reads as missing data.
                (row.household or "No household",
                 "Helvetica", INK if row.household else MUTED),
                (_said(row.last_kid_checkin, now, fmt), "Helvetica", MUTED),
                (_said(row.last_served, now, fmt), "Helvetica", MUTED),
                (_said(row.last_contact, now, fmt), "Helvetica", MUTED),
                (quiet, "Helvetica-Bold" if flagged else "Helvetica",
                 accent if flagged else MUTED),
            )

            for (text, font, colour), x, width in zip(cells, xs, widths):
                pdf.setFillColorRGB(*colour)
                pdf.setFont(font, 8.2)
                pdf.drawString(x, state["y"], clipped(text, font, 8.2, width))

            state["y"] -= ROW_HEIGHT

        state["y"] -= 4 * MM

    # The legend. Every column on this sheet is a judgement call about what
    # counts, and a reader deciding who to call deserves to know which ones.
    if not room_for(16 * MM):
        new_page()
    state["y"] -= 2 * MM
    pdf.setStrokeColorRGB(*RULE)
    pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
    state["y"] -= 5 * MM
    pdf.setFillColorRGB(*MUTED)
    pdf.setFont("Helvetica", 7.2)
    for line in (
        "Kids last in: the most recent check-in by any child of that "
        "household. Blank for anyone with no household on file.",
        "Last served: the most recent service they were scheduled for and "
        "did not decline. Services still to come are not counted.",
        "Last contact: the most recent contact any staff member wrote down, "
        "by any method.",
        "Quietest: time since the most recent of those three. Marked once it "
        "passes six months.",
    ):
        pdf.drawString(MARGIN, state["y"], line)
        state["y"] -= 3.4 * MM

    if volunteers is not None:
        _volunteer_pages(
            pdf=pdf, report=volunteers, church=church, accent=accent,
            state=state, new_page=new_page, room_for=room_for,
            clipped=clipped, usable=usable,
        )

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# How the volunteer tables are laid out. Fractions of the usable width, so
# the sheet reflows if the margins ever change.
VOLUNTEER_COLUMNS = (
    ("Name", 0.26),
    ("Shifts", 0.08),
    ("Months", 0.08),
    ("Roles", 0.26),
    ("Last served", 0.14),
    ("Regular", 0.18),
)

TREND_COLUMNS = (
    ("Month", 0.18),
    ("Volunteers", 0.14),
    ("Shifts", 0.12),
    ("Shifts each", 0.14),
    ("On the month before", 0.21),
    ("On the same month last year", 0.21),
)


def _roles(positions) -> str:
    """The roles somebody covers, with the count in front once it matters.

    `MANY_POSITIONS` is the burnout threshold, so at or above it the number
    is the point and the list is detail. Below it the list is short enough to
    read whole and a count in front would be noise.
    """
    if not positions:
        return "Not recorded"
    joined = ", ".join(positions)
    if len(positions) >= MANY_POSITIONS:
        return f"{len(positions)}: {joined}"
    return joined


def _delta(now: int, before: int | None) -> str:
    """A change, or an honest blank.

    None means there is nothing to compare against, which is different from
    no change and has to read differently or a church in its first year sees
    a column of zeroes and concludes it is flat.
    """
    if before is None:
        return ""
    if now == before:
        return "no change"
    return f"{'+' if now > before else ''}{now - before}"


def _volunteer_pages(
    *, pdf, report, church, accent, state, new_page, room_for, clipped, usable
):
    """The volunteer half of the document.

    Shares the canvas and the page machinery with the roster above it, so
    page numbering runs straight through and the reader gets one document.
    """
    state["title"] = f"{church.name} volunteers"
    state["strap"] = f"Serving from {report.window_label}"
    new_page()

    def heading(text, note=""):
        if not room_for((16 * MM) + (2 * ROW_HEIGHT)):
            new_page()
        pdf.setFillColorRGB(*INK)
        pdf.setFont("Helvetica-Bold", 10.5)
        pdf.drawString(MARGIN, state["y"], text)
        if note:
            pdf.setFillColorRGB(*MUTED)
            pdf.setFont("Helvetica", 8.5)
            pdf.drawString(
                MARGIN + pdf.stringWidth(text, "Helvetica-Bold", 10.5) + (3 * MM),
                state["y"], note,
            )
        state["y"] -= 5.5 * MM

    def heads(columns):
        xs, running = [], MARGIN
        for _, share in columns:
            xs.append(running)
            running += usable * share
        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica-Bold", 7)
        for (title, _), x in zip(columns, xs):
            pdf.drawString(x, state["y"], title.upper())
        state["y"] -= 2.2 * MM
        pdf.setStrokeColorRGB(*RULE)
        pdf.setLineWidth(0.6)
        pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
        state["y"] -= ROW_HEIGHT * 0.75
        return xs

    def draw_row(cells, xs, columns, index):
        if index % 2:
            pdf.setFillColorRGB(*BAND)
            pdf.rect(MARGIN - (1.5 * MM), state["y"] - (1.8 * MM),
                     usable + (3 * MM), ROW_HEIGHT, stroke=0, fill=1)
        widths = [usable * share - (3 * MM) for _, share in columns]
        for (text, font, colour), x, width in zip(cells, xs, widths):
            pdf.setFillColorRGB(*colour)
            pdf.setFont(font, 8.2)
            pdf.drawString(x, state["y"], clipped(str(text), font, 8.2, width))
        state["y"] -= ROW_HEIGHT

    def empty(text):
        pdf.setFillColorRGB(*MUTED)
        pdf.setFont("Helvetica-Oblique", 8.5)
        pdf.drawString(MARGIN, state["y"], text)
        state["y"] -= ROW_HEIGHT * 1.6

    def people_table(rows, note_for=None):
        xs = heads(VOLUNTEER_COLUMNS)
        for index, row in enumerate(rows):
            if not room_for(ROW_HEIGHT):
                new_page()
                xs = heads(VOLUNTEER_COLUMNS)
            regular = note_for(row) if note_for else (
                "Yes" if row.is_flagged else "Not ticked"
            )
            draw_row(
                (
                    (row.name, "Helvetica-Bold", INK),
                    (row.shifts, "Helvetica", INK),
                    (f"{row.months} of {WINDOW_MONTHS}", "Helvetica", MUTED),
                    # The count first once somebody is over the roles
                    # threshold. A long list gets an ellipsis, and on the one
                    # page that is about workload, "Cameras, Coffee, Kids,
                    # Sound, Welcome, Wo…" loses the only number a reader
                    # needs: how many. Below the threshold the list speaks
                    # for itself and a count would be noise.
                    (_roles(row.positions), "Helvetica",
                     INK if row.positions else MUTED),
                    (row.last_served.strftime("%b %-d") if row.last_served
                     else "Not in this window", "Helvetica", MUTED),
                    (regular, "Helvetica-Bold" if not row.is_flagged else "Helvetica",
                     accent if not row.is_flagged else MUTED),
                ),
                xs, VOLUNTEER_COLUMNS, index,
            )
        state["y"] -= 4 * MM

    # 1. The four numbers somebody reads out loud.
    monthly = report.monthly
    heading("At a glance")
    pdf.setFillColorRGB(*MUTED)
    pdf.setFont("Helvetica", 8.5)
    for line in (
        f"{len(report.active)} people served at least once, across "
        f"{report.weekends} weekend{'' if report.weekends == 1 else 's'} "
        f"with a service.",
        f"{len(monthly)} of them served in all {WINDOW_MONTHS} months, which "
        f"is the bar this sheet uses for serving at least monthly.",
        f"{report.total_shifts} shifts in total.",
    ):
        pdf.drawString(MARGIN, state["y"], line)
        state["y"] -= 4.2 * MM
    state["y"] -= 3 * MM

    # 2. The monthly regulars.
    heading("Serving at least monthly", f"{len(monthly)} people")
    if monthly:
        people_table(monthly)
    else:
        empty("Nobody served in all three months.")

    # 3. The actionable list, and the one with teeth: ticking the box is what
    #    moves somebody to Disciple on the rail.
    should = report.should_be_flagged
    heading(
        "Should be ticked as a regular volunteer",
        f"{len(should)} people" if should else "",
    )
    if should:
        people_table(should, note_for=lambda row: "Tick this one")
    else:
        empty("Everybody serving monthly is already ticked.")

    # 4. The opposite direction, which nobody goes looking for.
    stale = report.stale_flags
    heading("Ticked, but not serving", f"{len(stale)} people" if stale else "")
    if stale:
        people_table(stale, note_for=lambda row: "Still ticked")
    else:
        empty("Every ticked volunteer has served in this window.")

    # 5. Workload.
    heading("Workload")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColorRGB(*(accent if report.is_concentrated else MUTED))
    share = round(report.concentration * 100)
    pdf.drawString(
        MARGIN, state["y"],
        f"The busiest {len(report.busiest)} of {len(report.active)} volunteers "
        f"cover {share}% of all shifts."
        + ("  That is a rota resting on a few people." if report.is_concentrated else ""),
    )
    state["y"] -= 5.5 * MM

    at_risk = report.at_risk
    if at_risk:
        xs = heads((("Name", 0.26), ("Shifts", 0.1), ("Why they are flagged", 0.64)))
        for index, row in enumerate(at_risk):
            if not room_for(ROW_HEIGHT):
                new_page()
                xs = heads((("Name", 0.26), ("Shifts", 0.1),
                            ("Why they are flagged", 0.64)))
            draw_row(
                (
                    (row.name, "Helvetica-Bold", INK),
                    (row.shifts, "Helvetica", INK),
                    ("; ".join(row.reasons), "Helvetica", accent),
                ),
                xs, (("Name", 0.26), ("Shifts", 0.1), ("Why", 0.64)), index,
            )
        state["y"] -= 4 * MM
    else:
        empty("Nobody is over any of the thresholds below.")

    # 6. The trend.
    heading("Month by month", f"last {len(report.months)} months")
    xs = heads(TREND_COLUMNS)
    for index, point in enumerate(report.months):
        if not room_for(ROW_HEIGHT):
            new_page()
            xs = heads(TREND_COLUMNS)
        before = report.months[index - 1] if index else None
        a_year = report.months[index - 12] if index >= 12 else None
        last = index == len(report.months) - 1
        draw_row(
            (
                (point.label + (" (so far)" if last else ""),
                 "Helvetica-Bold" if last else "Helvetica", INK),
                (point.volunteers, "Helvetica", INK),
                (point.shifts, "Helvetica", MUTED),
                (f"{point.per_volunteer:.1f}" if point.volunteers else "",
                 "Helvetica", MUTED),
                (_delta(point.volunteers, before.volunteers if before else None),
                 "Helvetica", MUTED),
                (_delta(point.volunteers, a_year.volunteers if a_year else None),
                 "Helvetica", MUTED),
            ),
            xs, TREND_COLUMNS, index,
        )
    state["y"] -= 4 * MM

    # 7. What every number on these pages means.
    if not room_for(26 * MM):
        new_page()
    pdf.setStrokeColorRGB(*RULE)
    pdf.line(MARGIN, state["y"], PAGE_WIDTH - MARGIN, state["y"])
    state["y"] -= 5 * MM
    pdf.setFillColorRGB(*MUTED)
    pdf.setFont("Helvetica", 7.2)
    for line in (
        "Served: scheduled for a service that has already happened and did "
        "not decline it. Being invited counts; declining does not. The same "
        "rule as the Last served column on the roster.",
        f"Serving at least monthly: served in all {WINDOW_MONTHS} of the "
        "months in this window. Counted as distinct months, not shifts, so "
        "three shifts in one week is one month.",
        "Should be ticked: serving monthly with the Regular volunteer box "
        "still off. Ticking it, alongside Regular giver, is what moves "
        "somebody to Disciple.",
        f"Flagged for workload: on {round(BUSY_SUNDAY_SHARE * 100)}% or more "
        f"of the weekends that had a service, or covering {MANY_POSITIONS} "
        f"or more roles, or {LONG_STREAK} or more weekends in a row.",
        f"Concentrated: the busiest {round(CONCENTRATION_SHARE * 100)}% of "
        f"volunteers covering {round(CONCENTRATION_FLAG * 100)}% or more of "
        "the shifts.",
        "Every date is on this church's own clock, not the server's.",
    ):
        pdf.drawString(MARGIN, state["y"], line)
        state["y"] -= 3.4 * MM
