"""Builds a published checkout and its reader from a dict of files, for evidence tests."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from pr_evidence_reviewer.checkout.archive import TreeEntry
from pr_evidence_reviewer.checkout.reader import CheckoutReader
from tests.checkout.fakes import FakeArchiveSource, make_archive, publish, regular, tree_entries


def reader_for(
    root: Path,
    files: Mapping[str, bytes],
    *,
    extra_entries: Sequence[TreeEntry] = (),
    archived: Mapping[str, bytes] | None = None,
) -> CheckoutReader:
    """A reader over `files`. `archived` (default: all of `files`) is what the tarball holds, so a
    file in the tree but not in the tarball models `export-ignore`."""
    in_archive = files if archived is None else archived
    source = FakeArchiveSource(
        files,
        entries=[*tree_entries(files), *extra_entries],
        archive=make_archive(regular(in_archive)),
        blobs={},
    )
    return CheckoutReader(publish(root, source))
