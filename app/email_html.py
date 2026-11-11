"""The HTML half of an email.

Until now every email this platform sent was plain text. `OutboxMessage` has
carried a `body_html` column and the Resend transport has forwarded it since
the day both were written, and nothing ever filled it in. This module fills it
in, for every email, from the text the sender already typed.

**Why it is built here and not in each composer.** Two screens let staff write
an email, but about twenty places send one: a password reset, a pastoral
alert, a serving reminder, a kids pickup code. Adding an argument to each of
those is twenty chances to forget, and the one that gets forgotten is the one
a church notices. `app.notify.notify` calls this once, so an email is
formatted because it is an email, not because somebody remembered.

**The plain text part is untouched, deliberately.** Every message still
carries exactly the body it carried before, character for character, and
nothing here rewrites it. A client that prefers text sees what it saw last
week, markers and all, which reads the way a plain-text email with a list in
it has always read.

That is also what makes this safe to apply to every email at once rather than
rolling it out one screen at a time. The worst case for a message whose
wording happens to look like a marker is that it looks odd in HTML. It can
never be that a word went missing, because the other half of the message is
still the author's literal text, and a version of this that "tidied up" the
text half would have given up exactly that guarantee to save a reader four
asterisks.

**Why everything is an inline style.** Gmail strips `<style>` blocks in some
contexts and keeps them in others, Outlook's rendering engine is Word, and
nothing honours a stylesheet reliably. Classes stay on the markup for anybody
reading the source, but every rule that has to survive is written on the
element. There are no external stylesheets, no web fonts, and no images.

**And why no logo.** A church's logo lives behind the app's login. An email
that referenced it would either leak a URL that works for anybody who
receives the message, or show a broken image to everybody, depending on how
the files are served. The wordmark is set in text instead, in the church's
own accent colour, which cannot break and cannot leak.
"""

from __future__ import annotations

import re

from markupsafe import escape

from app.brand import palette_for
from app.markup import _ORDERED, render

# The width an email body is comfortable at. Not a percentage: Outlook treats
# a percentage width on the body as the full window, which on a desktop is a
# line of text eleven inches wide.
WIDTH = 600

# Every element the grammar can produce, with the style it carries. Written
# here rather than inline in the substitution so the two lists cannot drift:
# a tag added to app/markup.py and not to this table arrives in somebody's
# inbox with no styling at all, which is how an email ends up looking broken
# in exactly one client.
STYLES = {
    "p": "margin:0 0 14px;line-height:1.55",
    "h3": "margin:22px 0 8px;font-size:17px;line-height:1.3;font-weight:700",
    "ul": "margin:0 0 14px;padding-left:22px",
    "ol": "margin:0 0 14px;padding-left:22px",
    "li": "margin:0 0 6px;line-height:1.55",
    "strong": "font-weight:700",
    "em": "font-style:italic",
}

# A dashed list prints its own dash, so it must not also print a bullet.
# `list-style:none` with the padding left in place keeps it aligned with the
# bulleted list above it rather than jumping to the margin.
DASHED_UL = "margin:0 0 14px;padding-left:22px;list-style:none"

_TAG = re.compile(r"<(p|h3|ul|ol|li|strong|em|blockquote)(?=[ >])([^>]*)>")


def _style_for(tag: str, attributes: str, accent: str) -> str:
    if tag == "ul" and 'class="dashed"' in attributes:
        return DASHED_UL
    if tag == "blockquote":
        # Scripture, usually. A left rule in the church's own colour rather
        # than italics, because a long italic passage is hard to read and
        # because the rule survives a client that drops the border as a
        # plain indent, which still looks deliberate.
        return (
            f"margin:0 0 14px;padding:2px 0 2px 14px;"
            f"border-left:3px solid {accent};color:#44483f"
        )
    return STYLES.get(tag, "")


def inline_styles(html: str, accent: str) -> str:
    """Put a style attribute on every tag the grammar produced.

    Runs over markup this application generated a moment ago, never over
    anything a person typed: `app.markup.render` escapes its input before it
    writes a single tag, so by the time this function sees a `<p>` the only
    way it got there is that `render` put it there.
    """

    def swap(match: re.Match) -> str:
        tag, attributes = match.group(1), match.group(2)
        style = _style_for(tag, attributes, accent)
        if not style:
            return match.group(0)
        return f'<{tag}{attributes} style="{style}">'

    return _TAG.sub(swap, html)


def render_body(source: str, church) -> str:
    """One email, as a whole HTML document.

    A document rather than a fragment, because a fragment is what a client
    wraps in its own defaults, and Outlook's defaults are Times New Roman.
    """
    palette = palette_for(church)
    accent = getattr(church, "accent_hex", None) or palette.green
    body = inline_styles(str(render(source)), accent)

    # A sans stack with no web font. A font file an email client has to fetch
    # is a font file it will not fetch, and the fallback it lands on is worse
    # than the one named here.
    family = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(church.name)}</title></head>"
        f'<body style="margin:0;padding:0;background:{palette.bone};'
        f'-webkit-text-size-adjust:100%">'
        # A table, not a div. Outlook ignores max-width on a div, so the
        # one layout element in this document is the one element every
        # client has agreed on since 1998.
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{palette.bone}"><tr>'
        f'<td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="{WIDTH}" cellpadding="0" '
        f'cellspacing="0" border="0" '
        f'style="width:100%;max-width:{WIDTH}px;background:{palette.surface};'
        f'border-radius:12px"><tr>'
        f'<td style="padding:26px 26px 8px">'
        f'<div style="font:600 13px/1.2 {family};letter-spacing:.04em;'
        f'text-transform:uppercase;color:{accent}">{escape(church.name)}</div>'
        "</td></tr><tr>"
        f'<td style="padding:10px 26px 26px;font:400 15px/1.55 {family};'
        f'color:{palette.ink}">{body}</td>'
        "</tr></table></td></tr></table></body></html>"
    )


def looks_formatted(source: str | None) -> bool:
    """Whether the author used any of the grammar.

    Only used to decide what to tell somebody on a screen. It never decides
    whether to send the HTML part: every email gets one, because an email
    with no markers in it still wants a readable line height and a margin,
    and because "sometimes HTML, sometimes not" is two code paths through
    every message this church sends.
    """
    if not source:
        return False
    for line in source.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith(("- ", "-- ", "# ", "> ")) or _ORDERED.match(stripped):
            return True
    return "**" in source
