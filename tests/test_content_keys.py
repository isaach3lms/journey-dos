"""No copy dictionary may define the same key twice.

Python keeps the last one silently, so a new "template_heading" for one
screen quietly renamed a heading on another. Caught once in SETTINGS and once
in SERVICES; this makes it a test failure instead of a surprise.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "app" / "content.py"


def test_no_duplicate_keys_in_any_copy_dict():
    tree = ast.parse(SOURCE.read_text())
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            seen = {}
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in seen:
                        problems.append(f"{key.value!r} at lines {seen[key.value]} and {key.lineno}")
                    seen[key.value] = key.lineno
    assert not problems, "Duplicate copy keys: " + "; ".join(problems)
