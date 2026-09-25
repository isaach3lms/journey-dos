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
    NavItem("dashboard", "Dashboard", "Lead", 3, "dash", EVERYONE, ready=True),
    NavItem("people", "People", "Lead", 2, "people", STAFF_AND_LEADERS, ready=True),
    NavItem("groups", "Groups", "Lead", 9, "people", STAFF_AND_LEADERS, ready=True),
    NavItem("services", "Services", "Run", 10, "serv", STAFF_AND_LEADERS, ready=True),
    NavItem("kids", "Kids", "Run", 11, "kids", STAFF_AND_LEADERS, ready=True),
    NavItem("giving", "Giving", "Run", 7, "give", STAFF_ONLY, ready=True),
    NavItem("resources", "Resources", "Run", 6, "res", STAFF_AND_LEADERS, ready=True),
    NavItem("messages", "Messages", "Manage", 12, "msg", STAFF_AND_LEADERS, ready=True),
    NavItem("settings", "Settings", "Manage", 15, "set", STAFF_ONLY, ready=True),
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
    "settings": "settings.index",
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

# Increments that are actually built. The nav reads this to decide whether a
# section is a working screen or a placeholder, so the
# dashboard cannot claim something is shipped that is not.
SHIPPED_INCREMENTS = set(range(16))

SHELL = {
    "title": "Welcome To Your Dashboard",
    "subtitle": "The roster is live. Click any stage to see who is in it.",
    "placeholder_lead": "Not built yet.",
    "placeholder_body": (
        "This screen arrives at increment {increment}, {name}. The navigation "
        "item is here now so the shape of the finished product is visible "
        "while it is being built."
    ),
}


# The staff dashboard, laid out like the approved demo. See app/dashboard.py.
DASHBOARD = {
    "title": "Welcome To Your Dashboard",
    "sunday_today": "Sunday, {date} is today",
    "sunday_tomorrow": "Sunday, {date} is tomorrow",
    "sunday_out": "Sunday, {date} is {days} days out",
    "search": "Search",
    "search_placeholder": "Find a person by name or email",
    "request_support": "Request support",
    "support_subject": "Support request from {church}",

    "rail_heading": "The Journey",
    "rail_intro": "Where all {total} people at {church} are right now. Click a stage to see who is in it.",
    "rail_empty": "Nobody is on the roster yet. Add people from the People tab.",
    # Children sit at the end of the rail, counted on their own. A child is
    # not at a stage of anything; they are somebody's kid.
    "kids_label": "Kids",
    "kids_meaning": "Children on the roster, ready for check-in.",
    "kids_note": "In families",
    "open_people": "Open People",
    "stage_stuck": "{count} stuck",
    "stage_moving": "Moving",
    "footer_stuck": "people flagged as stuck too long",
    "footer_steps": "next steps taken in the last 7 days",
    "footer_unowned": "people with no owner",

    "tile_attendance": "Attendance last Sunday",
    "tile_guests": "First time guests",
    "tile_next_steps": "Next steps taken",
    "tile_giving": "Giving month to date",
    "trend_attendance_up": "Up {n}% over 4 weeks",
    "trend_attendance_down": "Down {n}% over 4 weeks",
    "trend_attendance_flat": "Level with the last 4 weeks",
    "trend_week_up": "Up {n} from last week",
    "trend_week_down": "Down {n} from last week",
    "trend_week_flat": "Same as last week",
    "trend_giving_up": "Up {n}% vs last month",
    "trend_giving_down": "Down {n}% vs last month",
    "trend_giving_flat": "Level with last month",
    "empty_attendance": "Add last Sunday's headcount",
    "empty_giving": "Connect giving to see this",

    "health_heading": "Church health",
    "health_of": "{score} of 100",
    "health_next_step": "People taking a next step",
    "health_grouped": "Members in a group",
    "health_serving": "Adults serving",
    "health_guests_48h": "Guests contacted within 48 hours",
    "health_hint_next_step": "Moved a stage or finished a next step in the last 90 days.",
    "health_hint_grouped": "Of the people at Member or beyond, how many are in an active group.",
    "health_hint_serving": "Of everyone who is not a child, how many are on an active team.",
    "health_hint_guests_48h": "Of first time guests in the last 90 days, how many had a logged contact within two days.",
    "weakest_next_step": "People taking a next step is the weakest number. {gap} people have not moved in 90 days.",
    "weakest_grouped": "Members in a group is the weakest number. {gap} members are not in a group yet.",
    "weakest_serving": "Adults serving is the weakest number. {gap} adults are not on a team yet.",
    "weakest_guests_48h": "Guest follow up is the weakest number. {gap} recent guests waited more than two days to hear from anyone.",
    "health_empty": "Health fills in as people, groups, and teams are added.",

    "auto_intro": "{count} messages went out this week. None of them were written on a Monday morning.",
    "auto_intro_none": "Nothing has gone out on its own this week yet.",
    "auto_all_active": "All active",
    "auto_badge": "AUTO",
    "auto_trigger": "Starts when someone becomes a {stage}, {steps} messages over {days} days",
    "auto_sent": "{count} sent this week",
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
    "help_link": "Help",

    # Creating an account
    # One message whether or not the address already has an account. Otherwise
    # the form is a way to find out who attends.
    "join_name_required": "We need a name to put on your account.",

    "joined_event": "Created their own account",
    "joined_detail": "Signed up through the app and confirmed their email.",
    "verify_bad_title": "That link will not work",
    "verify_bad": (
        "Confirmation links last three days and work once. This one has "
        "expired, has already been used, or was mistyped."
    ),

    "change_title": "Choose your own password",
    "change_body": (
        "You signed in with a temporary password. Pick one only you know and "
        "you are in."
    ),
    "change_submit": "Set it and continue",
    "change_done": "Done. That is your password now.",
    "change_same": "That is the temporary one. Pick something else.",

    "unverified_title": "Confirm your email first",
    "unverified": (
        "We sent a link to your address when you signed up. Open it and you "
        "are in. Nothing else is needed."
    ),
    "unverified_resend": "Send it again",
    "unverified_sent": "On its way. Check your inbox and your spam folder.",

    "join_link": "New here? Create an account",

    # Creating an account
    "join_title": "Create your account",
    "join_subtitle": "Takes a minute. We will email you a link to confirm it.",
    "join_name": "Your name",
    "join_submit": "Create it",
    "join_back": "Already have an account? Sign in",
    "join_closed_title": "Accounts are set up by the church",
    "join_closed": (
        "This church creates accounts for its people rather than letting "
        "anyone sign themselves up. Ask the church office and they will set "
        "yours up."
    ),
    # One message whether or not the address already has an account. Anything
    # else turns the form into a way to find out who attends.
    "join_sent": (
        "Check your email. If that address can be used here, a confirmation "
        "link is on its way. It works once and lasts three days."
    ),

    "verify_title": "Confirm your email",
    "verify_done": "You are all set. Welcome.",
    "verify_bad_link_title": "That link will not work",
    "verify_bad_link": (
        "Confirmation links last three days and work once. This one has "
        "expired, has already been used, or was mistyped."
    ),
    "verify_resend": "Send me a new link",
    "verify_needed_title": "Confirm your email first",
    "verify_needed": (
        "We sent a link to the address you signed up with. Open it and you "
        "are in."
    ),
    "verify_resent": "Sent. Check your email.",

    "verify_email_subject": "Confirm your account at {church}",
    "verify_email_body": (
        "Hello {name},\n\n"
        "Somebody created an account at {church} with this address. Open this "
        "link to confirm it:\n\n"
        "{link}\n\n"
        "The link works once and lasts three days.\n\n"
        "If this was not you, ignore this message. Nothing was created that "
        "can be used without opening the link above.\n\n"
        "{church}"
    ),

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
    "reset_email_subject": "Reset your password at {church}",
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

    # Notifications
    "push_heading": "Notifications on this device",
    "push_hint": (
        "A short note on your lock screen when something needs you. It never "
        "says anything private, only enough to open the app."
    ),
    "push_on": "Turn them on",
    "push_off": "Turn them off on this device",
    "push_enabled": "On for this device",
    "push_devices": "On for {count} of your devices",
    "push_none": "Off",
    "push_blocked": (
        "Your browser is blocking notifications. Turn them back on in your "
        "browser settings for this site, then come back here."
    ),
    "push_unsupported": (
        "This browser cannot do notifications. On an iPhone, add the app to "
        "your home screen first and open it from there."
    ),
    "push_saved": "Notifications are on for this device.",
    "push_removed": "Notifications are off for this device.",

    # Deleting an account
    "privacy_link": "What is stored about you",
    "delete_heading": "Delete your account",
    "delete_body": (
        "This removes your login straight away and signs you out everywhere. "
        "You will not be able to sign in again unless the church sets you up "
        "a new one."
    ),
    "delete_record": (
        "Your record at {church} belongs to the church, in the same way a "
        "paper roll would, so it stays. Ask the office if you want that "
        "removed too and they will do it."
    ),
    "delete_confirm_label": "Type your password to confirm",
    "delete_button": "Delete my account",
    "delete_wrong_password": "That password is not right, so nothing was deleted.",
    "delete_done": "Your account is deleted. Thanks for letting us know.",
    "delete_last_staff": (
        "You are the only staff account at this church. Deleting it would "
        "lock everybody out, so make somebody else staff first."
    ),
    "delete_gone_title": "Your account is deleted",
    "delete_gone": (
        "It is gone. If you change your mind, ask {church} and they will set "
        "you up again."
    ),
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

    "kids_label": "Kids",
    "kids_meaning": "Children on the roster, ready for check-in.",
    "kids_filter": "Kids",
    "kids_tag": "Child",
    "kids_age": "{years} yrs",
    "kids_age_one": "1 yr",
    "kids_no_age": "No birthday on file",
    "kids_empty": "No children on the roster yet. Parents add their own from the app, and staff can add them here.",

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

    # Linking somebody to a family. The duplicate-profile fix: put the second
    # record's family on the first, then archive the second.
    "household_change": "Change",
    "household_pick": "Move them into a family",
    "household_none_option": "No household",
    "household_new_label": "Or start a new family",
    "household_new_placeholder": "The Tanksley family",
    "household_save": "Save",
    "household_count": "{name} ({count})",
    "household_count_one": "{name} (1)",
    "household_moved": "{name} is now in {household}.",
    "household_started": "Started {household} with {name} in it.",
    "household_removed": "{name} is no longer in a household.",
    "household_already": "{name} is already in {household}.",
    "household_needs_name": "A family needs a name.",
    "household_unknown": "We could not find that household.",
    "household_event": "Household changed",
    "household_event_joined": "Moved into {household}",
    "household_event_left": "Taken out of {household}",
    "household_emptied": "{household} had nobody left in it, so it is gone.",
    "household_hint": (
        "Everyone in a family shares one address and one check-in code. "
        "Moving somebody here does not change anything else about them."
    ),
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

    # Adding somebody by hand
    "add_heading": "Add someone",
    "add_open": "Add someone",
    "add_hint": (
        "For the person who filled in a card on Sunday. Import a spreadsheet "
        "instead if you have a list."
    ),
    "add_first": "First name",
    "add_last": "Last name",
    "add_email": "Email",
    "add_phone": "Phone",
    "add_stage": "Where they are",
    "add_login": "Also let them sign in",
    "add_login_hint": (
        "We email them a link to set their own password. You never type one."
    ),
    "add_role": "What they can do",
    "add_submit": "Add them",
    "add_first_required": "A first name is the one thing we need.",
    "add_done": "{name} is on the roster.",
    "add_done_login": "{name} is on the roster. We emailed them a link to set a password.",
    "add_login_taken": (
        "{name} is on the roster, but {email} already has an account, so no "
        "invite was sent."
    ),
    "add_login_needs_email": (
        "{name} is on the roster. An account needs an email address, so none "
        "was created."
    ),
    "add_event": "Added by {name}",

    "waiting_heading": "Waiting to be let in",
    "waiting_intro": (
        "These people signed themselves up. Their own record works and they "
        "can read what you publish, but church-wide messages stay hidden "
        "until one of you says they are real."
    ),
    "waiting_none": "Nobody is waiting.",
    "waiting_count": "{count} waiting",
    "waiting_since": "Signed up {date}",
    # Bulk archiving
    "bulk_select": "Select",
    "bulk_selected": "{count} selected",
    "bulk_archive": "Archive them",
    "bulk_none": "Tick somebody first.",
    "bulk_archived": "Archived {count}.",
    "bulk_restored": "Restored {count}.",
    "bulk_hint": (
        "Archiving takes somebody off the roster and out of the counts, the "
        "flags, and every sequence. Nothing is deleted: their check-in "
        "history, giving, and messages stay, because the church has to keep "
        "those."
    ),
    "bulk_confirm": "Archive {count} people?",
    "bulk_self": "You cannot archive your own record.",

    "archived_event": "Archived",
    "archived_title": "Archived people",
    "archived_subtitle": "Off the roster, still on the record.",
    "archived_count": "{count} archived",
    "archived_none": "Nobody is archived.",
    "archived_restore": "Put them back",
    "archived_link": "Archived ({count})",

    "approve": "They are real",
    "approved": "{name} is in.",
    "approved_event": "Approved",
    "approved_detail": "Confirmed by {name} after signing themselves up.",
    "approved_pill": "Waiting to be approved",
    "approved_email_subject": "You are in at {church}",
    "approved_email_body": (
        "Hello {name},\n\n"
        "Somebody at {church} has confirmed your account. You will now see "
        "church-wide messages in the app along with everything else.\n\n"
        "{church}"
    ),

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
    "verse_label": "This week's verse",
    "app_name": "Home",
    "tab_home": "Home",
    "tab_read": "Read",
    "tab_give": "Give",
    "tab_groups": "Groups",
    "tab_serve": "Serve",
    "tab_chat": "Chat",
    "tab_you": "You",
    "tab_grow": "Grow",
    "grow_reading": "Reading plans",
    "grow_groups": "My groups",

    # One greeting at every hour. A clock-based greeting was wrong for
    # anybody outside the server's timezone, and it read as a novelty.
    "greeting": "Welcome back, {name}",
    "since": "Day {days} with {church}",
    "since_new": "Welcome to {church}",

    "next_step_label": "Your next step",
    "waiting_title": "Someone will let you in shortly",
    "waiting_body": (
        "You are signed up. Somebody at {church} confirms new accounts by "
        "hand, usually within a day or two. Everything here works in the "
        "meantime, and church-wide messages appear once they have."
    ),

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

    # Profile header and the journey strip
    "profile_member_for": "{stage} \u00b7 {days} days",
    "profile_member_new": "{stage} \u00b7 joined today",
    "journey_label": "Your journey",
    "journey_hint": "{meaning}",
    "profile_edit": "Edit",
    "profile_done": "Done",

    # What a member can change themselves
    "details_heading": "Your information",
    "details_hint": "Keep this current so the church can reach you.",
    "details_first": "First name",
    "details_last": "Last name",
    "details_phone": "Phone",
    "details_phone_hint": "Where the church can text or call you.",
    "details_birthday": "Birthday",
    "details_birthday_hint": "Optional. Day and month are what we use.",
    "details_address": "Street address",
    "details_city": "City",
    "details_postal": "ZIP",
    "details_address_note": "Your address is shared with everyone in your family.",
    "details_email_locked": (
        "Email is the address you sign in with, so the church office changes "
        "that one. Ask them and it is done in a minute."
    ),
    "details_save": "Save my information",
    "details_saved": "Saved. Thank you.",
    "details_unchanged": "Nothing changed.",
    "details_name_required": "We need both a first and last name.",
    "details_birthday_bad": "That birthday did not look like a date.",
    "details_birthday_future": "A birthday cannot be in the future.",
    "details_event": "Updated their own details in the app",
    "details_event_detail": "Changed: {fields}",

    # Shortcut rows to the rest of the app
    "row_family": "Family",
    "row_family_none": "Nobody on file yet",
    "row_groups": "Groups",
    "row_groups_none": "Not in a group yet",
    "row_serving": "Serving",
    "row_serving_none": "Not scheduled",
    "row_serving_count": "{count} coming up",
    "row_notifications": "Notifications",
    "row_notifications_hint": "Email and this device",
    "row_details_sub": "Name, phone, birthday, address",
    "row_email_sub": "What the church emails you",
    "row_blocked_count": "{count} blocked",
    # Adding your own children, ready for check-in
    "family_add": "Add a family member",
    "family_add_hint": (
        "Add your children here so they can be checked in on Sunday. The kids "
        "team sees their name, their age, and anything you tell us."
    ),
    "family_first": "First name",
    "family_last": "Last name",
    "family_last_hint": "Leave blank to use yours.",
    "family_birthday": "Birthday",
    "family_birthday_hint": "So the kids team puts them in the right room.",
    "family_is_child": "This is a child, for kids check-in",
    "family_notes": "Anything the kids team should know",
    "family_notes_hint": "Allergies, a nickname, how they do with drop-off.",
    "family_save": "Add them",
    "family_added": "{name} added to your family.",
    "family_first_required": "We need at least a first name.",
    "family_duplicate": "{name} is already on your family.",
    "family_full": "A family can hold {max} people. Ask the church office if you need more.",
    "family_household_name": "The {last} family",
    "family_event": "Added by a parent in the app",
    "family_event_detail": "By {name}",
    "family_remove": "Remove",
    "family_remove_confirm": "Remove this person from your family?",
    "family_removed": "{name} removed from your family.",
    "family_removed_event": "Removed by a parent in the app",
    "family_remove_missing": "We could not find that person on your family.",
    "family_child_label": "Child",
    "family_check_in_ready": "Ready for check-in",
    "family_you": "You",

    "row_account": "Account and privacy",
    "row_account_sub": "Password, privacy, delete your account",

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

    # Notifications
    "push_heading": "Notifications on this device",
    "push_hint": (
        "A short note on your lock screen when something needs you. It never "
        "says anything private, only enough to open the app."
    ),
    "push_on": "Turn them on",
    "push_off": "Turn them off on this device",
    "push_enabled": "On for this device",
    "push_devices": "On for {count} of your devices",
    "push_none": "Off",
    "push_blocked": (
        "Your browser is blocking notifications. Turn them back on in your "
        "browser settings for this site, then come back here."
    ),
    "push_unsupported": (
        "This browser cannot do notifications. On an iPhone, add the app to "
        "your home screen first and open it from there."
    ),
    "push_saved": "Notifications are on for this device.",
    "push_removed": "Notifications are off for this device.",

    # Deleting an account
    "privacy_link": "What is stored about you",
    "delete_heading": "Delete your account",
    "delete_body": (
        "This removes your login straight away and signs you out everywhere. "
        "You will not be able to sign in again unless the church sets you up "
        "a new one."
    ),
    "delete_record": (
        "Your record at {church} belongs to the church, in the same way a "
        "paper roll would, so it stays. Ask the office if you want that "
        "removed too and they will do it."
    ),
    "delete_confirm_label": "Type your password to confirm",
    "delete_button": "Delete my account",
    "delete_wrong_password": "That password is not right, so nothing was deleted.",
    "delete_done": "Your account is deleted. Thanks for letting us know.",
    "delete_last_staff": (
        "You are the only staff account at this church. Deleting it would "
        "lock everybody out, so make somebody else staff first."
    ),
    "delete_gone_title": "Your account is deleted",
    "delete_gone": (
        "It is gone. If you change your mind, ask {church} and they will set "
        "you up again."
    ),

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

    "session_heading": "Sessions",
    "session_add": "Add a session",
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
    "session_save": "Add session",
    "session_saved": "Added.",
    "session_delete": "Delete",
    "session_deleted": "Deleted.",
    "session_none": "No sessions yet. Add the first one below.",
    "session_title_required": "Every day needs a title.",

    "format_help": (
        "Formatting: a blank line starts a paragraph, # for a heading, "
        "> for scripture, - for a list, **bold** and *italic*."
    ),

    # Verse of the week
    "verse_heading": "Verse of the week",
    "verse_intro": (
        "Shown at the top of every member's Home tab. A new week starts on "
        "Sunday. Schedule weeks ahead and each one appears on its Sunday."
    ),
    "verse_live": "Showing now",
    "verse_none": "No verse yet. Members see nothing on Home until you add one.",
    "verse_week_label": "Week of {date}",
    "verse_field_reference": "Reference",
    "verse_field_reference_placeholder": "Hebrews 10:24",
    "verse_field_text": "Verse",
    "verse_field_text_placeholder": (
        "Paste the verse in your church's translation, or leave this blank and "
        "we fill it in from the World English Bible."
    ),
    "verse_field_translation": "Translation",
    "verse_field_translation_placeholder": "NIV",
    "verse_field_week": "Week starting",
    "verse_add": "Set the verse",
    "verse_save": "Save",
    "verse_edit": "Edit",
    "verse_delete": "Delete",
    "verse_delete_confirm": "Delete this verse? Members go back to the previous week's.",
    "verse_upcoming": "Scheduled",
    "verse_past": "Earlier weeks",
    "verse_saved_live": "{reference} is on every member's Home tab now.",
    "verse_saved_scheduled": "{reference} is scheduled. It appears on Sunday, {date}.",
    "verse_deleted": "Verse deleted.",
    "verse_reference_required": "The verse needs a reference, like Hebrews 10:24.",
    "verse_text_required": (
        "We could not find that reference in the World English Bible. Check it, "
        "or paste the verse text in yourself."
    ),
    "verse_bad_date": "That is not a date.",
    "verse_week_taken": "The week of {date} already has a verse. Edit that one instead.",

    # Staff library, laid out like the approved demo
    "library_heading": "Published to your people",
    "library_intro": (
        "Written for {church} and delivered inside your app. Members never see "
        "another brand. Click any resource to see the plan."
    ),
    "add_resource": "Add a resource",
    "created_hint": "Next you add the sessions, then publish.",
    "cancel": "Cancel",
    "search": "Search",
    "search_placeholder": "Search by title",
    "search_none": "Nothing matches that.",
    "edit_plan": "Edit plan",
    "click_to_open": "Click card to open",
    "card_sessions": "{count} sessions",
    "card_one_session": "1 session",
    "card_started": "{count} started",
    "started_people": "{count} people started this plan",
    "started_people_one": "1 person started this plan",
    "started_people_none": "Nobody has started this yet",
    "open_member": "Open in member app",
    "minutes": "{count} min",
    "no_passage": "No passage set",
    "close": "Close",
    "archived_heading": "Archived",
    "archived_hint": "Hidden from members and from the list above. Restore brings one back as a draft.",
    "restore": "Restore",
    "restored": "{title} restored as a draft.",

    # Editing, before and after publishing
    "details_heading": "Details",
    "field_title": "Title",
    "field_kind": "Type",
    "field_summary": "Description",
    "field_summary_placeholder": "One or two lines members see under the title",
    "field_cover": "Cover",
    "save_details": "Save",
    "live_banner": "Live. Members see every change the moment you save.",
    "draft_banner": "Draft. Only staff can see this until you publish it.",
    "saved_live": "Saved. Members see the change now.",
    "saved_draft": "Saved.",
    "session_saved_live": "Added. Members see the new session now.",
    "session_edit": "Edit",
    "session_save_changes": "Save changes",
    "session_minutes": "Minutes",
    "move_up": "Move up",
    "move_down": "Move down",
    "session_delete_confirm": "Delete this session? Anyone who marked it done loses that tick.",
    "session_last_live": (
        "That is the only session in a live plan. Unpublish it first, or add "
        "another session before deleting this one."
    ),
    "sessions_total": "{count} sessions, about {minutes} minutes",

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
    "passage_fallback": (
        "Showing the {name} because your church's translation was not "
        "available just now."
    ),
    "passage_missing": (
        "We do not have the text for {reference} loaded yet. Open it in your "
        "own Bible and the rest of the day still works."
    ),
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
    "files_heading": "Team files",
    "files_empty": "No files yet. Upload the curriculum, a chart, or a checklist.",
    "files_hint": "PDFs only, up to {max} MB each. Everyone on this team sees them in their Serve tab.",
    "file_title": "What it is",
    "file_title_placeholder": "October kids curriculum",
    "file_choose": "Choose a PDF",
    "file_upload": "Upload",
    "file_by": "added by {name}",
    "file_delete": "Delete",
    "file_delete_confirm": "Delete this file? The team stops seeing it straight away.",
    "file_deleted": "{title} deleted.",
    "file_added": "Added. Everyone on {team} can open it now.",
    "file_missing": "Choose a PDF to upload.",
    "file_empty": "That file is empty.",
    "file_not_pdf": "That is not a PDF. Save it as a PDF and try again.",
    "file_too_big": "That file is bigger than {limit} MB. Split it up, or share a link to it instead.",
    "file_quota": "Your church has used all {limit} MB of file space. Delete something first.",
    "file_413": "That file was too large to upload.",
    "member_files_heading": "Team files",
    "status_draft_hint": "Draft. Only staff and leaders can see this plan.",
    "status_published_hint": "Published. Everyone serving sees this plan in their Serve tab.",
    "publish": "Publish",
    "unpublish": "Unpublish",
    "published": "{name} is published. The team sees the plan in the app.",
    "unpublished": "{name} is back to a draft. The team no longer sees the plan.",
    "publish_empty": "Add items to the running order before publishing it.",
    "member_plan_pending": "The running order is still being put together. It shows here once it is published.",
    "savetemplate_heading": "Save as a template",
    "savetemplate_hint": (
        "Keep this running order for next time. New services on the Services "
        "page can start from it, and changing this service later never changes "
        "the template."
    ),
    "savetemplate_new": "A new template",
    "savetemplate_replace": "Replace {name}",
    "savetemplate_name_placeholder": "Template name, like Sunday 10:30",
    "savetemplate_include_needs": "Include the roles this service needs",
    "savetemplate_save": "Save template",
    "savetemplate_created": "{name} saved as a template with {count} items. Pick it when you plan the next service.",
    "savetemplate_replaced": "{name} now uses this running order ({count} items).",
    "savetemplate_empty": "Add items to the running order before saving it as a template.",
    "savetemplate_name_required": "Give the template a name.",
    "savetemplate_name_taken": "There is already a template called {name}. Pick Replace {name} to update it, or use a different name.",
    "headcount_heading": "Headcount",
    "headcount_placeholder": "How many were here",
    "headcount_save": "Save",
    "headcount_hint": "Everyone in the room, kids included. The dashboard's attendance number comes from here.",
    "headcount_saved": "Headcount saved.",
    "headcount_bad": "That is not a headcount. Use a whole number.",
    "title": "Services",
    "subtitle": "Plan Sunday and fill the team.",

    # The week strip
    "ahead_heading": "Plan ahead",
    "ahead_hint": "Plan weeks out, not the night before. Open a Sunday to work on it.",
    "ahead_count": "{count} scheduled",
    "ahead_roles": "{filled} of {total} roles",
    "ahead_no_roles": "No roles listed",
    "ahead_add": "Add a Sunday",

    # The plan header
    "plan_for": "{day} at {time}",
    "plan_ends": "Ends at {time}. Change any element and every time after it moves.",
    "songs_word": "Song",
    "element_word": "Element",
    "roles_filled": "{filled} of {total} roles filled",
    "roles_none": "No roles listed for this service",

    # The side panel
    "open_roles_heading": "Open roles",
    "open_roles_none": "Every role is filled.",
    "open_roles_count": "{count} open",
    "open_roles_filled": "{filled} of {wanted} filled",
    "add_heading": "Add to the plan",
    "waiting_heading": "Waiting on",
    "waiting_none": "Everybody has answered.",
    "team_heading": "Who is serving",
    "team_none": "Nobody asked yet.",

    # Songs on the plan
    "songs_heading": "Songs for this Sunday",
    "songs_none": "No songs in the plan yet.",
    "songs_ccli": "CCLI {number}",
    "songs_no_ccli": "No CCLI number on file",
    "songs_open": "Open in SongSelect",
    "songs_licence_note": (
        "Charts attached from SongSelect open only for staff, leaders, and "
        "the people scheduled on this service."
    ),
    "chart_open": "Chart",
    "key_set": "{title} is now in {key}.",
    "key_change": "Key",

    # Service types and templates
    "types_title": "Service types",
    "types_subtitle": "The shape a Sunday usually takes, so you are not rebuilding it every week.",
    "type_name": "What kind of service",
    "type_name_placeholder": "Sunday Morning",
    "type_add": "Add the type",
    "type_added": "{name} added. Build its usual running order.",
    "types_empty": "No service types yet.",
    "types_empty_hint": (
        "Sunday Morning, Wednesday Youth, Christmas Eve. Each one holds the "
        "running order that rarely changes."
    ),
    "template_heading": "Its usual running order",
    "template_hint": (
        "Copied into every new service of this type. Leave songs blank if you "
        "pick them each week; the slots still appear."
    ),
    "template_empty": "Nothing in the template yet.",
    "template_minutes": "{minutes} minutes",
    "needs_heading": "Who it usually needs",
    "needs_hint": "Shown as filled or short on every plan of this type.",
    "needs_position": "Position",
    "needs_wanted": "How many",
    "needs_add": "Add",
    "needs_added": "{name} added.",
    "needs_empty": "No positions listed yet.",
    "needs_line": "{position}, {wanted}",

    # Plan
    "section_add": "Add a section",
    "section_placeholder": "Worship",
    "section_hint": "A heading to group what follows. It takes no time of its own.",
    "move_up": "Up",
    "move_down": "Down",
    "moved": "Moved.",
    "starts_at_item": "{time}",
    "plan_runs": "Runs {start} to {end}",
    "plan_total": "{minutes} minutes of plan",
    "copy_heading": "Start from a previous week",
    "copy_hint": (
        "Replaces this plan with that one. Who served is not copied: last "
        "week's team is not this week's, and nobody should find out they are "
        "playing by reading it."
    ),
    "copy_do": "Copy it here",
    "copied": "Copied {count} items from {name}.",
    "copy_none": "No earlier service to copy from.",

    "staffing_heading": "Still needed",
    "staffing_full": "Fully staffed.",
    "staffing_line": "{position}: {filled} of {wanted}",
    "staffing_short": "{position}: short {count}",
    "staffing_accepted": "{count} accepted",
    "staffing_none": "No positions listed for this service.",

    "new_heading": "Plan a service",
    "name": "What is it",
    "name_placeholder": "Sunday",
    "use_type": "Use a type",
    "no_type": "One off, no type",
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
    "songs_subtitle": "Titles, keys, CCLI numbers, and the charts your band plays from.",
    "songs_why": (
        "Add a song by hand here. Lyrics are never stored as text. Charts are "
        "kept as the PDF you downloaded from SongSelect under your church's "
        "licence, and open only for staff, leaders, and whoever is scheduled "
        "on a service that uses the song."
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

    # SongSelect import
    "import_heading": "Import from SongSelect",
    "import_steps": (
        "In SongSelect, download a song as a PDF chart, a USR file, a text "
        "file, or ChordPro, and drop up to {max} here at once. Each one adds "
        "the song with its writers, CCLI number, and key. A PDF is also kept "
        "as the song's chart for the band."
    ),
    "import_why_api": (
        "CCLI only opens a direct SongSelect connection to a short list of "
        "approved software partners, so downloads are the way in."
    ),
    "import_browse": "Find songs on SongSelect",
    "import_choose": "SongSelect files",
    "import_submit": "Import the songs",
    "import_none_chosen": "Choose at least one file downloaded from SongSelect.",
    "import_too_many": "That is {count} files. Import up to {max} at a time.",
    "import_added": "{count} added to your songs.",
    "import_updated": "{count} already here, details filled in.",
    "import_unchanged": "{count} already here with nothing new.",
    "import_skipped": "{name}: {why}",
    "import_why_empty": "the file is empty.",
    "import_why_too_big": "too big to be a SongSelect download.",
    "import_why_pdf_unreadable": (
        "this PDF has no text we can read, which usually means a scan. Add "
        "the song by hand, then attach the PDF on its row."
    ),
    "import_why_pdf_no_ccli": (
        "no CCLI song number in this PDF, so it does not look like a "
        "SongSelect chart. Add the song by hand, then attach the PDF on its row."
    ),
    "import_charts": "Charts attached: {count}.",
    "chart_heading": "Charts",
    "chart_attach": "Attach a PDF chart",
    "chart_label": "What is it (optional)",
    "chart_label_placeholder": "Chord chart in G",
    "chart_upload": "Attach",
    "chart_added": "Chart attached to {title}.",
    "chart_duplicate": "That chart is already on {title}.",
    "chart_deleted": "{title} removed.",
    "chart_delete": "Remove",
    "chart_delete_confirm": "Remove this chart? Anyone scheduled will lose it from their plan.",
    "import_why_wrong_type": "not a SongSelect download. Use USR, TXT, or ChordPro.",
    "import_why_unreadable": "we could not find a song title in it.",
    "import_why_no_title": "we could not find a song title in it.",
    "import_why_no_ccli": (
        "no CCLI song number in it, so it does not look like a SongSelect "
        "download."
    ),
    "songselect_link": "SongSelect",
    "licence_line": "CCLI License #{number}",
    "licence_missing": "Add your CCLI license number in Settings so it shows here.",

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
    "forgot_email_subject": "Your check-in code at {church}",
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
    "chat_email_subject": "{name} posted in {room}",
    "chat_email_body": (
        "Hello {first_name},\n\n"
        "{name} posted in {room}:\n\n"
        "  {excerpt}\n\n"
        "Open the app to read the rest and reply:\n{link}\n\n"
        "You get one of these at most every half hour per room. To stop them, "
        "open the app, go to You, and turn off Chat messages."
    ),
    "message_options": "Message options",
    "member_compose_placeholder": "Message",
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
    # Reporting and blocking
    "report": "Report",
    "report_heading": "Report this message",
    "report_reason": "What is wrong with it (optional)",
    "report_submit": "Send the report",
    "report_reason_label": "Their reason",
    "report_done": (
        "Reported. Church staff will look at it. It stays visible to others "
        "until they decide, so one reader cannot remove anybody's message."
    ),
    "report_again": "You already reported this. Staff have it.",
    "report_own": "That is your own message. You can ask staff to remove it.",

    "block": "Block {name}",
    "block_confirm": (
        "Block {name}? You will stop seeing their messages everywhere in the "
        "app, straight away. They are not told. Staff are, so they can help."
    ),
    "block_done": "Blocked. You will not see messages from {name}.",
    "block_self": "You cannot block yourself.",
    "block_staff_author": "Messages with no person behind them cannot be blocked. Report it instead.",
    "blocked_hidden": "{count} hidden from people you blocked.",

    "blocks_heading": "People you blocked",
    "blocks_none": "You have not blocked anybody.",
    "blocks_hint": "Their messages are hidden from you everywhere in the app.",
    "unblock": "Unblock",
    "unblocked": "Unblocked {name}. You will see their messages again.",

    "filter_refused": (
        "That was not posted. \"{terms}\" is not allowed in chat here. "
        "Change it and send again."
    ),

    # Agreeing to the community standards before chat opens
    "agree_title": "Before you open chat",
    "agree_intro": (
        "Chat is for the people of {church}. Please read these and agree "
        "before you post."
    ),
    "agree_submit": "I agree",
    "agree_done": "Thanks. Chat is open.",
    "agree_full": "Read the full standards",

    # Staff: reports
    "reports_title": "Reported messages",
    "reports_subtitle": "Decide each one. Every decision is recorded.",
    "reports_waiting": "{count} waiting",
    "reports_none": "Nothing waiting. Nobody has reported a message.",
    "reports_link": "Reported messages ({count})",
    "report_from": "Reported by {name}",
    "report_from_block": "{name} blocked the author",
    "report_in": "In {room}",
    "report_by_author": "Written by {name}",
    "report_already_removed": "The message has already been removed.",
    "report_remove": "Remove the message",
    "report_keep": "Keep it",
    "report_removed": "Removed. The report is closed.",
    "report_kept": "Kept. The report is closed.",
    "reports_recent": "Recently decided",
    "report_decided_by": "{status} by {name}",

    "alert_subject": "A message was reported in {room}",
    "alert_body": (
        "{reporter} {action} a message by {author} in {room}.\n\n"
        "{reason}"
        "Open the app to decide what happens to it:\n{link}\n\n"
        "The report stays open until somebody on staff removes the message "
        "or keeps it.\n\n{church}"
    ),
    "alert_action_report": "reported",
    "alert_action_block": "blocked the author of",
    "alert_reason": "Their reason: {reason}\n\n",

    "post_empty": "Say something first.",
    "post_forbidden": "You cannot post here.",
    "posted": "Sent.",

    "member_add": "Add someone",
    "member_added": "{name} added.",
    "member_remove": "Remove",
    "member_removed": "{name} removed.",
    "members_empty": "Nobody added yet.",
    "already_in": "{name} is already in this room.",

    "delete_confirm": "Delete this message? Everyone stops seeing it straight away.",
    "delete_chat": "Delete this chat",
    "delete_chat_confirm": "Delete {title}? Every message in it is cleared and it disappears for everyone. This cannot be undone.",
    "delete_chat_hint": "Archive hides a chat and keeps its messages. Delete clears every message too. Both are recorded in Settings under What happened here.",
    "chat_deleted": "{title} deleted. {count} messages cleared.",
    "recent_heading": "Latest messages everywhere",
    "recent_hint": "Every chat and announcement in one list, newest first, so you can keep an eye on things without opening each room.",
    "recent_empty": "Nobody has posted yet.",
    "announce_limit": "You have posted {count} church-wide announcements today. Try again tomorrow, or ask the church office to share it.",
    "announce_member_note": "This goes to everyone at {church}. Staff can remove anything posted here.",
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
    "staff_readonly": "You cannot post in this chat.",
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


# The cost comparison, per spec v3 section C.6. One list, so the number is one
# edit rather than a hunt through markup. The Bible is deliberately not counted
# as a saving: most churches use a free app already, so claiming it as a
# replaced line item is the kind of overstatement a pastor checks and remembers.
REPLACES = (
    {"name": "Planning Center", "cents": 19900,
     "note": "People, services, teams, check-in"},
    {"name": "Website and hosting", "cents": 9500,
     "note": "The site, the domain, the updates"},
    {"name": "Giving platform fees", "cents": 0,
     "note": "Unchanged. You keep Tithely and your rates."},
)

INCLUDED_NOT_SAVED = (
    {"name": "Bible and reading plans",
     "note": "Included, not counted as a saving. Most churches already use a "
             "free app for this."},
)

DOS_PRICE_CENTS = 10000


SETTINGS = {
    "title": "Settings",
    "subtitle": "Your brand, your data, your app.",

    # Accounts staff create





    "signup_hint": (
        "With this off, staff create accounts. With it on, anyone who reaches "
        "your sign-in page can make one and confirm it by email."
    ),
    "signup_toggle_on": "Let anyone create an account",
    "signup_toggle_off": "Only the church creates accounts",
    "signup_changed_on": "Anyone with your address can now create an account.",
    "signup_changed_off": "Only staff create accounts now.",

    # Accounts staff create
    "accounts_heading": "People who can sign in",
    "accounts_hint": (
        "Create an account and we email them a link to set their own "
        "password. You never type one, so it is never a password two people "
        "know."
    ),
    "accounts_name": "Their name",
    "accounts_email": "Their email",
    "accounts_role": "What they can do",
    "accounts_create": "Create the account",
    "accounts_created": "{name} can sign in. We emailed them a link to set a password.",
    "accounts_exists": "{email} already has an account here.",
    "accounts_name_required": "We need a name and an email address.",
    "accounts_empty": "Nobody has an account yet.",

    "role_member": "Member: their own record only",
    "role_leader": "Leader: the roster, groups, services, kids",
    "role_staff": "Staff: everything, including giving and settings",
    "role_changed": "{name} is now {role}.",
    "role_change": "Change",

    "account_active": "Active",
    "account_off": "Switched off",
    "account_unverified": "Has not confirmed their email",
    "account_never": "Never signed in",
    "account_last_seen": "Last in {when}",
    "account_deactivate": "Switch off",
    "account_reactivate": "Switch back on",
    "account_deactivated": "{name} can no longer sign in.",
    "account_reactivated": "{name} can sign in again.",
    "account_resend": "Send a set-password link",
    "account_temp": "Give them a temporary password",
    "account_temp_hint": (
        "For somebody whose email is dead or who is standing in front of you. "
        "We generate it, you read it out, and they have to change it before "
        "they can do anything. You never choose it, so it never becomes a "
        "password two people know."
    ),
    "account_temp_made": (
        "Temporary password for {name}: {password}. Read it to them now. It "
        "is not shown again and they must change it at sign-in."
    ),
    "account_temp_self": (
        "Use the forgot-password link for your own account rather than "
        "issuing yourself a temporary one."
    ),
    "account_resent": "Sent to {email}.",

    "account_self": "You cannot change your own access.",
    "account_last_staff": (
        "That would leave nobody with staff access, and nobody able to undo "
        "it. Make somebody else staff first."
    ),

    "invite_subject": "Your {church} account",
    "invite_body": (
        "Hello {name},\n\n"
        "{actor} has set up an account for you at {church}.\n\n"
        "Open this link to choose a password and sign in:\n\n"
        "{link}\n\n"
        "The link works once and expires in {minutes} minutes. If it has "
        "expired by the time you open this, ask them to send another.\n\n"
        "{church}"
    ),

    "signup_heading": "Who can create an account",
    "signup_on": "Anyone can create their own account",
    "signup_off": "Only we create accounts",
    "signup_warning": (
        "Turning this on means a stranger who finds the address can read your "
        "church-wide announcements. It does not give anyone access to a "
        "person's record: an account only connects to somebody on your roster "
        "when the email address matches exactly one person and they have "
        "confirmed it from that inbox."
    ),

    "brand_heading": "Your brand",
    "church_name": "Church name",
    "app_name": "App name in the store",
    "custom_domain": "Web address your people use",
    "custom_domain_hint": (
        "The hostname you pointed at this app, without https or a trailing "
        "slash. Until this matches, that address shows a page saying no "
        "church is set up here."
    ),
    "custom_domain_bad": "{value} is not a hostname. It should look like app.yourchurch.org.",
    "custom_domain_taken": "{value} already points at another church. One address, one church.",
    "custom_domain_set": "{value} now opens this church.",
    "app_domain": "Name in the app store listing",
    "timezone": "Timezone",
    "timezone_hint": (
        "Meeting and service times are read in this zone. A wrong value here "
        "produces no error at all, so it is worth checking."
    ),
    "accent": "Primary colour",
    "accent_placeholder": "#485B38",
    "accent_hint": (
        "Every screen updates instantly, including the member app. Any hex "
        "works as long as white text stays readable on it."
    ),
    "save_brand": "Save",
    "brand_saved": "Saved. Every screen is already using it.",
    "accent_rejected": "{reason}",
    "timezone_rejected": "{value} is not a timezone. Try America/Chicago.",

    "replaces_heading": "What this replaces",
    "replaces_total": "Current total",
    "replaces_ours": "Discipleship Operating System",
    "replaces_saving": (
        "Net saving of {monthly} a month, {yearly} a year, before counting "
        "staff hours."
    ),
    "replaces_note": "Per month.",
    "included_heading": "Included, not counted",

    "audit_heading": "What happened here",
    "audit_intro": (
        "Sign-ins, access changes, provider keys, matched gifts, deleted "
        "messages, and every child collected. Append only: nothing on this "
        "screen can be edited or removed, including by us."
    ),
    "audit_empty": "Nothing recorded yet.",
    "audit_all": "Everything",
    "audit_filter": "Show",
    "audit_by": "by {name}",
    "audit_system": "by the system",
    "audit_count": "{count} in the last 30 days",
    "audit_retention": "Entries are kept for {days} days.",

    "bible_heading": "Scripture",
    "bible_body": (
        "Reading plans show the passage inline from the World English Bible, "
        "which is public domain and needs nobody's permission."
    ),
    "bible_licensed": (
        "A licensed translation such as NIV runs through your church's own "
        "YouVersion registration. We never store the text of a licensed "
        "translation, so if that service is unavailable your people read the "
        "World English Bible and the page says so."
    ),

    # Giving shortcut
    "giving_heading": "Giving",
    "giving_linked": (
        "The Giving tab in the sidebar opens {url} in a new tab. Change that "
        "link, match gifts to people, and see who stopped giving from the "
        "giving setup page."
    ),
    "giving_unlinked": (
        "Add your giving platform's sign-in link and the Giving tab will open "
        "it directly."
    ),
    "giving_setup": "Giving setup and reports",

    # CCLI
    "ccli_heading": "CCLI license",
    "ccli_hint": (
        "Your Church Copyright License number. It shows on the songs page and "
        "on every service plan so whoever builds the slides has it for the "
        "lyric footer. Find it in your CCLI profile under Licenses."
    ),
    "ccli_label": "License number",
    "ccli_save": "Save the number",
    "ccli_saved": "CCLI license number saved.",
    "ccli_cleared": "CCLI license number removed.",
    "ccli_bad": "A CCLI license number is 4 to 10 digits.",
    "ccli_songs": "Songs and SongSelect import",

    # Member announcements
    "announce_heading": "Who can post church-wide announcements",
    "announce_on": "Staff, leaders, and members",
    "announce_off": "Staff only",
    "announce_hint": (
        "With this on, approved members can post to church-wide announcement "
        "chats, up to 5 a day each. The word filter applies, anyone can "
        "report a post, and staff can delete anything from Messages. Only "
        "staff can email an announcement."
    ),
    "announce_toggle_on": "Let members post announcements",
    "announce_toggle_off": "Only staff post announcements",
    "announce_changed_on": "Members can now post church-wide announcements.",
    "announce_changed_off": "Only staff can post announcements now.",

    # Email delivery
    "email_heading": "Email delivery",
    "email_intro": (
        "Whether confirmation links, password resets, and church email are "
        "actually leaving. Check here first when somebody says they never got "
        "the link."
    ),
    "email_ok": "Working",
    "email_problem": "Needs attention",
    "email_off": "Not sending real email",
    "email_sent": "Sent",
    "email_waiting": "Waiting",
    "email_failed": "Gave up",
    "email_last_sent": "Last email left {when}.",
    "email_never_sent": "No email has left this church yet.",
    "email_from": "Sent from {address}",
    "email_off_body": (
        "This server is set to write email to its log instead of sending it. "
        "Nobody receives anything. Set MAIL_TRANSPORT to resend on the web "
        "service and the outbox job in Render."
    ),
    "email_no_key": (
        "There is no Resend API key on this server. Add RESEND_API_KEY to the "
        "web service and to the outbox job in Render, both of them."
    ),
    "email_stale": (
        "The oldest email has been waiting {minutes} minutes. The sending job "
        "runs every 5, so it is not running or not finishing. In Render, open "
        "journey-dos-outbox and read its latest log."
    ),
    "email_problems_heading": "Recent problems",
    "email_attempts": "{count} tries",
    "email_test_button": "Send a test email to me",
    "email_test_hint": "Goes to {email} immediately and shows you exactly what the provider said.",
    "email_test_subject": "Test email from {church}",
    "email_test_body": (
        "Hello {name},\n\n"
        "This is a test from the email settings at {church}. If you are "
        "reading it, confirmation links and password resets are reaching "
        "people.\n\n"
        "{church}"
    ),
    "email_test_sent": "Sent. Check {email}, including spam. If it is not there in two minutes, the address is being filtered on the receiving end.",
    "email_test_console": "Written to the server log, not sent. This server is not set up to send real email.",
    "email_test_failed": "Not sent. The provider said: {error}",
    "email_test_not_queued": "Could not send a test: {reason}",
    "email_why_blocked_client": (
        "Resend's firewall turned the request away before it reached your "
        "account. This was a bug in how the app identified itself, fixed in "
        "the September 19 update. Once that is deployed, use Retry below to "
        "send what was stuck."
    ),
    "email_retry_button": "Retry the ones that failed",
    "email_retry_hint": "Sends failed email from the last 3 days again, now. Confirmation links stay valid for 3 days.",
    "email_retry_done": "Retried {total}: {sent} sent, {failed} still failing.",
    "email_retry_none": "Nothing from the last 3 days needs retrying.",
    "email_why_domain_unverified": (
        "Your sending domain is not verified with Resend. In Resend, open "
        "Domains, add thejourneychurchsemo.com, and add the DNS records it "
        "shows at your domain registrar. Nothing sends until it says Verified."
    ),
    "email_why_test_mode": (
        "Resend is still in test mode, which only delivers to the account "
        "owner's own address. Verifying your domain in Resend ends test mode."
    ),
    "email_why_bad_key": (
        "Resend does not recognise the API key. Create a new sending key in "
        "Resend and paste it into RESEND_API_KEY on both Render services."
    ),
    "email_why_restricted_key": (
        "The API key is not allowed to send. Create one with Sending access "
        "in Resend and replace RESEND_API_KEY on both Render services."
    ),
    "email_why_bad_from": (
        "The From address is not one Resend accepts. It must be on your "
        "verified domain, like no-reply@thejourneychurchsemo.com."
    ),
    "email_why_rate_limited": "Resend is limiting how fast we send. These retry on their own.",
    "email_why_network": "The server could not reach Resend. These retry on their own.",

    "support_heading": "Getting help",
    "support_body": (
        "Email isaac@betweensundaysconsulting.com and a person will answer. "
        "There is no ticket queue and no chatbot."
    ),
    "support_what_to_include": (
        "It helps to say what you were doing and roughly when. The screen "
        "above gives us the rest."
    ),
    "support_status": "Everything on this page is running.",

    # ---- The rows on this screen -------------------------------------
    # Every setting is a closed row until it is opened, the way the You tab
    # works for members. The line under each name is what staff read to
    # decide whether they need to open it at all.
    "rows_hint": "Open a setting to change it. Everything is saved where you change it.",
    "brand_pill": "White label",
    "brand_sub": "{name} \u00b7 {timezone}",
    "accounts_sub_one": "1 person can sign in",
    "accounts_sub": "{count} people can sign in",
    "accounts_sub_none": "Nobody has an account yet",
    "signup_sub_on": "Anyone with your address can create their own login",
    "signup_sub_off": "Only the church creates accounts",
    "giving_sub_on": "Connected",
    "giving_sub_off": "Not connected yet",
    "ccli_sub": "License {number}",
    "ccli_sub_none": "No license number saved",
    "announce_sub_on": "Members can post to the whole church",
    "announce_sub_off": "Staff post announcements",
    "email_sub": "{sent} sent \u00b7 {queued} waiting \u00b7 {failed} failed",
    "replaces_sub": "{amount} a month of software you no longer need",
    "audit_sub": "Who changed what, and when",
    "bible_sub": "{verses} verses across {books} books",
    "support_sub": "Email a person. No ticket queue.",
}


PWA = {
    "description": (
        "{church} in your pocket: what is next for you, what you are reading, "
        "your groups, and your kids check-in code."
    ),
    "shortcut_read": "Reading",
    "shortcut_you": "Your details",

    "offline_title": "You are offline",
    "offline_body": (
        "This page needs a connection. Anything you have already opened is "
        "still here."
    ),
    "offline_hint": "It will load as soon as you are back on a signal.",

    "install_prompt": "Add {church} to your home screen",
    "install_action": "Add it",
    "install_dismiss": "Not now",
    "install_ios": (
        "In Safari, tap the share button and then Add to Home Screen."
    ),
}


PRIVACY = {
    "title": "Privacy",
    "updated": "Last updated {date}",
    "intro": (
        "{church} uses this app to keep track of its own people. Between "
        "Sundays Consulting builds and runs it for them. This page says "
        "exactly what is stored, why, and how to get rid of it."
    ),

    "collect_heading": "What is stored",
    "collect": (
        {
            "name": "Your name and contact details",
            "why": "So the church can reach you and know who you are.",
        },
        {
            "name": "Where you are in the church's own steps",
            "why": (
                "Visitor, attender, member and so on, plus notes staff write "
                "about conversations they have had with you."
            ),
        },
        {
            "name": "Your household, and your children if you check them in",
            "why": (
                "A child's name and the times they were checked in and "
                "collected, and who collected them. This is a safety record "
                "and the church keeps it."
            ),
        },
        {
            "name": "Giving, if your church connects its giving platform",
            "why": (
                "Amounts and dates are copied here read only. Card numbers "
                "are never seen by this app at any point."
            ),
        },
        {
            "name": "What you read, join, and serve on",
            "why": "Reading progress, group RSVPs, and serving answers.",
        },
        {
            "name": "Messages you send in the app",
            "why": (
                "Announcements, rooms, and direct conversations, plus any "
                "report you file and anybody you block, so staff can act on "
                "them. A report never copies the words of the message."
            ),
        },
        {
            "name": "Sign-in records",
            "why": (
                "When you signed in, and when somebody changed access or "
                "collected a child. Kept for 400 days."
            ),
        },
    ),

    "not_heading": "What is never stored",
    "not_collected": (
        "Card or bank numbers. Giving happens on your church's own giving "
        "platform and this app only ever links to it.",
        "Your location. Nothing here tracks where you are.",
        "Your contacts, photos, microphone, or anything else on your device.",
        "Advertising identifiers. Nothing here is sold, shared with "
        "advertisers, or used to target you.",
    ),

    "share_heading": "Who it is shared with",
    "share": (
        "Nobody, beyond the services needed to run the app: Resend sends the "
        "email, Render hosts it, and your church's own giving platform is "
        "linked to rather than replaced. Your data is never sold and is never "
        "shared with another church."
    ),

    "rights_heading": "Getting rid of it",
    "rights": (
        "You can delete your account yourself, in the app, on the You screen. "
        "That removes your login immediately. Your record at the church "
        "belongs to the church, in the same way a paper roll would, so ask "
        "the office if you want that removed too."
    ),
    "children_heading": "Children",
    "children": (
        "Children do not have accounts. A child's name and check-in times are "
        "entered by a parent at a kiosk or by staff, and exist so the church "
        "knows who was in a room and who took them home."
    ),

    "contact_heading": "Questions",
    "contact": (
        "Ask {church} first, since it is their data. For anything about how "
        "the software itself works, email isaac@betweensundaysconsulting.com."
    ),
}


# The public support page. Required by the app stores, and the only page a
# member can reach when the thing they need help with is signing in.
#
# Written for the person who is stuck, not for a reviewer. Every answer is
# something they can act on without emailing anybody, because most of them do
# not need a human and the ones who do should not be queued behind those who
# do not.
SUPPORT = {
    "title": "Help",
    "subtitle": "The short answers first, then a person.",

    "answers_heading": "The usual questions",
    "answers": (
        {
            "q": "I cannot sign in",
            "a": (
                "Use the forgotten password link on the sign-in screen. It "
                "emails you a link that works once and lasts an hour. If the "
                "email never arrives, the address on your account is probably "
                "not the one you are checking, and {church} can fix that."
            ),
        },
        {
            "q": "I do not have an account",
            "a": (
                "{church} sets accounts up. Ask the office and they will send "
                "you a link to choose your own password."
            ),
        },
        {
            "q": "I want fewer emails",
            "a": (
                "Every email has an unsubscribe link at the bottom. You can "
                "also choose which kinds you get on the You screen in the app. "
                "Anything about your own account still reaches you, because "
                "that is not news, it is your account."
            ),
        },
        {
            "q": "I want to delete my account",
            "a": (
                "You screen, then Delete your account. It removes your login "
                "straight away. Your record at {church} belongs to the church, "
                "in the same way a paper roll would, so ask the office if you "
                "want that removed too."
            ),
        },
        {
            "q": "Where is my kids check-in code",
            "a": (
                "On the You screen, in large type. It is your household's "
                "code, so it is the same for everybody in your family."
            ),
        },
        {
            "q": "Someone is being unkind in chat",
            "a": (
                "Tap Report under the message and church staff are told "
                "straight away. Tap Block and you stop seeing that person's "
                "messages immediately, without waiting for anybody. You can "
                "undo a block on the You screen."
            ),
        },
        {
            "q": "Giving is not working",
            "a": (
                "Giving happens on {church}'s own giving platform. This app "
                "links to it and never handles the payment, so anything to do "
                "with a card or a receipt is with them."
            ),
        },
    ),

    "contact_heading": "Still stuck",
    "contact_church": (
        "Ask {church} first. They hold your record and can change anything "
        "about it."
    ),
    "contact_us": (
        "If the app itself is broken, email isaac@betweensundaysconsulting.com "
        "and a person will answer. There is no ticket queue and no chatbot."
    ),
    "contact_detail": (
        "It helps to say what you were doing and roughly when. We can find the "
        "rest."
    ),

    "privacy_link": "What is stored about you",
    "community_link": "Community standards",
    "sign_in_link": "Back to sign in",
}


# The community standards. Public, because Apple's reviewer needs to read
# them, and because a person deciding whether to join should be able to see
# the rules before they agree to them.
#
# Short on purpose. Standards nobody reads protect nobody, and every line here
# is one staff will actually enforce.
COMMUNITY = {
    "title": "Community standards",
    "subtitle": "For chat at {church}.",
    "intro": (
        "Chat is where the people of {church} talk to each other. These are "
        "the rules for it. There is no tolerance for content or behaviour "
        "that breaks them."
    ),
    "rules": (
        (
            "Be kind",
            "No insults, harassment, threats, or bullying, including in jest.",
        ),
        (
            "Keep it clean",
            "No sexual content, graphic violence, hate speech, or language "
            "you would not use in the lobby on a Sunday.",
        ),
        (
            "Protect each other",
            "Never share somebody else's private details, and nothing about "
            "children beyond what their own parent posts.",
        ),
        (
            "No selling",
            "No advertising, spam, fundraising for outside causes, or links "
            "posted to get clicks.",
        ),
        (
            "Be yourself",
            "Post as you. Do not pretend to be somebody else.",
        ),
    ),
    "enforcement_heading": "What happens if they are broken",
    "enforcement": (
        "Anybody can report a message or block the person who wrote it, from "
        "the message itself. A block works immediately. Church staff read "
        "every report, normally within a day, and can remove messages and "
        "remove somebody from chat or the app entirely."
    ),
    "contact": (
        "If something needs attention faster than that, contact {church} "
        "directly."
    ),
    "support_link": "Help",
    "privacy_link": "Privacy",
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
