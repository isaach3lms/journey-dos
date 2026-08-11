"""
Brand tokens for the tenant church.

This is the templating lever. To stand up a new church on the DOS, copy this
file, swap the values, drop new logo files into static/img, and the entire
public site, member app, and staff dashboard re-skin themselves. No template
edits required.

Source of truth for The Journey Church: the brand sheet supplied by the client
(Journey_Church_Brand_Visual.png). Do not take colors from screenshots or
printed collateral.
"""

BRAND = {
    "church_name": "The Journey Church",
    "short_name": "Journey",
    "city": "Jackson",
    "state": "Missouri",
    "domain": "thejourneychurchsemo.com",
    "launch_line": "Launching Fall 2026",
    "mission": (
        "Journey Church exists to help people live adventurously expectant "
        "as they take their next step with God."
    ),
    "core_values": [
        "Adventurous faith",
        "Expectant hope",
        "Authentic relationships",
        "Biblical truth",
        "Mission focused",
    ],
    # Palette. Named to match the client brand sheet.
    "colors": {
        "forest": "#2F3E24",   # primary dark green
        "moss": "#485B38",     # secondary green
        "gold": "#F6C14B",     # accent yellow
        "bone": "#F2F0E7",     # neutral background
        "ink": "#1A1A1A",      # near black text
        "white": "#FFFFFF",
    },
    "type": {
        "display": "Montserrat",
        "display_weights": "600;700",
        "body": "Inter",
        "body_weights": "400;500;600",
    },
    "social": {
        "facebook": "https://facebook.com/thejourneychurch.semo",
        "instagram": "https://instagram.com/thejourneychurch.semo",
        "youtube": "https://youtube.com/@thejourneychurch.semo",
    },
    "logo_white": "img/journey-logo-white.png",
    "logo_mark": "img/journey-logo-white.png",
}


def css_variables() -> str:
    """Render the palette as CSS custom properties for the base template."""
    c = BRAND["colors"]
    return "".join(f"--{name}:{value};" for name, value in c.items())
