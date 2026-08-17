"""Every word the shell renders lives here, not in a template.

Copy edits never touch markup. A change to a nav label, a section heading, or
a placeholder line is a change to a Python list in this file.

`increment` on a nav item is the build increment that turns that item from a
placeholder into a working screen, per spec v3 section D.1. It is shown in the
UI on purpose: a pastor walking the shell should be able to see what is
finished and what is scheduled, rather than clicking into empty rooms.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    group: str
    increment: int
    icon: str
    ready: bool = False


# Groups render in this order.
NAV_GROUPS = ["Lead", "Run", "Manage"]

NAV_ITEMS: list[NavItem] = [
    NavItem("dashboard", "Dashboard", "Lead", 3, "dash", ready=False),
    NavItem("people", "People", "Lead", 2, "people"),
    NavItem("services", "Services", "Run", 10, "services"),
    NavItem("kids", "Kids", "Run", 11, "kids"),
    NavItem("giving", "Giving", "Run", 7, "giving"),
    NavItem("resources", "Resources", "Run", 6, "resources"),
    NavItem("messages", "Messages", "Manage", 12, "messages"),
    NavItem("settings", "Settings", "Manage", 15, "settings"),
]

INCREMENT_NAMES = {
    0: "Foundation and tenancy",
    1: "Identity and roles",
    2: "People, households, stages",
    3: "Stuck engine, next steps, contact log",
    4: "Outbox and notification preferences",
    5: "Member app shell",
    6: "Resources, reader, progress",
    7: "Tithely link out",
    8: "Bible, NIV via YouVersion with WEB fallback",
    9: "Groups",
    10: "Services, songs, teams",
    11: "Kids check in",
    12: "Messaging",
    13: "Tithely read only sync",
    14: "Sequences and automations",
    15: "Settings, support, audit surface",
}


# Icons, lifted verbatim from the approved interactive demo so the shell
# and the demo are visually identical. Stroke color is inherited, so these
# carry no brand information and do not violate the token rule.
ICONS: dict[str, str] = {
    "dashboard": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="7" height="9" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/><rect x="3" y="16" width="7" height="5" rx="2"/></svg>'
    ),
    "people": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5.5a3 3 0 010 5.6"/><path d="M18 14.5c2 .8 3 2.6 3 5"/></svg>'
    ),
    "services": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M8 4v5"/></svg>'
    ),
    "kids": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="8" r="3.4"/><path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6"/></svg>'
    ),
    "giving": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 21s-7-4.4-7-9.4A4.1 4.1 0 0112 9a4.1 4.1 0 017 2.6c0 5-7 9.4-7 9.4z"/></svg>'
    ),
    "resources": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 5.5A2 2 0 016 4h5v16H6a2 2 0 01-2-2z"/><path d="M20 5.5A2 2 0 0018 4h-5v16h5a2 2 0 002-2z"/></svg>'
    ),
    "messages": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M20 15a3 3 0 01-3 3H8l-4 3V6a3 3 0 013-3h10a3 3 0 013 3z"/></svg>'
    ),
    "settings": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.7 1.1 2 2 0 11-4 0 1.6 1.6 0 00-2.7-1.1l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.6 1.6 0 003 15a2 2 0 010-4 1.6 1.6 0 001.1-2.7l-.1-.1a2 2 0 112.8-2.8l.1.1A1.6 1.6 0 009.6 4.4a2 2 0 014 0 1.6 1.6 0 002.7 1.1l.1-.1a2 2 0 112.8 2.8l-.1.1A1.6 1.6 0 0021 11a2 2 0 010 4z"/></svg>'
    ),
}

SHELL = {
    "title": "Foundation",
    "subtitle": "Increment 0 is live. The shell, the tenant, and the brand.",
    "proof_heading": "What this page proves",
    "proof_intro": (
        "Nothing here is hard coded to one church. This page is reading a "
        "single database row and rendering itself from it."
    ),
    "proof_points": [
        (
            "The tenant came from the address",
            "The host in your address bar resolved to one church row before any "
            "other code ran. An address that matches no church returns a 404 "
            "rather than guessing.",
        ),
        (
            "The brand came from that row",
            "Every color, both typefaces, and the logo above were read off the "
            "same row. No template in this codebase contains a hex code.",
        ),
        (
            "A second church is a second row",
            "No migration, no deploy, no branch. Run the seed command with a "
            "different slug and a second church exists.",
        ),
        (
            "Time is stored the same way everywhere",
            "Timestamps are written and read as aware UTC on both SQLite here "
            "and Postgres in production, so a comparison cannot fail in one "
            "environment and pass in the other.",
        ),
    ],
    "roadmap_heading": "What comes next",
    "roadmap_intro": (
        "Each item below becomes a working screen at the increment shown. The "
        "order is the approved build order."
    ),
    "placeholder_lead": "Not built yet.",
    "placeholder_body": (
        "This screen arrives at increment {increment}, {name}. The navigation "
        "item is here now so the shape of the finished product is visible "
        "while it is being built."
    ),
}

ERRORS = {
    "404_title": "Nothing at this address",
    "404_body": (
        "No church is configured for this address, or the page has moved. "
        "Check the address, or go back to the dashboard."
    ),
    "500_title": "Something broke on our end",
    "500_body": (
        "The page could not be loaded. Nothing you were working on was lost. "
        "Try again in a moment, and if it keeps happening, email "
        "isaac@betweensundaysconsulting.com."
    ),
}
