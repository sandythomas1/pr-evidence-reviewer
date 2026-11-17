"""The one validator for repository paths (threat T4).

Every path that comes from a git tree, the model, a claim or a request goes through
`normalize_repo_path` before it is looked up in a checkout manifest. Nothing is rewritten: no Unicode
or case normalisation, so a path is accepted only as the exact string the tree lists.
"""

import unicodedata
from typing import NewType

RepoPath = NewType("RepoPath", str)

MAX_PATH_BYTES = 1024
MAX_SEGMENT_BYTES = 255
_REFUSED_SEGMENTS = frozenset({"", ".", ".."})


def normalize_repo_path(path: str) -> RepoPath | None:
    """Return `path` as a `RepoPath` if it is a safe, relative, slash-separated repository path.

    Refuses: empty; more than 1024 UTF-8 bytes or a segment over 255; NUL or other control
    characters; a backslash; a leading slash; any colon (drive letters, alternate data streams);
    `.`, `..` or empty segments; and any `.git` segment.
    """
    if not isinstance(path, str) or not path:
        return None
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > MAX_PATH_BYTES:
        return None
    if "\\" in path or ":" in path or path.startswith("/"):
        return None
    if any(unicodedata.category(char) == "Cc" for char in path):
        return None
    for segment in path.split("/"):
        if segment in _REFUSED_SEGMENTS or segment.lower() == ".git":
            return None
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            return None
    return RepoPath(path)
