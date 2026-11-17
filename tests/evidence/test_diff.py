"""Hunk parsing, low-value paths and CI-setup detection (moved from the review packet, task 16)."""

import pytest

from pr_evidence_reviewer.evidence.diff import is_low_value, parse_hunks, touches_ci_config
from tests.review.builders import changed_file


def test_numbers_every_head_side_line_and_leaves_removed_lines_unnumbered() -> None:
    patch = (
        "@@ -1,3 +1,4 @@ def upload(data):\n def upload(data):\n-    send(data)\n+    a\n+    b\n x"
    )

    [hunk] = parse_hunks(patch)

    assert hunk.text.split("\n") == [
        "@@ +1 @@ def upload(data):",
        "      1   def upload(data):",
        "        -     send(data)",
        "      2 +     a",
        "      3 +     b",
        "      4   x",
    ]
    assert hunk.head_lines == frozenset({1, 2, 3, 4})
    assert hunk.added_lines == frozenset({2, 3})


def test_lines_before_the_first_hunk_header_are_skipped() -> None:
    assert parse_hunks("diff --git a/x b/x\nindex 1..2\n") == []
    assert parse_hunks("Binary files differ") == []


def test_the_no_newline_marker_is_not_a_line() -> None:
    [hunk] = parse_hunks("@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file")

    assert hunk.head_lines == frozenset({1})
    assert "No newline" not in hunk.text


def test_a_removed_file_has_no_head_lines() -> None:
    [hunk] = parse_hunks("@@ -1,2 +0,0 @@\n-gone\n-also gone")

    assert hunk.head_lines == frozenset()


def test_a_trailing_newline_in_a_patch_is_not_a_line() -> None:
    [hunk] = parse_hunks("@@ -1,1 +1,2 @@\n context\n+added\n")

    assert hunk.head_lines == frozenset({1, 2})


def test_an_empty_context_line_inside_the_hunk_counts_until_the_header_is_used_up() -> None:
    [hunk] = parse_hunks("@@ -1,3 +1,3 @@\n one\n\n three\n\n")

    assert hunk.head_lines == frozenset({1, 2, 3})


def test_each_hunk_header_starts_a_new_hunk() -> None:
    hunks = parse_hunks("@@ -1 +1 @@\n+a\n@@ -9,0 +10,2 @@\n+b\n+c")

    assert [hunk.head_lines for hunk in hunks] == [frozenset({1}), frozenset({10, 11})]


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "web/pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "go.sum",
        "static/app.min.js",
        "static/site.min.css",
        "static/app.min.mjs",
        "app.js.map",
        "dist/bundle.js",
        "vendor/lib/x.go",
        "web/node_modules/left-pad/index.js",
        "build/out.js",
        "third_party/zlib/zlib.c",
        "src/__generated__/schema.ts",
        "api/v1/service.pb.go",
        "proto/service_pb2.py",
        "src/Client.generated.cs",
    ],
)
def test_lockfiles_and_generated_or_vendored_files_are_low_value(path: str) -> None:
    assert is_low_value(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/upload.py",
        "builder.py",
        "src/build.py",
        "src/minimal.js",
        "docs/vendoring.md",
        "src/generated_report.py",
        "src/pb.go",
        "admin.js",
    ],
)
def test_ordinary_source_files_are_not_low_value(path: str) -> None:
    assert not is_low_value(path)


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".gitlab-ci.yml",
        ".circleci/config.yml",
        "azure-pipelines.yml",
        "build/Jenkinsfile",
    ],
)
def test_a_change_to_the_ci_setup_is_detected(path: str) -> None:
    assert touches_ci_config(changed_file(path))


def test_a_rename_away_from_the_ci_setup_is_detected() -> None:
    assert touches_ci_config(changed_file("old/ci.yml", previous_path=".github/workflows/ci.yml"))


def test_an_unrelated_yaml_file_is_not_ci_setup() -> None:
    assert not touches_ci_config(changed_file("config/app.yml"))
