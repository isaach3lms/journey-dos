"""
Automated follow up.

Sequences are data, not database rows. Editing a sequence is editing this file,
which means no admin CRUD to build and no risk of a half-configured sequence in
production. Enrollment state lives in the database.

Placeholders available in subject and body: {first_name}, {church_name},
{city}, {site_url}.

Rules that make this safe for a church:
- A sequence stops the moment a human logs a real contact. Automation never
  talks over a pastor.
- A sequence stops when the person reaches the stage it was trying to move them
  toward.
- One send per person per day maximum, enforced by the runner.
"""

SEQUENCES = {
    "welcome": {
        "name": "New connect card welcome",
        "trigger_source": ["connect card", "self signup"],
        # Stop sending once the person reaches this stage or later.
        "stop_at_stage": "Launch team",
        "steps": [
            {
                "delay_days": 0,
                "subject": "Glad you reached out, {first_name}",
                "body": (
                    "<p>{first_name},</p>"
                    "<p>Thanks for filling out the card. A real person read it, and someone "
                    "from our team will reach out to you personally in the next couple of days.</p>"
                    "<p>{church_name} is not open yet. We are gathering the people who will "
                    "open the doors with us in {city}. If that sounds like something you want "
                    "to be part of, you can read what we are asking of the launch team here: "
                    "<a href=\"{site_url}/launch-team\">{site_url}/launch-team</a></p>"
                    "<p>Glad you are here.</p>"
                ),
            },
            {
                "delay_days": 3,
                "subject": "What we are actually building",
                "body": (
                    "<p>{first_name},</p>"
                    "<p>Most people ask the same question when they hear about a new church: "
                    "why another one?</p>"
                    "<p>Here is our answer. {church_name} exists to help people live "
                    "adventurously expectant as they take their next step with God. Not the "
                    "step someone else is on. The next one that is actually yours.</p>"
                    "<p>If you have questions, reply to this email. It comes to a person.</p>"
                ),
            },
            {
                "delay_days": 10,
                "subject": "An open invitation",
                "body": (
                    "<p>{first_name},</p>"
                    "<p>We are still building the launch team. It is a real commitment: "
                    "gatherings between now and launch, one team to serve on, and giving "
                    "toward the launch.</p>"
                    "<p>If you are in, start here: "
                    "<a href=\"{site_url}/launch-team\">{site_url}/launch-team</a></p>"
                    "<p>If the timing is not right, that is a fine answer. We will keep you "
                    "posted either way.</p>"
                ),
            },
        ],
    },
    "launch_team": {
        "name": "Launch team onboarding",
        "trigger_source": ["launch team"],
        "stop_at_stage": "Serving",
        "steps": [
            {
                "delay_days": 0,
                "subject": "You are on the launch team",
                "body": (
                    "<p>{first_name},</p>"
                    "<p>You are in. A pastor will call you this week to talk through which "
                    "team fits you: setup, kids, worship, hospitality, or tech.</p>"
                    "<p>Set up your account so you can see where you are and what is next: "
                    "<a href=\"{site_url}/account/claim\">{site_url}/account/claim</a></p>"
                ),
            },
            {
                "delay_days": 7,
                "subject": "Pick your team",
                "body": (
                    "<p>{first_name},</p>"
                    "<p>Launch team works when everyone has one thing they own. Have you "
                    "picked yours yet?</p>"
                    "<p>Reply with the team you want and we will get you connected to the "
                    "person leading it.</p>"
                ),
            },
        ],
    },
}


def sequence_for_source(source: str):
    """Return (key, sequence) for the source of a person record, or (None, None)."""
    for key, sequence in SEQUENCES.items():
        if source in sequence["trigger_source"]:
            return key, sequence
    return None, None


def render(text: str, person, brand, site_url: str) -> str:
    return text.format(
        first_name=person.first_name,
        church_name=brand["church_name"],
        city=brand["city"],
        site_url=site_url.rstrip("/"),
    )
