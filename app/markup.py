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
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

# Applied after escaping, so the pattern can only ever match literal text the
# author typed. It cannot match generated markup, because none exists yet.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S)

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
      blank line separates paragraphs
      **bold** and *italic* inline
    """
    if not source:
        return Markup("")

    # Escape once, at the top, before anything else looks at the text.
    safe = str(escape(source[:MAX_LENGTH])).replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    open_list = False

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            out.append("</ul>")
            open_list = False

    for block in safe.split("\n\n"):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        # A block of list items becomes one list.
        if all(line.startswith("- ") for line in lines):
            close_list()
            out.append("<ul>")
            for line in lines:
                out.append(f"<li>{_inline(line[2:].strip())}</li>")
            out.append("</ul>")
            continue

        if all(line.startswith("&gt; ") for line in lines):
            close_list()
            joined = "<br>".join(_inline(line[5:].strip()) for line in lines)
            out.append(f"<blockquote>{joined}</blockquote>")
            continue

        if len(lines) == 1 and lines[0].startswith("# "):
            close_list()
            out.append(f"<h3>{_inline(lines[0][2:].strip())}</h3>")
            continue

        close_list()
        joined = "<br>".join(_inline(line) for line in lines)
        out.append(f"<p>{joined}</p>")

    close_list()
    return Markup("".join(out))


def plain(source: str | None, limit: int = 180) -> str:
    """A short unformatted excerpt, for lists and previews."""
    if not source:
        return ""
    text = re.sub(r"[#>*\-]", "", source).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"
