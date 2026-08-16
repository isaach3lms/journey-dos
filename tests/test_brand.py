"""Invariant 5: brand.py is the only theming lever."""

from __future__ import annotations

import pathlib

from app.brand import DEFAULT_TOKENS, css_variables, readable_ink, tokens_for
from app.extensions import db

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
CSS_FILE = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"


def test_default_tokens_render_as_css_variables():
    css = css_variables(DEFAULT_TOKENS)
    assert css.startswith(":root{")
    assert "--accent:#2563FF;" in css


def test_tenant_accent_overrides_the_default(app, journey):
    journey.accent_hex = "#E8C766"
    db.session.commit()
    assert tokens_for(journey)["accent"] == "#E8C766"


def test_invalid_hex_falls_back_instead_of_breaking_the_page(app, journey):
    journey.accent_hex = "not-a-color"
    db.session.commit()
    assert tokens_for(journey)["accent"] == DEFAULT_TOKENS["accent"]


def test_accent_ink_is_chosen_for_contrast_not_taste():
    assert readable_ink("#0B1026") == "#FFFFFF"
    assert readable_ink("#E8C766") == "#0B1026"
    assert readable_ink("#2563FF") == "#FFFFFF"


def test_reskin_is_a_row_update_visible_on_the_page(app, client, journey):
    journey.accent_hex = "#7C3AED"
    db.session.commit()
    body = client.get("/", headers={"Host": "app.thejourneychurchsemo.com"}).get_data(
        as_text=True
    )
    assert "--accent:#7C3AED;" in body


def test_no_hex_values_hardcoded_in_templates_or_stylesheet():
    """Guardrail: the day someone pastes a hex into a template, this fails."""
    offenders = []
    for path in list(TEMPLATE_DIR.rglob("*.html")) + [CSS_FILE]:
        if path.parent.name == "errors":
            continue  # error pages inline the safe token block by design
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "{#")):
                continue
            for token in stripped.split():
                if token.startswith("#") and len(token.strip("#;,'\"")) in (3, 6):
                    candidate = token.strip("#;,'\"")
                    if all(c in "0123456789abcdefABCDEF" for c in candidate):
                        offenders.append(f"{path.name}:{lineno} {token}")
    assert offenders == [], f"Hardcoded colors outside brand.py: {offenders}"
