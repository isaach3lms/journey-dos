"""Brand tokens for the Discipleship Operating System.

This module is the sole theming lever. Templates never carry colors.
To reskin a tenant, change the row in `church` or the palette below.
Nothing in `templates/` is edited to change how a tenant looks.

Usage
-----
    from app.brand import brand_css_vars, PALETTES

    @app.context_processor
    def inject_brand():
        return {"brand_css": brand_css_vars(g.church)}

Then, once, in base.html:

    <style>:root{ {{ brand_css }} }</style>

Contrast policy, enforced by convention and by the checks at the bottom
of this file:
  - Any surface that carries white text must measure at least 4.5:1.
    Journey green #485B38 measures 7.43:1 with white. It passes.
  - Journey gold #F6C14B measures 1.66:1 with white. It never carries
    text and never sits behind a label. It is used on dark chrome and
    on the Journey rail bars, where the count is stated numerically
    directly above the bar.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """One tenant's visual identity. Every value is a token, not a rule."""

    name: str

    # Core five. Sourced from the client's brand board.
    deep: str          # chrome: sidebar, kiosk, drawer headers, modal headers
    green: str         # accent: anything that carries white text
    gold: str          # emphasis: dark surfaces and rail bars only, never text
    bone: str          # page background
    ink: str           # body text

    # Derived surfaces
    mist: str          # selected and tinted states
    surface: str = "#FFFFFF"
    surface_alt: str = "#FAF9F3"

    # Semantic. Deliberately outside the brand hues so a flag never
    # reads as decoration.
    flag: str = "#9A3412"
    flag_bg: str = "#F8EAE3"
    good: str = "#0F766E"
    good_bg: str = "#E4F4F1"

    # Type
    font_display: str = "'Montserrat','Inter',sans-serif"
    font_body: str = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"

    # Logo, relative to the static folder. White reversed art, because
    # every surface the logo sits on is dark chrome.
    logo_reversed: str = "img/journey-logo-white.png"
    logo_primary: str | None = None

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
    logo_reversed="img/between-sundays-white.svg",
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
    """Resolve a tenant to a palette.

    Order: the church's own palette key, then its accent override, then
    the platform default. A tenant with no theming still renders.
    """
    key = getattr(church, "palette_key", None) or DEFAULT_PALETTE
    base = PALETTES.get(key, PALETTES[DEFAULT_PALETTE])

    accent = getattr(church, "accent_hex", None)
    if accent and accent.lower() != base.green.lower():
        base = Palette(**{**asdict(base), "green": accent})
    return base


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def brand_css_vars(church) -> str:
    """Emit the :root custom properties for one tenant, as one string."""
    p = palette_for(church)
    line_base, muted_base = p.deep, p.ink

    tokens = {
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
        "--line": _rgba(line_base, 0.14),
        "--line-soft": _rgba(line_base, 0.09),
        "--muted": _rgba(muted_base, 0.62),
        "--muted-2": _rgba(muted_base, 0.45),
        "--flag": p.flag,
        "--flag-bg": p.flag_bg,
        "--good": p.good,
        "--good-bg": p.good_bg,
        "--font-display": p.font_display,
        "--font-body": p.font_body,
        "--shadow-sm": f"0 1px 2px {_rgba(line_base, .07)}, 0 2px 6px {_rgba(line_base, .06)}",
        "--shadow-md": f"0 4px 14px {_rgba(line_base, .10)}, 0 1px 3px {_rgba(line_base, .07)}",
        "--shadow-lg": f"0 18px 48px {_rgba(muted_base, .20)}",
        "--r-sm": p.radii["sm"],
        "--r": p.radii["md"],
        "--r-lg": p.radii["lg"],
    }
    return "".join(f"{k}:{v};" for k, v in tokens.items())


# ---------------------------------------------------------------------------
# Contrast guard
# ---------------------------------------------------------------------------

def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def assert_accent_readable(p: Palette, minimum: float = 4.5) -> None:
    """Fail loudly if a tenant sets an accent that cannot carry white text.

    Call this in the church admin form and in a test. A pastor pasting a
    yellow hex into Settings should be stopped there, not discovered on
    a phone in a lobby.
    """
    ratio = contrast(p.green, "#FFFFFF")
    if ratio < minimum:
        raise ValueError(
            f"Accent {p.green} measures {ratio:.2f}:1 against white text. "
            f"Minimum is {minimum}:1. Pick a darker hex or the accent will "
            f"be unreadable on every button in the app."
        )


if __name__ == "__main__":
    for key, p in PALETTES.items():
        print(f"{key:16} accent {p.green} on white  {contrast(p.green, '#FFFFFF'):5.2f}:1")
        print(f"{'':16} gold   {p.gold} on white  {contrast(p.gold, '#FFFFFF'):5.2f}:1  (never carries text)")
        print(f"{'':16} gold   {p.gold} on ink    {contrast(p.gold, p.ink):5.2f}:1")
        print(f"{'':16} chrome {p.deep} on white  {contrast(p.deep, '#FFFFFF'):5.2f}:1")
