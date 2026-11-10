"""Page title and heading blocks hold text, never page sections.

A careless find-and-replace once pasted a whole card into the title, heading,
and subheading blocks of the Messages page, so the card appeared three extra
times. This catches that class of mistake on every template.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
BLOCK = re.compile(r"{%\s*block\s+(title|heading|subheading)\s*%}(.*?){%\s*endblock\s*%}", re.S)


def test_title_blocks_contain_no_markup_sections():
    bad = []
    for path in TEMPLATES.rglob("*.html"):
        for name, body in BLOCK.findall(path.read_text()):
            if re.search(r"<(section|div|ul|form|article)\b", body):
                bad.append(f"{path.relative_to(TEMPLATES)}: {name}")
    assert not bad, bad
