"""Brand tokens. This module is the sole theming lever.

To reskin a tenant you change two columns on its `church` row, or you add a
palette below. You never edit a template. There is not one hex code anywhere
in `app/templates/` or `app/static/css/app.css`.

Contrast policy, enforced by `assert_accent_readable` and by the tests:

  - Any surface that carries white text must reach 4.5:1. Journey green
    `#485B38` measures 7.42:1.
  - Journey gold `#F6C14B` measures 1.66:1 against white. It never carries
    text and never sits behind a label. It appears on dark chrome and on the
    Journey rail bars, where the count is printed directly above the bar, so
    the bar duplicates information rather than carrying it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Palette:
    name: str

    deep: str          # chrome: sidebar, kiosk, drawer headers, modal headers
    green: str         # accent: anything that carries white text
    gold: str          # emphasis: dark surfaces and rail bars only, never text
    bone: str          # page background
    ink: str           # body text
    mist: str          # selected and tinted states

    surface: str = "#FFFFFF"
    surface_alt: str = "#FAF9F3"

    # Semantic colors sit deliberately outside the brand hues, so a flag can
    # never be mistaken for decoration.
    flag: str = "#9A3412"
    flag_bg: str = "#F8EAE3"
    good: str = "#0F766E"
    good_bg: str = "#E4F4F1"

    font_display: str = "'Montserrat','Inter',sans-serif"
    font_body: str = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
    font_url: str = (
        "https://fonts.googleapis.com/css2"
        "?family=Montserrat:wght@600;700&family=Inter:wght@400;500;600&display=swap"
    )

    logo_reversed: str = "img/journey-logo-white.png"

    radii: dict = field(default_factory=lambda: {"sm": "8px", "md": "14px", "lg": "20px"})


JOURNEY = Palette(
    name="The Journey Church",
    deep="#2F3E24",
    green="#485B38",
    gold="#F6C14B",
    bone="#F2F0E7",
    ink="#1A1A1A",
    mist="#E4E9DD",
    logo_reversed="img/journey-logo-white.png",
)

BETWEEN_SUNDAYS = Palette(
    name="Between Sundays",
    deep="#0B1026",
    green="#2563FF",
    gold="#F6C14B",
    bone="#F7F9FF",
    ink="#0B1026",
    mist="#E6EEFF",
    surface_alt="#FAFBFF",
    font_display="'Poppins',sans-serif",
    font_body="'Poppins',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    font_url=(
        "https://fonts.googleapis.com/css2"
        "?family=Poppins:wght@400;500;600;700&display=swap"
    ),
    logo_reversed="",
)

PALETTES: dict[str, Palette] = {
    "journey": JOURNEY,
    "between-sundays": BETWEEN_SUNDAYS,
}

DEFAULT_PALETTE = "between-sundays"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def palette_for(church) -> Palette:
    """Resolve a church row to a palette. A church with no theming still renders."""
    if church is None:
        return PALETTES[DEFAULT_PALETTE]

    base = PALETTES.get(
        getattr(church, "palette_key", None) or DEFAULT_PALETTE,
        PALETTES[DEFAULT_PALETTE],
    )

    overrides = {}
    accent = getattr(church, "accent_hex", None)
    if accent:
        overrides["green"] = accent
    logo = getattr(church, "logo_reversed_path", None)
    if logo:
        overrides["logo_reversed"] = logo

    return Palette(**{**asdict(base), **overrides}) if overrides else base


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def brand_tokens(church) -> dict[str, str]:
    p = palette_for(church)
    return {
        "--deep": p.deep,
        "--green": p.green,
        "--gold": p.gold,
        "--bone": p.bone,
        "--ink": p.ink,
        "--chrome": p.deep,
        "--accent": p.green,
        "--mist": p.mist,
        "--white": p.surface,
        "--surface-alt": p.surface_alt,
        "--line": _rgba(p.deep, 0.14),
        "--line-soft": _rgba(p.deep, 0.09),
        "--muted": _rgba(p.ink, 0.62),
        "--muted-2": _rgba(p.ink, 0.45),
        "--flag": p.flag,
        "--flag-bg": p.flag_bg,
        "--good": p.good,
        "--good-bg": p.good_bg,
        "--font-display": p.font_display,
        "--font-body": p.font_body,
        "--shadow-sm": f"0 1px 2px {_rgba(p.deep, .07)}, 0 2px 6px {_rgba(p.deep, .06)}",
        "--shadow-md": f"0 4px 14px {_rgba(p.deep, .10)}, 0 1px 3px {_rgba(p.deep, .07)}",
        "--shadow-lg": f"0 18px 48px {_rgba(p.ink, .20)}",
        "--r-sm": p.radii["sm"],
        "--r": p.radii["md"],
        "--r-lg": p.radii["lg"],
    }


# A CSS value is emitted into a <style> block with |safe, so it is sanitized
# here rather than trusted. Anything that could close the block or start a new
# declaration is stripped. `accent_hex` comes from a database column a staff
# member can edit, which is exactly the input that must not be trusted.
_CSS_VALUE_FORBIDDEN = re.compile(r"[<>{};\\]|/\*|\*/|@import|expression\(|url\(", re.I)


def sanitize_css_value(value: str) -> str:
    """Strip anything that could break out of a CSS declaration."""
    cleaned = _CSS_VALUE_FORBIDDEN.sub("", str(value)).strip()
    return cleaned


def brand_css_vars(church) -> str:
    """The `:root` body for one tenant, as one string."""
    return "".join(
        f"{k}:{sanitize_css_value(v)};" for k, v in brand_tokens(church).items()
    )


# ---------------------------------------------------------------------------
# Contrast guard
# ---------------------------------------------------------------------------

def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def assert_accent_readable(accent_hex: str, minimum: float = 4.5) -> None:
    """Reject an accent that cannot carry white text.

    Call this from the Settings form. A pastor pasting Journey gold into the
    accent field should be stopped in the form, not discovered by a volunteer
    squinting at a button in a lobby.
    """
    ratio = contrast(accent_hex, "#FFFFFF")
    if ratio < minimum:
        raise ValueError(
            f"{accent_hex} measures {ratio:.2f}:1 against white text and the "
            f"minimum is {minimum}:1. Every button in the app uses this color "
            f"with white text on it. Pick a darker shade."
        )
