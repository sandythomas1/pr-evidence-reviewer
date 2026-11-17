"""Test-file conventions: changed sources mapped to changed and likely tests (tasks 19 and 20, AC5)."""

import subprocess

import pytest

from pr_evidence_reviewer.domain.pull_request import ChangedFile
from pr_evidence_reviewer.evidence.test_mapping import (
    CONVENTIONS,
    SourceTests,
    is_test_path,
    map_tests,
)
from tests.review.builders import changed_file


def _changed(*paths: str, status: str = "modified") -> list[ChangedFile]:
    return [changed_file(path, status=status) for path in paths]


def _only(result_sources: tuple[SourceTests, ...]) -> SourceTests:
    [source] = result_sources
    return source


@pytest.fixture(autouse=True)
def _nothing_is_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("test mapping must not start a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.mark.parametrize(
    ("ecosystem", "source", "test"),
    [
        ("Python", "src/shop/upload.py", "tests/shop/test_upload.py"),
        ("Python", "shop/upload.py", "shop/upload_test.py"),
        ("JavaScript/TypeScript", "web/src/cart.ts", "web/src/cart.test.ts"),
        ("JavaScript/TypeScript", "web/src/Cart.tsx", "web/src/__tests__/Cart.tsx"),
        ("JavaScript/TypeScript", "lib/price.js", "test/price.spec.js"),
        ("Go", "internal/upload/client.go", "internal/upload/client_test.go"),
        (
            "Java/Kotlin",
            "src/main/java/com/acme/Upload.java",
            "src/test/java/com/acme/UploadTest.java",
        ),
        ("Java/Kotlin", "app/src/main/kotlin/Cart.kt", "app/src/test/kotlin/CartTests.kt"),
        ("C#", "src/Shop/Upload.cs", "tests/Shop.Tests/UploadTests.cs"),
        ("Ruby", "lib/shop/upload.rb", "spec/shop/upload_spec.rb"),
        ("Ruby", "app/models/cart.rb", "test/models/cart_test.rb"),
        ("Rust", "src/upload.rs", "tests/upload.rs"),
    ],
)
def test_each_ecosystem_maps_a_changed_source_file_to_its_existing_test(
    ecosystem: str, source: str, test: str
) -> None:
    result = map_tests(_changed(source), [source, test, "README.md"])

    mapped = _only(result.sources)
    assert mapped.source_path == source
    assert mapped.ecosystem == ecosystem
    assert mapped.likely_tests == (test,)
    assert mapped.changed_tests == ()
    assert not mapped.no_test_found
    assert result.gaps == ()


def test_every_listed_ecosystem_has_a_convention() -> None:
    assert {convention.ecosystem for convention in CONVENTIONS} == {
        "Python",
        "JavaScript/TypeScript",
        "Go",
        "Java/Kotlin",
        "C#",
        "Ruby",
        "Rust",
    }


def test_a_test_the_pull_request_changed_is_reported_with_its_source() -> None:
    files = _changed("src/upload.py", "tests/test_upload.py")

    result = map_tests(files, ["src/upload.py", "tests/test_upload.py", "tests/test_other.py"])

    mapped = _only(result.sources)
    assert mapped.changed_tests == ("tests/test_upload.py",)
    assert mapped.likely_tests == ()
    assert result.changed_tests == ("tests/test_upload.py",)


def test_every_changed_test_is_listed_even_without_a_matching_source() -> None:
    result = map_tests(_changed("tests/test_retry.py", "web/a.spec.ts"), [])

    assert result.sources == ()
    assert result.changed_tests == ("tests/test_retry.py", "web/a.spec.ts")


def test_a_source_file_with_no_test_is_a_gap() -> None:
    result = map_tests(_changed("src/billing.py"), ["src/billing.py", "tests/test_upload.py"])

    mapped = _only(result.sources)
    assert mapped.no_test_found
    assert result.gaps == (mapped,)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/guide.rst",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "uv.lock",
        ".github/workflows/ci.yml",
        "config/app.yaml",
        "src/__init__.py",
        "tests/conftest.py",
        "web/vite.config.ts",
        "web/src/types.d.ts",
        "vendor/lib/x.go",
        "dist/app.js",
        "api/service.pb.go",
    ],
)
def test_docs_config_lockfiles_and_generated_files_are_never_gaps(path: str) -> None:
    result = map_tests(_changed(path), [path])

    assert result.sources == ()
    assert result.gaps == ()


def test_a_removed_source_file_is_not_mapped() -> None:
    result = map_tests(_changed("src/old.py", status="removed"), [])

    assert result.sources == ()


def test_the_nearest_matching_test_wins_over_a_same_named_one_elsewhere() -> None:
    paths = ["src/a/utils.py", "tests/a/test_utils.py", "tests/b/test_utils.py"]

    result = map_tests(_changed("src/a/utils.py"), paths)

    assert _only(result.sources).likely_tests == ("tests/a/test_utils.py",)


def test_a_same_named_file_outside_a_test_folder_is_not_a_test() -> None:
    result = map_tests(_changed("src/upload.rs"), ["src/upload.rs", "examples/upload.rs"])

    assert _only(result.sources).no_test_found


def test_without_the_checkout_only_the_changed_files_are_used() -> None:
    files = _changed("src/upload.py", "tests/test_upload.py", "src/billing.py")

    result = map_tests(files, None)

    assert not result.repository_checked
    by_path = {source.source_path: source for source in result.sources}
    assert by_path["src/upload.py"].changed_tests == ("tests/test_upload.py",)
    assert by_path["src/billing.py"].no_test_found
    assert [source.source_path for source in result.sources] == ["src/billing.py", "src/upload.py"]


def test_results_are_sorted_and_repeatable_whatever_the_input_order() -> None:
    files = _changed("src/b.py", "src/a.py", "tests/test_b.py", "tests/test_a.py")
    paths = [file.path for file in files]

    first = map_tests(files, paths)
    second = map_tests(files[::-1], paths[::-1])

    assert first == second
    assert [source.source_path for source in first.sources] == ["src/a.py", "src/b.py"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_upload.py", True),
        ("tests/helpers.py", True),
        ("pkg/upload_test.go", True),
        ("web/src/__tests__/Cart.tsx", True),
        ("src/test/java/com/acme/Helper.java", True),
        ("spec/upload_spec.rb", True),
        ("src/upload.py", False),
        ("src/testing_utils.py", False),
        ("README.md", False),
    ],
)
def test_test_files_are_recognised_by_name_or_folder(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected
