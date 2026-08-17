"""Brand tokens are the only theming lever, and the contrast floor holds."""

import re
from pathlib import Path

import pytest

from app.brand import (
    JOURNEY,
    PALETTES,
    assert_accent_readable,
    brand_css_vars,
    contrast,
    palette_for,
)


class TestContrast:
    def test_white_on_white_is_one(self):
        assert contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)

    def test_black_on_white_is_twenty_one(self):
        assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.05)

    def test_journey_accent_clears_aa_for_white_text(self):
        assert contrast(JOURNEY.green, "#FFFFFF") >= 4.5

    def test_journey_gold_cannot_carry_white_text(self):
        # This is why gold is restricted to dark surfaces and rail bars.
        assert contrast(JOURNEY.gold, "#FFFFFF") < 3.0

    def test_journey_gold_is_readable_on_ink(self):
        assert contrast(JOURNEY.gold, JOURNEY.ink) >= 4.5

    def test_every_shipped_palette_clears_the_floor(self):
        for key, palette in PALETTES.items():
            assert contrast(palette.green, "#FFFFFF") >= 4.5, key


class TestAccentGuard:
    def test_a_dark_accent_passes(self):
        assert_accent_readable("#485B38") is None

    def test_gold_is_rejected_with_a_useful_message(self):
        with pytest.raises(ValueError) as err:
            assert_accent_readable("#F6C14B")
        assert "white text" in str(err.value)


class TestResolution:
    def test_no_church_falls_back_to_the_platform_palette(self):
        assert palette_for(None).name == "Between Sundays"

    def test_accent_column_overrides_the_palette(self):
        class Row:
            palette_key = "journey"
            accent_hex = "#123456"
            logo_reversed_path = None

        assert palette_for(Row()).green == "#123456"

    def test_unknown_palette_key_does_not_crash(self):
        class Row:
            palette_key = "does-not-exist"
            accent_hex = None
            logo_reversed_path = None

        assert palette_for(Row()).name == "Between Sundays"

    def test_css_vars_include_the_accent(self):
        class Row:
            palette_key = "journey"
            accent_hex = "#485B38"
            logo_reversed_path = None

        css = brand_css_vars(Row())
        assert "--accent:#485B38;" in css
        assert "--chrome:#2F3E24;" in css


class TestTemplatesCarryNoColors:
    """The architecture rule, enforced rather than trusted."""

    HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")

    def test_no_hex_codes_in_templates(self):
        root = Path(__file__).resolve().parent.parent / "app" / "templates"
        offenders = []
        for path in root.rglob("*.html"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.HEX.search(line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        assert not offenders, "Templates must not contain colors:\n" + "\n".join(offenders)

    def test_stylesheet_uses_tokens_not_literals(self):
        css = (
            Path(__file__).resolve().parent.parent
            / "app" / "static" / "css" / "app.css"
        ).read_text(encoding="utf-8")
        # White is allowed on dark chrome; it is white by definition, not by brand.
        found = {h.lower() for h in self.HEX.findall(css)}
        assert found <= {"#fff", "#ffffff"}, f"Unexpected color literals: {found}"
