"""Builds PR snapshots for review tests without going through the GitHub client."""

import re
from typing import Any

from pr_evidence_reviewer.domain.pull_request import (
    ChangedFile,
    CheckResult,
    CommitRef,
    PullRequestRef,
    PullRequestSnapshot,
)
from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.render import ShownIndex, render_packet

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40

UPLOAD_PATCH = (
    "@@ -1,3 +1,4 @@ def upload(data):\n"
    " def upload(data):\n"
    "-    send(data)\n"
    "+    for attempt in range(3):\n"
    "+        send(data)\n"
    "     return True\n"
    "@@ -20,2 +21,3 @@\n"
    " # retry\n"
    "+RETRIES = 3\n"
    " # end"
)
TEST_PATCH = "@@ -0,0 +1,2 @@\n+def test_retries():\n+    assert upload(b'x')"


def changed_file(path: str, patch: str | None = UPLOAD_PATCH, **extra: Any) -> ChangedFile:
    fields: dict[str, Any] = {"status": "modified", "additions": 1, "deletions": 1}
    fields.update(extra)
    return ChangedFile(path=path, patch=patch, **fields)


def check(name: str, conclusion: str | None = "success", **extra: Any) -> CheckResult:
    fields: dict[str, Any] = {"source": "check_run", "status": "completed", "url": None}
    fields.update(extra)
    return CheckResult(name=name, conclusion=conclusion, **fields)


def snapshot(
    files: list[ChangedFile] | None = None,
    checks: list[CheckResult] | None = None,
    **overrides: Any,
) -> PullRequestSnapshot:
    fields: dict[str, Any] = {
        "ref": PullRequestRef(owner="acme", repo="shop", number=7),
        "title": "Add retry to the upload client",
        "body": "Retries failed uploads three times.\n\nAdds a unit test for the retry loop.",
        "author": "octocat",
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/acme/shop/pull/7",
        "base": CommitRef(ref="main", sha=BASE_SHA),
        "head": CommitRef(ref="feature/retry", sha=HEAD_SHA),
        "changed_files_count": 2,
        "files": (
            files
            if files is not None
            else [
                changed_file("src/upload.py"),
                changed_file("tests/test_upload.py", TEST_PATCH, status="added", deletions=0),
            ]
        ),
        "checks": checks if checks is not None else [check("tests"), check("lint")],
    }
    fields.update(overrides)
    return PullRequestSnapshot(**fields)


_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def head_files(snap: PullRequestSnapshot) -> dict[str, bytes]:
    """Head-side file contents consistent with each changed file's patch: the context and added
    lines at their head line numbers, blank lines elsewhere. Removed files aren't at head."""
    files: dict[str, bytes] = {}
    for changed in snap.files:
        if changed.status == "removed":
            continue
        lines: dict[int, str] = {}
        number = 0
        for line in (changed.patch or "").splitlines():
            header = _HUNK.match(line)
            if header:
                number = int(header.group(1))
            elif line.startswith(("+", " ")) and number:
                lines[number] = line[1:]
                number += 1
        last = max(lines, default=1)
        text = "".join(lines.get(n, "") + "\n" for n in range(1, last + 1))
        files[changed.path] = text.encode()
    return files


def shown_for(snap: PullRequestSnapshot) -> ShownIndex:
    """What a diff-only packet for `snap` shows the model."""
    return render_packet(build_packet(snap, None, []))[1]
