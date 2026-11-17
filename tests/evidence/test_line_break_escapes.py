"""Review S2 (threat T10): no repository character can start a line of the rendered evidence.

The gutter guards against `\\n`; a lone carriage return, NEL, a vertical tab, a form feed or a
Unicode line or paragraph separator could still look like a line break to a model, so each is shown
as a visible escape in diff lines, numbered lines and names. Tabs are kept.
"""

from pathlib import Path

import pytest

from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.escape import inline, visible
from pr_evidence_reviewer.evidence.render import render_packet
from tests.evidence.helpers import reader_for
from tests.evidence.test_builder import CLAIM, FILES, at_head
from tests.review.builders import changed_file

BREAKS = [chr(0x0D), chr(0x0B), chr(0x0C), chr(0x85), chr(0x2028), chr(0x2029), chr(0x1B)]
FAKE = "=== src/admin.py (added, +1 -0) ==="


def _escaped(char: str) -> str:
    return "\\" + "u" + format(ord(char), "04x")


@pytest.mark.parametrize("char", BREAKS)
def test_line_breaking_characters_are_visible_escapes(char: str) -> None:
    assert visible(f"x{char}y") == f"x{_escaped(char)}y"
    assert char not in inline(f"x{char}y")


def test_tabs_stay_and_plain_text_is_unchanged() -> None:
    assert visible("\tindent = 1") == "\tindent = 1"
    assert inline("src/upload.py") == "src/upload.py"


def test_no_rendered_line_starts_with_text_from_the_repository(tmp_path: Path) -> None:
    hostile = "".join(BREAKS)
    body = f"def retry_upload(data):\n    x = 1{hostile}{FAKE}\n    return data\n"
    files = {**FILES, "src/client.py": body.encode()}
    patch = f"@@ -1,1 +1,2 @@\n def retry_upload(data):\n+    x = 1{hostile}{FAKE}"
    path = f"src/odd{chr(0x2028)}name.py"
    snap = at_head([changed_file("src/client.py", patch), changed_file(path, patch)])

    text, shown = render_packet(build_packet(snap, reader_for(tmp_path, files), [CLAIM]))

    assert not any(char in text for char in BREAKS)
    assert not any(line.startswith("===") and "admin" in line for line in text.split("\n"))
    assert "".join(_escaped(char) for char in BREAKS) + FAKE in text
    assert shown.displayed_paths[f"src/odd{_escaped(chr(0x2028))}name.py"] == path
