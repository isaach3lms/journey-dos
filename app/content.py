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

from app.models.user import ROLES


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    group: str
    increment: int
    icon: str
    # Which roles see this item at all. A member never renders a link to the
    # staff roster, so there is no link to guess at and no 403 to hit. The
    # route still checks the role; the nav is presentation, not enforcement.
    roles: frozenset = frozenset(ROLES)
    ready: bool = False


STAFF_ONLY = frozenset({"staff"})
STAFF_AND_LEADERS = frozenset({"staff", "leader"})
EVERYONE = frozenset(ROLES)


# Groups render in this order.
NAV_GROUPS = ["Lead", "Run", "Manage"]

NAV_ITEMS: list[NavItem] = [
    NavItem("dashboard", "Dashboard", "Lead", 3, "dash", EVERYONE),
    NavItem("people", "People", "Lead", 2, "people", STAFF_AND_LEADERS, ready=True),
    NavItem("services", "Services", "Run", 10, "serv", STAFF_AND_LEADERS),
    NavItem("kids", "Kids", "Run", 11, "kids", STAFF_AND_LEADERS),
    NavItem("giving", "Giving", "Run", 7, "give", STAFF_ONLY),
    NavItem("resources", "Resources", "Run", 6, "res", EVERYONE),
    NavItem("messages", "Messages", "Manage", 12, "msg", EVERYONE),
    NavItem("settings", "Settings", "Manage", 15, "set", STAFF_ONLY),
]


# Where a nav item points once it is real. Items absent from this map fall
# through to the increment placeholder.
NAV_ENDPOINTS = {
    "dashboard": "shell.index",
    "people": "people.index",
}


def nav_for(user) -> list[NavItem]:
    """The navigation one user can see. Anonymous users see nothing."""
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    return [item for item in NAV_ITEMS if user.role in item.roles]


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

# Increments that are actually built. The roadmap card reads this, so the
# dashboard cannot claim something is shipped that is not.
SHIPPED_INCREMENTS = {0, 1, 2, 3, 4, 5}

SHELL = {
    "title": "Foundation",
    "subtitle": "The roster is live. Click any stage to see who is in it.",
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
        (
            "Your session belongs to this church only",
            "Signing in here does not sign you in anywhere else. A session "
            "issued by one church is refused by every other, even when the "
            "same person holds an account at both.",
        ),
        (
            "The menu on the left is yours",
            "Staff, leaders, and members see different navigation from the "
            "same code. Hiding a link is presentation; the page itself checks "
            "the role again before it renders.",
        ),
        (
            "The rail above is a live count",
            "Every number comes from one grouped query against this church's "
            "roster. Click a stage to see exactly who is standing on it.",
        ),
        (
            "A person id is only a number",
            "Opening someone from another church returns a 404, not their "
            "record. Every query that touches a person carries the church in "
            "its WHERE clause rather than filtering afterwards.",
        ),
        (
            "The flag needs two reasons, not one",
            "Someone is flagged only when they are past their stage's expected "
            "time AND nobody has spoken to them in three weeks. Long-standing "
            "members never flag; staying is the point for them.",
        ),
        (
            "Email is queued, never sent inside a click",
            "A request that calls a mail provider is as slow and as reliable "
            "as that provider. The outbox commits a row and a worker sends it, "
            "so a failure is retried and recorded rather than lost.",
        ),
        (
            "Opting out is checked when the message goes, not when it is written",
            "Somebody can unsubscribe in the hour between a message being "
            "queued and being sent. Receipts and account email still reach "
            "them, because those are not marketing.",
        ),
        (
            "One database, two readers",
            "The member app and this dashboard read the same rows. A person "
            "sees only their own record, because no route in the member app "
            "accepts a person id at all.",
        ),
        (
            "Logging a call clears the flag, a note does not",
            "Real contact is the hard stop. Writing that somebody should call "
            "Marcus is not calling Marcus, and treating the two the same would "
            "quietly stop the system flagging the people it exists to find.",
        ),
    ],
    "roadmap_heading": "What comes next",
    "roadmap_intro": (
        "Each item below becomes a working screen at the increment shown. The "
        "order is the approved build order."
    ),
    "shipped_label": "Shipped",
    "progress_label": "{shipped} of {total} shipped",
    "placeholder_lead": "Not built yet.",
    "placeholder_body": (
        "This screen arrives at increment {increment}, {name}. The navigation "
        "item is here now so the shape of the finished product is visible "
        "while it is being built."
    ),
}


AUTH = {
    "title": "Sign in",
    "subtitle": "Use the address your church has on file.",
    "email_label": "Email address",
    "password_label": "Password",
    "remember_label": "Keep me signed in on this device",
    "submit_label": "Sign in",

    "email_required": "Enter your email address.",
    "email_invalid": "That does not look like an email address.",
    "password_required": "Enter your password.",

    # One message for every failure. Distinguishing "no such account" from
    # "wrong password" tells an outsider who attends this church.
    "failed": "That email and password do not match. Check both and try again.",
    "locked": (
        "Too many attempts. This account is locked for 15 minutes. "
        "If you need in sooner, ask a staff member to reset your password."
    ),
    "login_required": "Sign in to see that page.",
    "signed_out": "You are signed out.",
    "forbidden_title": "You do not have access to that",
    "forbidden_body": (
        "Your account does not include this area. If you think it should, "
        "ask a staff member at your church to change your access."
    ),

    "no_reset_yet": (
        "Password reset by email arrives at increment 4, when the outbox "
        "ships. Until then a staff member resets passwords."
    ),
    "sign_out": "Sign out",
}


PEOPLE = {
    "title": "People",
    "subtitle": "Every person, and the step they are on.",

    "rail_heading": "The Journey",
    "rail_intro": (
        "Where all {total} people at {church} are right now. "
        "Click a stage to see who is in it."
    ),
    "rail_empty": (
        "Nobody is on the roster yet. Import a CSV or add people one at a "
        "time, and this rail fills in."
    ),

    "search_placeholder": "Search by name or email",
    "all_stages": "All stages",
    "clear_filter": "Clear",

    "col_person": "Person",
    "col_stage": "Stage",
    "col_household": "Household",
    "col_time": "Time in stage",

    "no_results": "Nobody matches that.",
    "no_results_hint": "Try a shorter search, or clear the stage filter.",

    "days_in_stage": "{days} days",
    "one_day_in_stage": "1 day",
    "today_in_stage": "Today",

    # Person detail
    "snapshot": "Snapshot",
    "timeline": "Timeline",
    "household": "Household",
    "no_household": "Not linked to a household",
    "household_alone": "The only person in this household",
    "contact": "Contact",
    "no_email": "No email on file",
    "no_phone": "No phone on file",
    "first_seen": "First seen",
    "never_seen": "Not recorded",
    "empty_timeline": "Nothing recorded yet.",

    "move_heading": "Move a stage",
    "move_hint": (
        "Moving someone restarts the clock on how long they have been where "
        "they are."
    ),
    "advance_to": "Move to {stage}",
    "at_end": "At the end of the rail.",

    "stage_moved": "Moved from {frm} to {to}",
    "stage_moved_detail": "Direction: {direction}",
    "stage_flash": "{name} is now at {stage}.",

    "note_heading": "Add a note",
    "note_placeholder": "What happened, in a sentence a pastor would say.",
    "note_save": "Save note",
    "note_saved": "Note added to the timeline.",
    "note_empty": "A note needs some text.",

    "back_to_roster": "Back to the roster",
    "showing": "Showing {start} to {end} of {total}",
}


STUCK = {
    "card_heading": "Needs a person, not an email",
    "card_intro": (
        "These people stopped moving and nobody has spoken to them. The system "
        "caught it before anyone noticed on a Sunday."
    ),
    "card_empty": "Nobody is stuck. Everyone overdue has been contacted recently.",
    "card_empty_hint": (
        "A stage flag only fires when someone is past their stage's expected "
        "time AND nobody has made contact in {window} days."
    ),
    "flag": "Stuck",
    "see_all": "See all",

    "banner_title": "Stuck in {stage}",
    "banner_never": "Visited, but nobody has ever logged contact.",

    "metric_stuck": "flagged as stuck",
    "metric_contacted": "people contacted in the last 7 days",
    "metric_unowned": "people with no owner",

    # Contact log
    "contact_heading": "Log a conversation",
    "contact_hint": (
        "Logging real contact is what clears a flag. A note does not, because "
        "writing that someone should be called is not the same as calling "
        "them."
    ),
    "contact_method": "How",
    "contact_summary": "What happened",
    "contact_placeholder": "Called after service. Coming to the lunch on the 10th.",
    "contact_save": "Log it",
    "contact_saved": "Logged. {name} is contacted as of today.",
    "contact_empty": "Say what happened, even if it is one line.",
    "contact_history": "Contact history",
    "contact_none": "No contact logged yet.",
    "contact_never": "Never contacted",
    "contact_days_ago": "{days} days ago",
    "contact_today": "Today",
    "contact_yesterday": "Yesterday",
    "last_contact": "Last contact",

    # Next steps
    "step_heading": "The next step",
    "step_recommended": "Recommended: {step}",
    "step_title_label": "What needs to happen",
    "step_owner_label": "Who owns it",
    "step_due_label": "By when",
    "step_unassigned": "Nobody yet",
    "step_assign": "Assign it",
    "step_assigned": "Assigned to {owner}.",
    "step_open": "Open next steps",
    "step_none": "No next step assigned.",
    "step_done": "Mark done",
    "step_dropped": "Drop it",
    "step_closed": "Closed. {title}",
    "step_overdue": "Overdue",
    "step_due": "Due {date}",
    "step_no_due": "No date set",
    "step_title_required": "A next step needs a description.",

    # Ownership
    "owner_heading": "Owner",
    "owner_none": "Nobody owns this person",
    "owner_set": "{owner} now owns {name}.",
    "owner_assign": "Take ownership",
    "owner_clear": "Release",
    "owner_cleared": "Ownership released.",
}


EMAIL = {
    "heading": "Send an email",
    "hint": (
        "Queued now, sent by the worker. If they have opted out of this kind "
        "of message it is not sent, and the reason is recorded."
    ),
    "category": "What kind of message",
    "subject": "Subject",
    "subject_placeholder": "One line they will see in their inbox",
    "body": "Message",
    "body_placeholder": "Write it the way you would say it.",
    "send": "Queue it",
    "queued": "Queued for {name}. It sends on the next run of the worker.",
    "duplicate": "That message is already queued.",
    "cannot": "Not queued. {reason}",
    "no_address": "No email address on file, so there is nothing to send to.",

    "history": "Email history",
    "history_none": "No email has been queued for this person.",
    "queued_count": "{count} queued",

    "prefs_heading": "What they get emailed about",
    "prefs_hint": (
        "Account, kids check-in, and giving receipts always send. Everything "
        "else can be turned off, here or by the person themselves."
    ),
    "prefs_always": "Always sends",
    "prefs_on": "On",
    "prefs_off": "Off",
    "prefs_save": "Save preferences",
    "prefs_saved": "Preferences updated.",
    "opted_out": "Unsubscribed from all optional email",
    "opted_out_on": "Unsubscribed on {date}",
    "opt_out": "Unsubscribe them",
    "opt_in": "Resubscribe them",
    "opt_out_done": "{name} is unsubscribed from optional email.",
    "opt_in_done": "{name} will receive optional email again.",

    # The public unsubscribe page. Nobody is signed in when they see this.
    "unsub_title": "Unsubscribe",
    "unsub_confirm": "Stop sending optional email to {email}?",
    "unsub_button": "Yes, unsubscribe me",
    "unsub_done_title": "You are unsubscribed",
    "unsub_done": (
        "You will not get announcements, digests, or invitations from "
        "{church}. Receipts and anything to do with your account or your "
        "children still send, because those are not marketing."
    ),
    "unsub_bad_link": "That link is not valid",
    "unsub_bad_link_body": (
        "It may have expired or been mistyped. Ask the church office and they "
        "can update your preferences directly."
    ),
    "unsub_resubscribe": "Changed your mind? Ask the church to turn it back on.",
}


MEMBER = {
    "app_name": "Home",
    "tab_home": "Home",
    "tab_you": "You",

    "greeting_morning": "Good morning, {name}",
    "greeting_afternoon": "Good afternoon, {name}",
    "greeting_evening": "Good evening, {name}",
    "since": "Day {days} with {church}",
    "since_new": "Welcome to {church}",

    "next_step_label": "Your next step",
    "next_step_none": "Nothing on your list right now.",
    "next_step_none_hint": "When there is a next step for you, it shows up here.",
    "next_step_owner": "{owner} is following up",
    "next_step_due": "By {date}",

    "stage_label": "Where you are",
    "stage_meaning": "{meaning}",

    "household_label": "Your family",
    "household_alone": "Just you on file",
    "household_none": "We do not have your family on file yet.",

    "pin_label": "Check-in code",
    "pin_hint": (
        "Use this at the kids check-in kiosk. It tells us which family you "
        "are. It is not a password and it does not authorize a pickup."
    ),
    "pin_none": "No code yet. Ask the church office.",

    "profile_label": "Your details",
    "email_label": "Email",
    "phone_label": "Phone",
    "no_email": "No email on file",
    "no_phone": "No phone on file",
    "fix_details": (
        "Something wrong? Tell the church office and they can correct it."
    ),

    "prefs_label": "Email you get",
    "prefs_hint": (
        "Receipts and anything about your account or your children always "
        "send. Everything else is up to you."
    ),
    "prefs_always": "Always",
    "prefs_save": "Save",
    "prefs_saved": "Saved.",
    "opted_out_note": "You are unsubscribed from optional email.",
    "resubscribe": "Turn optional email back on",
    "unsubscribe_all": "Turn all optional email off",

    "sign_out": "Sign out",

    # Shown to a login with no roster record behind it.
    "unlinked_title": "We have your login, not your record yet",
    "unlinked_body": (
        "Your account works, but it is not connected to your record at the "
        "church. Ask the office to link it and this page fills in."
    ),

    # Staff previewing the member app.
    "preview_note": "You are looking at the member app as {name}.",
    "back_to_staff": "Back to the staff view",
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
