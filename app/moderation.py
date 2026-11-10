"""Filtering what can be posted in chat.

The first of the three things App Store Guideline 1.2 asks of an app where
people post content others read: filter objectionable material before it is
posted, let people report what gets through, and let people block the person
who wrote it. Reporting and blocking live in app/models/moderation.py.

**A filter is the weakest of the three and is written to be honest about
that.** A word list catches the obvious and nothing else; somebody determined
to be cruel does not need a swear word. It exists so that the careless and the
impulsive are stopped at the keyboard rather than after twelve people have
read it, and the real protection is a human reading a report.

**The list is a church's list, not a general one.** "Hell" and "damn" are not
on it, because a church talks about hell, and a filter that rejects a message
quoting Matthew 10 is a filter people learn to route around. Words go on this
list when there is no ordinary church sentence that needs them.

**A refused message is not stored.** The person is told it was not posted and
why, and can rewrite it. Nothing is logged with the text, because a log of
things people almost said is not something a church should be keeping.
"""

from __future__ import annotations

import re

# Stems. Common endings are matched automatically, so "fuck" also catches
# "fucking" and "fucked" without listing each.
BLOCKED_STEMS: tuple[str, ...] = (
    "fuck",
    "motherfuck",
    "shit",
    "bullshit",
    "bitch",
    "bastard",
    "asshole",
    "cunt",
    "dick",
    "cock",
    "pussy",
    "whore",
    "slut",
    "piss",
    "twat",
    "wank",
)

_ENDINGS = r"(?:s|es|ed|er|ers|ing|in|y|ty|head|heads|face)?"

_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(stem) for stem in BLOCKED_STEMS) + r")" + _ENDINGS + r"\b",
    re.IGNORECASE,
)

# Letters people substitute to get past a filter. Normalised before matching
# so "sh1t" and "f*ck" are caught; the original text is what gets posted if
# nothing matches.
_SUBSTITUTIONS = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
    "$": "s", "!": "i", "*": "u",
})


def objectionable_terms(text: str | None) -> list[str]:
    """The blocked terms in `text`, or an empty list.

    Returns what matched rather than a yes or no, so the person can be told
    which word to change instead of guessing.
    """
    if not text:
        return []
    normalised = text.translate(_SUBSTITUTIONS)
    # Collapse letters repeated three or more times: "shiiiit" reads as "shit".
    normalised = re.sub(r"(\w)\1{2,}", r"\1", normalised)
    found = []
    for match in _PATTERN.finditer(normalised):
        term = match.group(0).lower()
        if term not in found:
            found.append(term)
    return found


def is_allowed(text: str | None) -> bool:
    return not objectionable_terms(text)
