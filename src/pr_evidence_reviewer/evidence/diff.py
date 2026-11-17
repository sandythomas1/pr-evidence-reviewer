"""Diff hunks, low-value paths and CI-setup detection, shared by the packet, search and test mapping.

A hunk numbers every line that exists on the head side, so the model can cite real lines; removed
lines get no number.
"""

import re
from dataclasses import dataclass

from pr_evidence_reviewer.domain.pull_request import ChangedFile

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GUTTER = 7

# Files that rarely carry the change a claim is about: lockfiles and generated, minified or vendored
# code. The diff budget reaches them last and search skips them unless the pull request changed them.
_LOW_VALUE_NAMES = frozenset(
    {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "go.sum", "composer.lock"}
)
_LOW_VALUE_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".pb.go", "_pb2.py")
_LOW_VALUE_INFIXES = (".min.", ".generated.")
_LOW_VALUE_DIRS = frozenset(
    {"dist", "vendor", "node_modules", "build", "third_party", "__generated__"}
)

# A pull request that edits these can change what its own CI checks test.
_CI_CONFIG_PREFIXES = (".github/workflows/", ".circleci/")
_CI_CONFIG_NAMES = frozenset(
    {".gitlab-ci.yml", "azure-pipelines.yml", "azure-pipelines.yaml", "Jenkinsfile"}
)
CI_CONFIG_LIMITATION = (
    "This pull request changes its own CI setup, so its CI checks may not be independent evidence."
)


@dataclass(frozen=True)
class Hunk:
    text: str
    head_lines: frozenset[int]


def is_low_value(path: str) -> bool:
    """Lockfiles and generated, minified, or vendored files: rarely where a claimed change is."""
    *folders, name = path.split("/")
    return (
        name in _LOW_VALUE_NAMES
        or name.endswith(_LOW_VALUE_SUFFIXES)
        or any(infix in name for infix in _LOW_VALUE_INFIXES)
        or any(folder in _LOW_VALUE_DIRS for folder in folders)
    )


def touches_ci_config(file: ChangedFile) -> bool:
    return any(
        path.startswith(_CI_CONFIG_PREFIXES) or path.rsplit("/", 1)[-1] in _CI_CONFIG_NAMES
        for path in (file.path, file.previous_path)
        if path
    )


def parse_hunks(patch: str) -> list[Hunk]:
    """Split a unified diff into hunks. Lines before the first hunk header are skipped."""
    hunks: list[Hunk] = []
    header: re.Match[str] | None = None
    body: list[str] = []
    for line in patch.split("\n"):
        match = _HUNK_HEADER.match(line)
        if match is None:
            body.append(line)
            continue
        if header is not None:
            hunks.append(_render_hunk(header, body))
        header, body = match, []
    if header is not None:
        hunks.append(_render_hunk(header, body))
    return hunks


def _render_hunk(header: re.Match[str], body: list[str]) -> Hunk:
    """Number every line that exists on the head side: context and added lines.

    The header's line counts say how long the hunk is, so anything past them, such as the empty
    string a trailing newline leaves, is not a line.
    """
    old_left = int(header.group(1) or 1)
    next_line = int(header.group(2))
    new_left = int(header.group(3) or 1)
    lines = [f"@@ +{next_line} @@{header.group(4)}"]
    numbers: set[int] = set()
    for raw in body:
        if old_left <= 0 and new_left <= 0:
            break
        marker, content = (raw[0], raw[1:]) if raw else (" ", "")
        if marker == "-":
            lines.append(f"{'':>{_GUTTER}} - {content}")
            old_left -= 1
        elif marker in " +" and new_left > 0:
            lines.append(f"{next_line:>{_GUTTER}} {marker} {content}")
            numbers.add(next_line)
            next_line += 1
            new_left -= 1
            if marker == " ":
                old_left -= 1
    return Hunk(text="\n".join(lines), head_lines=frozenset(numbers))
