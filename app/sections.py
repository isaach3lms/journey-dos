"""The pages inside a section, as a list rather than as markup.

**What this replaces.** The People screen carried a row of outline buttons:
connect cards, account requests, sign-ups, prayer check-ins. Four of the most
used pages in the product, rendered to look exactly like the Download report
button beside them and exactly like every other button on every other screen.

A button says "this does something". A tab says "this is one of the places you
can be, and you are here". Those pages are places, and showing them as buttons
left somebody no way to tell what People contains without clicking each one and
coming back. Worse, once on one of those pages there was nothing saying the
other three existed, so each was a dead end you reversed out of.

**Why the counts stay on them.** They were on the buttons for a good reason,
written down at the time: somebody asking to be baptised is the most important
message this product carries, and a page you have to open to learn whether it
is empty is a page that gets opened on Wednesdays. A tab strip carrying the
numbers answers that from anywhere in the section.

**Why this is Python and not a template.** Same reason the copy is: a church
changing what its sections are called, or a build adding a page, should not be
editing markup to do it. The template walks whatever it is handed.

**Destinations only.** Download report is not here. It opens a PDF in a new
tab, which is an action rather than a place, and mixing the two is how the
button row stopped meaning anything in the first place. It belongs with the
other actions on the top line.
"""

from __future__ import annotations

from flask import url_for

# label is a key into the PEOPLE copy, not a string, so the wording stays in
# one place. count is the name of the figure a route may hand over for the
# badge; None means this tab never carries one.
PEOPLE_TABS = (
    ("people.index", "tab_roster", None),
    ("people.guests", "tab_guests", "guest_cards"),
    ("people.requests", "requests_view", "account_requests"),
    ("people.signups", "signups_view", "signup_count"),
    ("people.prayer_list", "prayer_list_title", "prayer_count"),
)


def tabs_for(spec, copy, current: str, counts=None) -> list[dict]:
    """One section's pages, ready for the template.

    `current` is an endpoint name rather than an index, because a section
    gains and loses pages and a positional answer silently marks the wrong
    one when that happens.

    A tab whose endpoint does not resolve is dropped rather than raised on. A
    section strip is navigation furniture, and taking a whole screen down
    because one destination moved is a worse failure than showing four tabs
    where there were five.
    """
    counts = counts or {}
    out = []

    for endpoint, key, count_name in spec:
        try:
            href = url_for(endpoint)
        except Exception:  # noqa: BLE001
            continue

        count = counts.get(count_name) if count_name else None
        out.append({
            "href": href,
            "label": copy.get(key, key),
            # Zero is not shown. An empty inbox is the normal state and a row
            # of noughts reads as broken rather than as finished.
            "count": count if count else None,
            "current": endpoint == current,
        })

    return out


def people_tabs(copy, current: str, counts=None) -> list[dict]:
    return tabs_for(PEOPLE_TABS, copy, current, counts)
