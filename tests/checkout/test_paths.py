import pytest

from pr_evidence_reviewer.checkout.paths import (
    MAX_PATH_BYTES,
    MAX_SEGMENT_BYTES,
    normalize_repo_path,
)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "src/pr_evidence_reviewer/cli.py",
        "Foo.py",
        "foo.py",
        ".github/workflows/ci.yml",
        ".gitignore",
        ".gitmodules",
        "docs/.git-blame-ignore-revs",
        "CON.txt",
        "a b/c d.txt",
        "naïve/café.py",
        "%2e%2e/x",
        "GIT~1/config",
        "..foo/bar",
        "foo../bar",
        "a" * MAX_SEGMENT_BYTES,
    ],
)
def test_accepts_ordinary_repository_paths_unchanged(path: str) -> None:
    assert normalize_repo_path(path) == path


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        ("", "empty"),
        ("/etc/passwd", "leading slash"),
        ("//srv/share/x", "leading double slash"),
        ("a\\b", "backslash"),
        ("\\\\srv\\s\\x", "UNC"),
        ("\\\\?\\C:\\x", "device path"),
        ("C:/x", "drive"),
        ("C:x", "drive-relative"),
        ("a:b", "alternate data stream"),
        ("a::$DATA", "alternate data stream"),
        (".", "dot segment"),
        ("..", "dot-dot segment"),
        ("../x", "dot-dot segment"),
        ("a/../../x", "dot-dot segment"),
        ("a/./b", "dot segment"),
        ("a/", "empty trailing segment"),
        ("a//b", "empty middle segment"),
        (".git/config", ".git segment"),
        ("sub/.git/hooks/pre-commit", ".git segment"),
        (".GIT/config", ".git segment, any case"),
        ("a\x00b", "NUL"),
        ("a\nb", "control char"),
        ("a\tb", "control char"),
        ("a\x7fb", "DEL"),
        ("a\x85b", "C1 control"),
        ("a" * (MAX_SEGMENT_BYTES + 1), "segment too long"),
        ("/".join(["abcdefgh"] * 120), "path too long"),
        ("\udc80.py", "not encodable as UTF-8"),
    ],
)
def test_refuses_paths_that_could_escape_or_alias(path: str, rule: str) -> None:
    assert normalize_repo_path(path) is None, rule


def test_length_limit_counts_utf8_bytes_not_characters() -> None:
    segment = "é" * 128  # 256 bytes in UTF-8
    assert normalize_repo_path(segment) is None
    assert normalize_repo_path("é" * 127) == "é" * 127


def test_whole_path_limit_is_inclusive() -> None:
    path = "/".join(["a" * 200] * 5) + "/" + "b" * (MAX_PATH_BYTES - 5 * 201)
    assert len(path.encode()) == MAX_PATH_BYTES
    assert normalize_repo_path(path) == path
    assert normalize_repo_path(path + "c") is None


def test_does_not_normalise_unicode_or_case() -> None:
    nfc = "caf\u00e9.py"
    nfd = "cafe\u0301.py"

    assert normalize_repo_path(nfc) == nfc
    assert normalize_repo_path(nfd) == nfd
    assert normalize_repo_path(nfc) != normalize_repo_path(nfd)
    assert normalize_repo_path("SRC/Main.py") == "SRC/Main.py"


def test_refuses_values_that_are_not_strings() -> None:
    assert normalize_repo_path(b"README.md") is None  # type: ignore[arg-type]
    assert normalize_repo_path(None) is None  # type: ignore[arg-type]
