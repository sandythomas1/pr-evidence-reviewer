"""CheckoutReader behaviour and the T4 path-vector suite (task 13)."""

import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pr_evidence_reviewer.checkout.archive import TreeEntry, git_blob_sha
from pr_evidence_reviewer.checkout.cache import Checkout
from pr_evidence_reviewer.checkout.paths import normalize_repo_path
from pr_evidence_reviewer.checkout.reader import CheckoutReader, CheckoutReadError, UnsafePathError
from tests.checkout.fakes import FakeArchiveSource, publish, tree_entries

FILES = {
    "src/main.py": b"def main():\r\n    return 1\r\n",
    "src/util.py": b"\xef\xbb\xbfVALUE = 2\n",
    "README.md": b"# shop\n\nno trailing newline",
    "logo.png": b"\x89PNG\r\n\x1a\n\0\0\0",
    "big.bin": b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 9\n",
    "latin1.txt": b"caf\xe9\n",
    "empty.py": b"",
}
LINK = TreeEntry(path="docs/link", mode="120000", kind="blob", sha="1" * 40, size=9)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Checkout:
    source = FakeArchiveSource(FILES, entries=[*tree_entries(FILES), LINK])
    return publish(tmp_path_factory.mktemp("cache"), source)


@pytest.fixture
def fresh(tmp_path: Path) -> Checkout:
    return publish(tmp_path, FakeArchiveSource(FILES))


def test_reads_text_files_by_exact_path(checkout: Checkout) -> None:
    reader = CheckoutReader(checkout)

    assert reader.text("src/main.py") == "def main():\r\n    return 1\r\n"
    assert reader.lines("src/main.py") == ["def main():", "    return 1"]
    assert reader.text("src/util.py") == "VALUE = 2\n"
    assert reader.line_count("README.md") == 3
    assert reader.lines("empty.py") == []
    assert reader.head_sha == checkout.manifest.sha


@pytest.mark.parametrize("path", ["logo.png", "big.bin", "latin1.txt", "missing.py"])
def test_non_text_and_unknown_files_have_no_text(checkout: Checkout, path: str) -> None:
    reader = CheckoutReader(checkout)

    assert reader.text(path) is None
    assert reader.lines(path) is None
    assert reader.line_count(path) is None


def test_lists_every_stored_file_with_its_kind(checkout: Checkout) -> None:
    kinds = {entry.path: entry.kind for entry in CheckoutReader(checkout).files()}

    assert kinds == {
        "README.md": "text",
        "big.bin": "lfs",
        "empty.py": "text",
        "latin1.txt": "undecodable",
        "logo.png": "binary",
        "src/main.py": "text",
        "src/util.py": "text",
    }


def test_iter_text_yields_only_text_files(checkout: Checkout) -> None:
    paths = ["src/main.py", "logo.png", "missing.py", "README.md"]

    found = [path for path, _ in CheckoutReader(checkout).iter_text(paths)]

    assert found == ["src/main.py", "README.md"]


def test_a_vanished_blob_is_a_read_error(fresh: Checkout) -> None:
    os.unlink(fresh.blobs_dir / git_blob_sha(FILES["src/main.py"]))

    with pytest.raises(CheckoutReadError):
        CheckoutReader(fresh).text("src/main.py")


def test_a_blob_changed_on_disk_is_a_read_error(fresh: Checkout) -> None:
    blob = fresh.blobs_dir / git_blob_sha(FILES["src/main.py"])
    os.chmod(blob, 0o600)
    blob.write_bytes(b"def main():\r\n    return 2\r\n")

    with pytest.raises(CheckoutReadError):
        CheckoutReader(fresh).text("src/main.py")


def test_decoded_text_is_kept_in_a_bounded_lru(fresh: Checkout) -> None:
    reader = CheckoutReader(fresh, cache_bytes=len(FILES["src/main.py"]) + 1)
    reader.text("src/util.py")
    reader.text("src/main.py")  # evicts src/util.py
    for path in ("src/main.py", "src/util.py"):
        os.unlink(fresh.blobs_dir / git_blob_sha(FILES[path]))

    assert reader.text("src/main.py") == "def main():\r\n    return 1\r\n"
    with pytest.raises(CheckoutReadError):
        reader.text("src/util.py")


def test_iter_text_reads_fresh_and_leaves_the_lru_alone(fresh: Checkout) -> None:
    reader = CheckoutReader(fresh)
    list(reader.iter_text(["src/main.py"]))
    os.unlink(fresh.blobs_dir / git_blob_sha(FILES["src/main.py"]))

    with pytest.raises(CheckoutReadError):
        reader.text("src/main.py")


# --- T4 vectors ------------------------------------------------------------------------------

REFUSED = [
    "../x",
    "a/../../x",
    "/etc/passwd",
    "C:/x",
    "C:x",
    "\\\\srv\\s\\x",
    "\\\\?\\C:\\x",
    "a\\..\\x",
    "a:b",
    "a::$DATA",
    "src/main.py::$DATA",
    "a/./b",
    "./src/main.py",
    "src//main.py",
    "src/main.py/",
    ".git/config",
    "src/.GIT/config",
    "",
    "src/main.py\0",
]
UNMATCHED = ["%2e%2e/x", "GIT~1/config", "SRC/Main.py", "src/Main.py", "SRC~1/MAIN.PY", "docs/link"]


@pytest.mark.parametrize("path", REFUSED)
def test_refuses_unsafe_paths(checkout: Checkout, path: str) -> None:
    reader = CheckoutReader(checkout)

    with pytest.raises(UnsafePathError):
        reader.file(path)
    with pytest.raises(UnsafePathError):
        reader.text(path)


@pytest.mark.parametrize("path", UNMATCHED)
def test_aliases_short_names_and_links_match_nothing(checkout: Checkout, path: str) -> None:
    reader = CheckoutReader(checkout)

    assert reader.file(path) is None
    assert reader.text(path) is None


def test_a_link_in_the_tree_is_listed_as_skipped_not_stored(checkout: Checkout) -> None:
    assert ("docs/link", "link") in {(s.path, s.reason) for s in checkout.manifest.skipped}


_KEYS = sorted(FILES)
_ALPHABET = st.sampled_from([*"aAsSrRcC./\\:~%$0-_ ", "\u0301", "\u00e9", "\0", "\u202e"])
_PATHS = st.one_of(
    st.sampled_from(_KEYS),
    st.text(_ALPHABET, max_size=24),
    st.builds(
        lambda key, prefix, suffix: prefix + key + suffix,
        st.sampled_from(_KEYS),
        st.text(_ALPHABET, max_size=4),
        st.text(_ALPHABET, max_size=4),
    ),
    st.builds(str.upper, st.sampled_from(_KEYS)),
)


@given(path=_PATHS)
def test_any_accepted_path_resolves_only_to_its_exact_manifest_key(
    checkout: Checkout, path: str
) -> None:
    reader = CheckoutReader(checkout)
    if normalize_repo_path(path) is None:
        with pytest.raises(UnsafePathError):
            reader.file(path)
        return
    entry = reader.file(path)
    assert (entry is not None) == (path in FILES)
    if entry is not None:
        assert entry.path == path
