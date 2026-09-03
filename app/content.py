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
    NavItem("groups", "Groups", "Lead", 9, "people", STAFF_AND_LEADERS, ready=True),
    NavItem("services", "Services", "Run", 10, "serv", STAFF_AND_LEADERS, ready=True),
    NavItem("kids", "Kids", "Run", 11, "kids", STAFF_AND_LEADERS, ready=True),
    NavItem("giving", "Giving", "Run", 7, "give", STAFF_ONLY, ready=True),
    NavItem("resources", "Resources", "Run", 6, "res", STAFF_AND_LEADERS, ready=True),
    NavItem("messages", "Messages", "Manage", 12, "msg", STAFF_AND_LEADERS, ready=True),
    NavItem("settings", "Settings", "Manage", 15, "set", STAFF_ONLY),
]


# Where a nav item points once it is real. Items absent from this map fall
# through to the increment placeholder.
NAV_ENDPOINTS = {
    "dashboard": "shell.index",
    "people": "people.index",
    "resources": "resources.index",
    "giving": "giving.index",
    "groups": "groups.index",
    "services": "services.index",
    "kids": "kids.index",
    "messages": "messages.index",
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
    "groups": (
        '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
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
SHIPPED_INCREMENTS = {0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14}

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

    "forgot_link": "Forgot your password?",

    # Request a reset
    "forgot_title": "Reset your password",
    "forgot_subtitle": "We will email you a link. It works once and lasts an hour.",
    "forgot_submit": "Send the link",
    "forgot_back": "Back to sign in",
    # One message whether or not the address has an account. Anything else
    # turns this form into a way to find out who attends the church.
    "forgot_sent": (
        "If that address has an account here, a reset link is on its way. "
        "Check your inbox, and your spam folder if it is not there."
    ),

    # Set a new one
    "reset_title": "Choose a new password",
    "reset_subtitle": "At least 12 characters. Length helps more than symbols do.",
    "reset_password": "New password",
    "reset_confirm": "Type it again",
    "reset_submit": "Save it and sign in",
    "reset_mismatch": "Those two do not match.",
    "reset_done": "Password changed. You are signed in.",
    "reset_signed_out_elsewhere": (
        "Anywhere else you were signed in has been signed out."
    ),

    "reset_bad_link_title": "That link will not work",
    "reset_bad_link": (
        "Reset links last an hour and work once. This one has expired, has "
        "already been used, or was mistyped. Ask for a new one."
    ),
    "reset_try_again": "Send a new link",

    # The email itself
    "reset_email_subject": "Reset your {church} password",
    "reset_email_body": (
        "Hello {name},\n\n"
        "Somebody asked to reset the password for your {church} account. "
        "Open this link to choose a new one:\n\n"
        "{link}\n\n"
        "The link works once and expires in {minutes} minutes.\n\n"
        "If this was not you, nothing has changed and you can ignore this "
        "message. Your current password still works.\n\n"
        "{church}"
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
    "tab_read": "Read",
    "tab_give": "Give",
    "tab_groups": "Groups",
    "tab_serve": "Serve",
    "tab_chat": "Chat",
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


RESOURCES = {
    "title": "Resources",
    "subtitle": "Content published under your name.",
    "intro": (
        "Written for {church} and delivered inside your app. Members never see "
        "another brand on it."
    ),

    "new_heading": "Write something new",
    "new_title": "What is it called",
    "new_title_placeholder": "Known: a five day plan",
    "new_kind": "What kind",
    "new_summary": "One line about it",
    "new_summary_placeholder": "Who it is for and what it covers.",
    "create": "Create it",
    "created": "{title} created. Add the days, then publish it.",
    "title_required": "It needs a name.",

    "empty": "Nothing published yet.",
    "empty_hint": "Write a plan, add a few days, and publish it. Members see it immediately.",

    "sessions": "{count} days",
    "one_session": "1 day",
    "no_sessions": "No days yet",
    "started": "{count} started",
    "started_none": "Nobody has started it",

    "edit": "Edit",
    "publish": "Publish it",
    "published": "{title} is live. Members can read it now.",
    "unpublish": "Unpublish",
    "unpublished": "{title} is back to a draft. Members no longer see it.",
    "publish_empty": "Add at least one day before publishing.",
    "archive": "Archive",
    "archived": "{title} archived.",

    "session_heading": "The days",
    "session_add": "Add a day",
    "session_title": "Title for this day",
    "session_title_placeholder": "Day 1: Known before you were born",
    "session_passage": "Passage",
    "session_passage_placeholder": "Psalm 139:1-6",
    "session_body": "What they read",
    "session_body_placeholder": (
        "Write it the way you would say it. Blank line for a new paragraph, "
        "> for scripture, - for a list, **bold**."
    ),
    "session_question": "A question to sit with",
    "session_question_placeholder": "Where have you been trying to be known?",
    "session_save": "Save the day",
    "session_saved": "Added.",
    "session_delete": "Delete",
    "session_deleted": "Deleted.",
    "session_none": "No days yet. Add the first one below.",
    "session_title_required": "Every day needs a title.",

    "format_help": (
        "Formatting: a blank line starts a paragraph, # for a heading, "
        "> for scripture, - for a list, **bold** and *italic*."
    ),

    # Member side
    "member_heading": "Reading",
    "member_none": "Nothing to read right now.",
    "member_none_hint": "When your church publishes a plan it shows up here.",
    "member_progress": "Day {done} of {total}",
    "member_day_of": "Day {position} of {total}",
    "member_start": "Start",
    "member_continue": "Keep reading",
    "member_done": "Finished",
    "member_mark": "Mark today done",
    "member_unmark": "Not done after all",
    "member_next": "Next day",
    "member_back": "Back to the plan",
    "member_complete": "You finished it. Well done.",
    "member_question": "Sit with this",
}


GIVING = {
    "title": "Giving",
    "subtitle": "You keep the platform you already use.",

    "explain": (
        "We do not move your money and we never see a card number. {provider} "
        "keeps your donor history, your rates, and your recurring givers "
        "exactly as they are. This screen opens it, and the app sends your "
        "people to your giving page."
    ),
    "why": (
        "Giving is mirrored here read only, so the system can tell you when "
        "somebody quietly stops. Nothing is written back to your platform."
    ),

    # Increment 13
    "sync_heading": "Giving data",
    "sync_hint": (
        "Paste your API keys and the system pulls a read-only copy of your "
        "giving. Until Tithely approves API access, import a CSV export "
        "instead. Both land in the same place."
    ),
    "public_key": "Public key",
    "private_key": "Private key",
    "private_key_stored": "A key is stored. Paste a new one to replace it.",
    "org_ref": "Organization ID",
    "save_keys": "Save the keys",
    "keys_saved": "Saved. The private key is encrypted at rest.",
    "keys_cleared": "Keys removed.",
    "last_sync": "Last pulled {when}",
    "never_synced": "Nothing pulled yet",

    "stopped_heading": "Giving that stopped",
    "stopped_intro": (
        "These people had a standing gift and it has gone quiet. That is "
        "usually a discipleship signal before it is a budget problem."
    ),
    "stopped_empty": "Nobody who gives regularly has gone quiet.",
    "stopped_empty_hint": (
        "A standing gift counts as stopped once it is past its own rhythm by "
        "three weeks, so a failed card that retried does not show up here."
    ),
    "stopped_flag": "Stopped",
    "stopped_unmatched": "Not matched to anyone on the roster",

    "mtd": "Giving month to date",
    "recent_gifts": "Recent gifts",
    "no_gifts": "No gifts recorded for this person.",
    "given_total": "{total} across {count} gifts",
    "given_since": "since {date}",
    "recurring_heading": "Standing gift",
    "recurring_line": "{amount} {frequency}, last on {date}",
    "recurring_none": "No standing gift on file.",

    # The review queue
    "queue_title": "Gifts we could not match to a person",
    "queue_subtitle": (
        "The system only attaches a gift by itself when an email address "
        "points at exactly one person. Everything else is here for a human."
    ),
    "queue_empty": "Every gift is matched.",
    "queue_empty_hint": "Nothing waiting.",
    "queue_count": "{count} waiting",
    "queue_suggestion": "Looks like {name}",
    "queue_no_suggestion": "No obvious match",
    "queue_attach": "That is them",
    "queue_attached": "{amount} attached to {name}.",
    "queue_pick": "Someone else",
    "queue_choose": "Choose a person",
    "queue_choose_first": "Pick who this is before attaching it.",
    "queue_ignore": "Not a person here",
    "queue_ignored": "Set aside.",
    "queue_ambiguous": "More than one person could be this. Pick one.",

    "open_admin": "Open {provider}",
    "open_admin_hint": "Opens in a new tab. Sign in there as you normally would.",
    "not_configured": "No giving link set up yet.",
    "not_configured_hint": (
        "Paste the two addresses below and the Giving nav and the member Give "
        "tab both start working."
    ),

    "setup_heading": "Where your giving lives",
    "provider_label": "Platform",
    "admin_label": "Your admin dashboard",
    "admin_placeholder": "https://tithe.ly/...",
    "admin_help": "The page you sign in to when you check giving.",
    "form_label": "Your giving page",
    "form_placeholder": "https://tithe.ly/give_new/www/#/tithely/give-one-time/...",
    "form_help": "The page your people land on when they tap Give.",
    "save": "Save the links",
    "saved": "Saved. Giving is live for staff and members.",
    "cleared": "Giving links cleared.",

    # Member side
    "member_tab": "Give",
    "member_heading": "Give",
    "member_intro": (
        "Giving happens on {church}'s own giving page. It opens in a new tab."
    ),
    "member_button": "Go to the giving page",
    "member_none": "Your church has not set up online giving here yet.",
    "member_none_hint": "Ask the church office how they would like you to give.",
}


GROUPS = {
    "title": "Groups",
    "subtitle": "Who is meeting, and who is in the room.",

    "new_heading": "Start a group",
    "name": "What is it called",
    "name_placeholder": "Wednesday Women",
    "pattern": "When it meets",
    "pattern_placeholder": "Wednesdays 7:00pm",
    "location": "Where",
    "location_placeholder": "The Hollands' house",
    "create": "Create it",
    "created": "{name} created. Add people to it.",
    "name_required": "It needs a name.",

    "empty": "No groups yet.",
    "empty_hint": "Start one, add a few people, and put the next meeting on it.",
    "members": "{count} people",
    "one_member": "1 person",
    "no_members": "Nobody in it yet",
    "led_by": "Led by {names}",
    "no_leader": "No leader named",

    "roster_heading": "Who is in it",
    "add_person": "Add someone",
    "add_person_placeholder": "Search the roster",
    "add": "Add",
    "added": "{name} added to {group}.",
    "already_in": "{name} is already in this group.",
    "remove": "Remove",
    "removed": "{name} removed.",
    "make_leader": "Make leader",
    "make_member": "Make member",
    "role_changed": "{name} is now a {role} of this group.",
    "roster_empty": "Nobody has been added yet.",

    "meetings_heading": "Meetings",
    "meeting_when": "When",
    "meeting_add": "Put a meeting on the calendar",
    "meeting_saved": "Meeting added.",
    "meeting_bad_time": "That does not look like a date and time.",
    "meeting_none": "No meetings scheduled.",
    "meeting_past": "Past",
    "going_count": "{count} going",
    "going_none": "Nobody has answered yet",
    "meeting_delete": "Remove",
    "meeting_deleted": "Meeting removed.",

    # Member side
    "member_tab": "Groups",
    "member_heading": "Your groups",
    "member_none": "You are not in a group yet.",
    "member_none_hint": "Ask a staff member about joining one.",
    "member_next": "Next meeting",
    "member_no_meeting": "Nothing on the calendar yet.",
    "rsvp_prompt": "Can you make it?",
    "rsvp_saved": "Thanks. We have you down as {response}.",
    "rsvp_going": "Going",
    "rsvp_maybe": "Maybe",
    "rsvp_not_going": "Can't make it",
}


SERVICES = {
    "title": "Services",
    "subtitle": "Plan the Sunday, ask the people, send it.",

    "new_heading": "Plan a service",
    "name": "What is it",
    "name_placeholder": "Sunday",
    "when": "When",
    "create": "Add it",
    "created": "{name} added. Build the plan.",
    "bad_time": "That does not look like a date and time.",

    "upcoming": "Coming up",
    "past": "Recent",
    "empty": "Nothing planned yet.",
    "empty_hint": "Put the next four Sundays on the calendar and build them out.",
    "runtime": "{minutes} minutes",
    "team_status": "{accepted} in, {waiting} waiting, {declined} out",

    # Plan editor
    "plan_heading": "The running order",
    "plan_empty": "Nothing in the plan yet.",
    "add_element": "Add an element",
    "add_song": "Add a song",
    "element_title": "What happens",
    "element_placeholder": "Welcome and announcements",
    "minutes": "Minutes",
    "item_notes": "Notes",
    "item_delete": "Remove",
    "item_added": "Added to the plan.",
    "item_removed": "Removed.",
    "item_title_required": "Every item needs a name.",
    "song_required": "Pick a song.",
    "key_label": "Key",
    "key_default": "Song default",
    "key_bad": "{key} is not a key. Try G, Bb, F#m, or Am.",
    "capo": "capo {capo}, play {shape}",
    "no_key": "No key set",

    # Songs
    "songs_title": "Songs",
    "songs_subtitle": "Titles, keys, and your CCLI numbers. Not the words.",
    "songs_why": (
        "We do not store lyrics or chord charts. Reproducing those needs the "
        "CCLI licence your church holds, so the words stay where you already "
        "license them. The number here points at your copy."
    ),
    "song_title": "Title",
    "song_author": "Author",
    "song_ccli": "CCLI number",
    "song_key": "Usual key",
    "song_tempo": "BPM",
    "song_add": "Add the song",
    "song_added": "{title} added.",
    "song_used": "used {count} times",
    "song_unused": "not used yet",
    "songs_empty": "No songs yet.",
    "songs_empty_hint": "Add the ones you actually sing. Ten is a good start.",

    # Teams
    "teams_title": "Teams",
    "teams_subtitle": "Who serves, and what they do.",
    "team_name": "Team name",
    "team_name_placeholder": "Worship",
    "team_add": "Add the team",
    "team_added": "{name} added. Add the positions people fill.",
    "position_add": "Add a position",
    "position_placeholder": "Acoustic",
    "position_added": "{name} added.",
    "positions_empty": "No positions yet.",
    "members_empty": "Nobody on this team yet.",
    "teams_empty": "No teams yet.",
    "teams_empty_hint": "Worship, Kids, Hospitality, Tech. Whatever you actually run.",
    "member_add": "Add someone",
    "member_added": "{name} is on {team}.",
    "member_removed": "{name} removed from the team.",
    "already_on": "{name} is already on this team.",

    # Assignments
    "who_heading": "Who is serving",
    "who_empty": "Nobody asked yet.",
    "assign": "Ask them",
    "assigned": "{name} asked to play {position}.",
    "already_asked": "{name} has already been asked for that.",
    "unassign": "Remove",
    "unassigned": "Removed from the plan.",

    "send_heading": "Send the plan",
    "send_hint": (
        "Emails everyone on the plan the running order and what you have asked "
        "them to do. Anyone who has already answered still gets it."
    ),
    "send": "Send it",
    "sent": "Queued for {count} people. It goes out on the next worker run.",
    "send_nobody": "Nobody is on this plan yet.",
    "resend": "Send it again",
    "sent_on": "Sent {date}",

    "email_subject": "{service} plan, {date}",
    "email_body": (
        "Hello {name},\n\n"
        "You are on the plan for {service}, {date}.\n\n"
        "You are down for: {position}\n\n"
        "Running order:\n{plan}\n"
        "\nOpen the app to accept or let us know you cannot make it.\n\n"
        "{church}"
    ),

    # Member side
    "member_tab": "Serve",
    "member_heading": "Serving",
    "member_none": "You are not on a plan right now.",
    "member_none_hint": "When someone asks you to serve, it turns up here.",
    "member_asked": "You have been asked to serve",
    "member_accept": "I can do it",
    "member_decline": "I cannot make it",
    "member_accepted": "You are down for {position}.",
    "member_declined": "Thanks for letting us know.",
    "member_change": "Change your answer",
    "member_plan": "The plan",
}


KIDS = {
    "title": "Kids",
    "subtitle": "Who is in the room, and who took them home.",

    "open_heading": "Open a session",
    "session_name": "What is it",
    "session_placeholder": "Sunday 9:30",
    "session_when": "When",
    "open_it": "Open it",
    "opened": "{name} is open. The kiosk is live.",
    "bad_time": "That does not look like a date and time.",

    "no_open_session": "No session is open.",
    "no_open_session_hint": (
        "Open one before a Sunday and the kiosk starts accepting families."
    ),
    "close": "Close the session",
    "closed": "{name} closed.",
    "reopen": "Reopen it",
    "close_warning": (
        "{count} children are still in a room. Closing the session does not "
        "check them out, and it should not: staff need to see them."
    ),
    "still_in": "{count} still in a room",
    "collected": "{count} collected",

    "present_heading": "In the rooms",
    "present_empty": "Nobody checked in yet.",
    "checked_in_at": "In at {time}",
    "collected_by": "Collected by {name} at {time}",
    "collected_unknown": "Collected at {time}",

    "open_kiosk": "Open the kiosk",
    "kiosk_exit": "Exit kiosk",

    # Kiosk
    "kiosk_welcome": "Welcome. Let's check in.",
    "kiosk_prompt": "Enter your family's check-in code.",
    "kiosk_clear": "Clear",
    "kiosk_delete": "Delete",
    "kiosk_forgot": "Forgot your code?",
    "kiosk_new_family": "First time here? Start a new family",
    "kiosk_unknown": "We do not recognise that code. Try again, or ask a volunteer.",
    "kiosk_too_many": (
        "Too many tries. Ask a volunteer at the desk and they will check you in."
    ),
    "kiosk_closed": "Check-in is not open right now.",

    "family_heading": "Who is here today?",
    "family_hint": "Tap everyone you are checking in, then check them in.",
    "family_none": "Nobody on this household is set up for kids check-in.",
    "family_already": "Already checked in",
    "check_in": "Check them in",
    "check_in_none": "Pick at least one person.",

    "label_heading": "Checked in",
    "label_code": "Pickup code",
    "label_hint": (
        "Show this code when you collect them. It is only good for today. "
        "Your check-in code stays the same and is not a pickup code."
    ),
    "label_children": "Checked in: {names}",
    "label_done": "Done",

    # Forgot the code
    "forgot_heading": "We will send you your code",
    "forgot_hint": "Type the email address the church has for you.",
    "forgot_email": "Email address",
    "forgot_send": "Send it",
    # One message either way, so the kiosk cannot be used to find out who
    # attends by typing addresses at it.
    "forgot_sent": (
        "If we have that address on file, your check-in code is on its way."
    ),
    "forgot_back": "Back",
    "forgot_email_subject": "Your {church} check-in code",
    "forgot_email_body": (
        "Hello {name},\n\n"
        "Your family's check-in code at {church} is {pin}.\n\n"
        "Use it at the kids check-in kiosk. It tells us which family you are. "
        "It is not a password and it does not authorize a pickup: the pickup "
        "code is printed when you check in and changes every week.\n\n"
        "{church}"
    ),

    # Check-out
    "checkout_heading": "Collect a child",
    "checkout_prompt": "Enter the pickup code from the check-in.",
    "checkout_find": "Find them",
    "checkout_unknown": "No children are checked in under that code.",
    "checkout_who": "Who is collecting?",
    "checkout_who_hint": "The name of the adult taking them. Write what is true.",
    "checkout_confirm": "Check them out",
    "checkout_done": "{names} checked out.",
    "checkout_already": "Already collected.",
    "checkout_none": "Pick at least one child.",
}


MESSAGES = {
    "title": "Messages",
    "subtitle": "Announcements, rooms, and direct conversations.",

    "new_heading": "Start something",
    "new_title": "What is it called",
    "new_title_placeholder": "Worship team",
    "new_kind": "Who can see it",
    "kind_announcement": "Everyone at the church",
    "kind_room": "Only people you invite",
    "create": "Create it",
    "created": "{title} created.",
    "title_required": "It needs a name.",

    "announcement_note": (
        "Everyone at {church} sees this. Only staff can post to it, so a "
        "church-wide message does not turn into a room nobody chose to join."
    ),
    "room_note": "Only the people you add can see this.",

    "empty": "No conversations yet.",
    "empty_hint": "Start an announcement everyone sees, or a room for one team.",
    "members": "{count} people",
    "no_messages": "Nothing said yet.",
    "last_message": "Last message {when}",
    "unread": "{count} unread",

    "post_placeholder": "Write something.",
    "post": "Send",
    "post_empty": "Say something first.",
    "post_forbidden": "You cannot post here.",
    "posted": "Sent.",

    "member_add": "Add someone",
    "member_added": "{name} added.",
    "member_remove": "Remove",
    "member_removed": "{name} removed.",
    "members_empty": "Nobody added yet.",
    "already_in": "{name} is already in this room.",

    "delete": "Delete",
    "deleted": "Message removed.",
    "deleted_placeholder": "This message was removed.",
    "delete_note": (
        "Deleting clears the words and keeps the record. The church can still "
        "see that something was said and removed."
    ),

    "archive": "Archive",
    "archived": "{title} archived.",
    "archived_note": "Archived. Nobody can post here now.",

    "email_it": "Email it too",
    "email_hint": (
        "Also sends this as an email to everyone who has not turned off church "
        "announcements."
    ),
    "emailed": "Queued for {count} people.",
    "email_subject": "{church}: {title}",

    # Member side
    "member_tab": "Chat",
    "member_heading": "Messages",
    "member_none": "Nothing here yet.",
    "member_none_hint": "Announcements and any rooms you are in show up here.",
    "member_readonly": "Only staff post here.",
    "member_back": "All messages",
    "direct_start": "Message someone",
    "direct_started": "Talking to {name}.",
    "direct_self": "You cannot start a conversation with yourself.",
}


AUTOMATION = {
    "card_heading": "Running without staff time",
    "card_intro": (
        "These went out on their own this week. None of them was written on a "
        "Monday morning."
    ),
    "card_empty": "Nothing is running automatically yet.",
    "card_empty_hint": (
        "A welcome series starts by itself when somebody first shows up, and "
        "stops the moment anyone actually talks to them."
    ),
    "sent_this_week": "sent automatically in the last 7 days",
    "running_now": "sequences running",
    "stopped_by_contact": "stopped because a person made contact",

    "person_heading": "Automatic follow up",
    "person_none": "Nothing running for this person.",
    "person_running": "Step {step} of {total}, next on {date}",
    "person_ended": "{reason}",
    "stop_it": "Stop it",
    "stopped": "Stopped.",

    "why_stopped": (
        "A sequence ends the moment somebody logs a real conversation, or the "
        "person gets where it was pointing. Writing a note does not stop it, "
        "because writing that somebody should be called is not calling them."
    ),
}

ERRORS = {
    # Shown when the host resolves to no church at all. Distinct from a 500 on
    # purpose: this is a configuration answer, not a crash, and telling an
    # operator "something broke on our end" sends them to read tracebacks that
    # do not exist.
    "unconfigured_title": "No church is set up at this address",
    "unconfigured_body": (
        "The application is running and the database is reachable. No church "
        "is mapped to this hostname yet, so there is nothing to show."
    ),
    "unconfigured_fix": (
        "If you are setting this up: open a shell on this service and run "
        "flask routing-check to see what resolves, then "
        "flask set-domain --church <slug> --domain <this hostname>."
    ),
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
