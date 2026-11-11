"""The things a church invites somebody to do next.

A tuple, not a table, for the same reason stages and sequences are one: these
are shape rather than content. Adding Child Dedication is four lines here and
a deploy. Making it a table would buy a form for editing them and cost a
migration, a per-church editor, and the certainty that two churches end up
with "Bapstism" in production.

**Every field here exists because a screen needs it and none of it is
decoration.** The tile shows `title` and `subtitle`; the form shows `heading`
and `blurb`; the question on the form is `prompt`, and it differs per offer
because "is there anything you would like us to know" is the wrong question
to ask somebody volunteering and the right one to ask somebody asking to be
baptised.

**The tint is content, not theme.** Brand tokens in app/brand.py stay the one
theming lever and nothing here touches them: a church's accent colour is its
accent colour, while these are category colours that tell six tiles apart at
a glance, the way a calendar colours its calendars. They are fixed across
churches on purpose, so that a screenshot of one church's app teaches you
where things are in another's.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    code: str
    # The tile.
    title: str
    subtitle: str
    # A key into ICONS below, kept separate from the code so two offers can
    # share a glyph without sharing an identity.
    icon: str
    tint: str
    # The form.
    heading: str
    blurb: str
    prompt: str
    placeholder: str
    # What staff see in a notification and on their list. Written here rather
    # than built from the title at the send site, so the wording a pastor
    # reads at 9pm is in the same file as the wording the member tapped.
    staff_line: str


OFFERS: tuple[Offer, ...] = (
    Offer(
        code="baptism",
        title="Baptism",
        subtitle="Sign up today",
        icon="water",
        tint="#2f7fd1",
        heading="Get baptised",
        blurb=(
            "Tell us you are ready and somebody will be in touch to talk it "
            "through and find a date. Nothing is booked by filling this in."
        ),
        prompt="Anything you would like us to know?",
        placeholder="Optional. Where you are up to, questions, anything.",
        staff_line="asked about being baptised",
    ),
    Offer(
        code="volunteer",
        title="Volunteer",
        subtitle="Join a team",
        icon="hand",
        # Darkened from #d1702f when the tiles went grey. On the old cream it
        # cleared 3:1 against its background; on the grey it did not, and an
        # icon that has gone muddy is the one thing on the tile somebody sees
        # before they read anything.
        tint="#C2611E",
        heading="Join a team",
        blurb=(
            "Tell us you are interested and somebody will be in touch about "
            "where you might fit. No commitment yet."
        ),
        prompt="Where would you like to serve?",
        placeholder="Optional. Kids, worship, hospitality, wherever needed.",
        staff_line="asked about volunteering",
    ),
)

BY_CODE: dict[str, Offer] = {offer.code: offer for offer in OFFERS}
CODES: tuple[str, ...] = tuple(offer.code for offer in OFFERS)


# ---------------------------------------------------------------------------
# The grid on the home screen.
#
# Not everything in that grid is an `Offer`. An offer is a one-time ask with a
# form, a sign-up row and a staff notification behind it; the weekly prayer
# check-in is none of those. It is the same kind of thing to a member looking
# at the screen, which is why it sits in the same grid, and a different thing
# to the code, which is why it is not an Offer with five fields left empty.
#
# `Tile` is only what the grid needs. Offers become tiles; the check-in is a
# tile of its own, and anything later that is a door rather than a form joins
# it here rather than being bolted onto Offer.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tile:
    code: str
    title: str
    subtitle: str
    icon: str
    tint: str
    # The view this tile opens.
    endpoint: str
    # Whether its URL carries the code, which is also whether "You have
    # asked" is a thing that can be true of it. An offer is asked for once;
    # the check-in is answered again every week.
    one_time: bool = True
    # Spans the whole row rather than one column. Three tiles in a
    # two-column grid leaves the third one alone beside half a row of
    # nothing, which looks like a tile failed to load.
    wide: bool = False


PRAYER_CODE = "prayer_checkin"

PRAYER_TILE = Tile(
    code=PRAYER_CODE,
    title="Weekly Prayer Check In",
    subtitle="Tell us how to pray this week",
    icon="candle",
    # A violet, because the other two tiles are blue and orange and the point
    # of the colour is telling three tiles apart at arm's length. 6.5:1 on the
    # tile grey; see the test that holds every tint to that floor.
    tint="#5B3FA8",
    endpoint="member.prayer",
    one_time=False,
    wide=True,
)


def tiles_for(church=None) -> tuple[Tile, ...]:
    """Everything in the home-screen grid, in the order it is drawn.

    The offers first, because they are what a church invites somebody to do,
    and the check-in last, because it is the one that comes round again.
    """
    offers = tuple(
        Tile(
            code=offer.code,
            title=offer.title,
            subtitle=offer.subtitle,
            icon=offer.icon,
            tint=offer.tint,
            endpoint="member.next_step",
        )
        for offer in offers_for(church)
    )
    return offers + (PRAYER_TILE,)


def url_for_tile(tile: Tile) -> str:
    """Where a tile goes.

    Here rather than in the template so the grid stays one loop with no
    branch in it, and so the difference between the two kinds of tile is
    stated once, in Python, where it can be tested.
    """
    from flask import url_for

    if tile.one_time:
        return url_for(tile.endpoint, code=tile.code)
    return url_for(tile.endpoint)


def offers_for(church=None) -> tuple[Offer, ...]:
    """Every read goes through here.

    The argument is unused today and deliberately present: it is the seam
    where per-church offers arrive without touching a call site, exactly as
    `stages_for` and `sequences_for` are for theirs.
    """
    return OFFERS


def get(code: str) -> Offer | None:
    return BY_CODE.get((code or "").strip().lower())


def label_for(code: str) -> str:
    """A name for a code, including one this deploy no longer offers.

    Rows outlive the tuple. An offer retired next year still has sign-ups in
    the table and on people's timelines, and those have to render as
    something a human can read rather than vanishing or crashing a list.
    """
    offer = get(code)
    return offer.title if offer else (code or "").replace("_", " ").title()


# ---------------------------------------------------------------------------
# The glyphs.
#
# Inline SVG paths rather than an icon font or a sprite file: there are two of
# them, they are tiny, and a font is a network request that can fail while the
# tile it was meant to label sits there empty. `currentColor` so the tint
# comes from CSS and the same path works on a dark background later.
# ---------------------------------------------------------------------------

ICONS: dict[str, str] = {
    # Water, for baptism. Three strokes rather than a drop: a drop reads as
    # a liquid, and this is about going under.
    "water": (
        "M2 7c2.5 0 2.5 2 5 2s2.5-2 5-2 2.5 2 5 2 2.5-2 5-2"
        "M2 13c2.5 0 2.5 2 5 2s2.5-2 5-2 2.5 2 5 2 2.5-2 5-2"
        "M2 19c2.5 0 2.5 2 5 2s2.5-2 5-2 2.5 2 5 2 2.5-2 5-2"
    ),
    # A raised hand, for volunteering. The one gesture that means "me".
    "hand": (
        "M9 11V5.5a1.5 1.5 0 0 1 3 0V11"
        "M12 11V4.5a1.5 1.5 0 0 1 3 0V11"
        "M15 11.5V7a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1.5a6 6 0 0 1-5-2.7"
        "L3.5 14a1.6 1.6 0 0 1 2.6-1.8L9 15.5"
    ),
    # A candle, for the weekly prayer check-in. Drawn and looked at next to
    # the other two at 22px before it was chosen: praying hands, which is the
    # obvious glyph, collapses into an arch at this size and stroke weight
    # and reads as a doorway. A speech bubble and a bare heart were both out
    # for a different reason, which is that the member tab bar already uses
    # them for Chat and Give.
    "candle": (
        "M12 2.2c2.2 2.5 3.3 4.1 3.3 5.6a3.3 3.3 0 0 1-6.6 0"
        "c0-1.5 1.1-3.1 3.3-5.6z"
        "M6.2 21v-8.1a2.2 2.2 0 0 1 2.2-2.2h7.2a2.2 2.2 0 0 1 2.2 2.2V21"
        "M3.4 21h17.2"
    ),
}


def icon_path(name: str) -> str:
    """The path data for a glyph, or an empty string.

    Empty rather than raising: a tile with no icon is a tile, and a missing
    glyph is not worth a 500 on somebody's home screen.
    """
    return ICONS.get(name, "")
