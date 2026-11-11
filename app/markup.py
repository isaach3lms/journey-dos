"""Rendering staff-written content for members.

Everything a church types into a reading plan is displayed in somebody else's
browser. That makes it untrusted input, even though the person typing it is a
pastor. A leader account is one weak password away from an outsider, and a
stored script in a member's browser is a far worse outcome than a bold tag that
does not render.

So: **escape everything first, then re-introduce a fixed set of formatting.**
The inverse order, formatting then trying to strip what looks dangerous, is the
approach that keeps producing XSS advisories, because the strip step has to
anticipate every trick and the escape step has to anticipate nothing.

Deliberately not Markdown. A full Markdown library accepts raw HTML by default,
supports link targets that can carry `javascript:`, and brings a dependency
whose CVEs become this application's problem. Churches writing devotionals need
paragraphs, headings, bold, italics, lists, and blockquotes for scripture. That
is the whole grammar.

**Three kinds of list, because churches asked for three.** A bulleted list, a
numbered list for steps that happen in order, and a dashed list for the times
somebody wants a list that does not look like a list of instructions. They are
three markers rather than one marker and a style setting, because what you type
is then what you get, and because a style setting would have to be stored
somewhere and carried through saved templates and email rendering, which is a
lot of machinery to choose a character.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

# Applied after escaping, so the pattern can only ever match literal text the
# author typed. It cannot match generated markup, because none exists yet.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S)

# "1. ", "2) ", "12. ". Capped at three digits so a line that opens with a
# year, "2026 was the year we", is a sentence and not item two thousand.
_ORDERED = re.compile(r"^(\d{1,3})[.)] +(.*)$")

BULLET = "- "
DASH = "-- "

# What a dashed list actually prints. An en dash, as a literal character
# inside the list item, rather than a CSS marker: `list-style-type` with a
# custom string is ignored by most email clients, and a church's email is the
# main place these lists are read.
DASH_MARK = "– "

MAX_LENGTH = 20_000


def _inline(text: str) -> str:
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return text


def render(source: str | None) -> Markup:
    """Turn author text into safe HTML.

    Grammar, one per line:
      # heading
      > blockquote, used for scripture
      - list item
      -- dashed list item
      1. numbered list item
      blank line separates paragraphs
      **bold** and *italic* inline

    A list is a whole block: every line between two blank lines has to carry
    the same marker. One line in five starting with a dash inside a paragraph
    is somebody writing a sentence with a dash in it, and turning that into a
    one-item list in the middle of their paragraph is the kind of helpfulness
    that makes people stop using the formatting.
    """
    if not source:
        return Markup("")

    # Escape once, at the top, before anything else looks at the text.
    safe = str(escape(source[:MAX_LENGTH])).replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []

    for block in safe.split("\n\n"):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        # Dashed before bulleted. "-- a" does not start with "- ", so the
        # order is not load-bearing, but reading it in the other order is a
        # moment of doubt every time somebody comes back to this function.
        if all(line.startswith(DASH) for line in lines):
            items = "".join(
                f"<li>{DASH_MARK}{_inline(line[len(DASH):].strip())}</li>"
                for line in lines
            )
            out.append(f'<ul class="dashed">{items}</ul>')
            continue

        if all(line.startswith(BULLET) for line in lines):
            items = "".join(
                f"<li>{_inline(line[len(BULLET):].strip())}</li>" for line in lines
            )
            out.append(f"<ul>{items}</ul>")
            continue

        ordered = [_ORDERED.match(line) for line in lines]
        if all(ordered):
            # Numbered from whatever the author started at. Somebody
            # continuing a list after a paragraph writes "4." and means
            # four, and renumbering them to one is the renderer telling the
            # author they were wrong about their own document.
            first = int(ordered[0].group(1))
            start = f' start="{first}"' if first != 1 else ""
            items = "".join(
                f"<li>{_inline(match.group(2).strip())}</li>" for match in ordered
            )
            out.append(f"<ol{start}>{items}</ol>")
            continue

        if all(line.startswith("&gt; ") for line in lines):
            joined = "<br>".join(_inline(line[5:].strip()) for line in lines)
            out.append(f"<blockquote>{joined}</blockquote>")
            continue

        if len(lines) == 1 and lines[0].startswith("# "):
            out.append(f"<h3>{_inline(lines[0][2:].strip())}</h3>")
            continue

        joined = "<br>".join(_inline(line) for line in lines)
        out.append(f"<p>{joined}</p>")

    return Markup("".join(out))


def plain(source: str | None, limit: int = 180) -> str:
    """A short unformatted excerpt, for lists and previews."""
    if not source:
        return ""
    text = re.sub(r"[#>*\-]", "", source).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def render_message(source: str | None) -> Markup:
    """A chat message: escaped, with line breaks preserved and nothing else.

    Deliberately not the full `render` grammar. A person typing "- 5" in a
    conversation means minus five, not a bullet, and "# 1" means number one.
    Applying document formatting to chat rewrites what people said.

    Escaping still happens first, for the same reason it does everywhere else:
    the author is a member of the church, which is not the same as trusted.
    """
    if not source:
        return Markup("")

    safe = str(escape(source[:4000])).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [block.strip() for block in safe.split("\n\n") if block.strip()]
    return Markup(
        "".join(
            "<p>" + "<br>".join(line for line in block.split("\n")) + "</p>"
            for block in paragraphs
        )
    )
