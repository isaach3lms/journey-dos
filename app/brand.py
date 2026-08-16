"""Brand tokens. The only theming lever in this application.

Rules:
- Templates reference CSS variables, never hex values.
- A church reskin is a row update on ``church.accent_hex``, never a template edit.
- This module reads no database and imports nothing from the app, so the 500
  page can use it while the database is unreachable.
"""

from __future__ import annotations

# Neutral product defaults. A church that has not set an accent gets these.
DEFAULT_TOKENS: dict[str, str] = {
    "ink": "#0B1026",
    "accent": "#2563FF",
    "accent-ink": "#FFFFFF",
    "navy": "#1E3A8A",
    "mist": "#E6EEFF",
    "surface": "#FFFFFF",
    "surface-2": "#F7F9FD",
    "text": "#111726",
    "muted": "#5B6478",
    "line": "#E3E8F2",
    "good": "#1E7A54",
    "warn": "#B4530A",
    "danger": "#B42318",
    "radius": "14px",
    "radius-sm": "9px",
    "font-sans": "'Poppins', ui-sans-serif, system-ui, -apple-system, sans-serif",
    "font-mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
    "maxw": "1180px",
}

# Tokens a tenant is allowed to override, mapped to the column that holds them.
TENANT_OVERRIDABLE = {
    "accent": "accent_hex",
}


def _is_hex(value: str) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v.startswith("#") or len(v) not in (4, 7):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in v[1:])


def readable_ink(hex_color: str) -> str:
    """Pick black or white text for a background, by relative luminance.

    WCAG contrast is enforced here rather than trusted to whoever pastes a hex
    into Settings. A pastor picking a pale yellow accent must not produce
    white-on-yellow buttons.
    """
    if not _is_hex(hex_color):
        return "#FFFFFF"
    v = hex_color.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    r, g, b = (int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    contrast_white = 1.05 / (lum + 0.05)
    contrast_black = (lum + 0.05) / 0.05
    return "#FFFFFF" if contrast_white >= contrast_black else "#0B1026"


def tokens_for(church=None) -> dict[str, str]:
    """Resolve the token set for a tenant. Never touches the database itself."""
    tokens = dict(DEFAULT_TOKENS)
    if church is None:
        return tokens
    for token, attr in TENANT_OVERRIDABLE.items():
        value = getattr(church, attr, None)
        if _is_hex(value or ""):
            tokens[token] = value.strip()
    tokens["accent-ink"] = readable_ink(tokens["accent"])
    return tokens


def css_variables(tokens: dict[str, str] | None = None) -> str:
    """Render the token set as a :root block. Injected into <head>."""
    tokens = tokens or DEFAULT_TOKENS
    body = "".join(f"--{k}:{v};" for k, v in tokens.items())
    return f":root{{{body}}}"
