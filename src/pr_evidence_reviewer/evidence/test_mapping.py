"""Which tests likely cover each changed source file, by file-naming conventions. Nothing is run.

For every changed source file, the conventions table names the test files that would cover it
(`upload.py` → `test_upload.py`, `Cart.java` → `CartTest.java`, ...). Those names are looked up among
the files at the head commit and the files the pull request changed. When several test files match,
the ones whose folders share the most with the source's folder win. A source with no matching test
is an evidence gap. Docs, config, lockfiles and generated code are never mapped, so they are never
gaps.

The table is data: add a `Convention` to support another ecosystem.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from pr_evidence_reviewer.domain.pull_request import ChangedFile
from pr_evidence_reviewer.evidence.diff import is_low_value

MAX_LIKELY_TESTS = 5

# Folders whose files are tests or test support.
_TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
# Folder names too common to say a test sits near its source.
_GENERIC_FOLDERS = _TEST_FOLDERS | {"src", "lib", "main", "java", "kotlin"}
# Source-language files that are setup or declarations, not code a test would cover.
_NOT_SOURCE_NAMES = frozenset({"__init__.py", "conftest.py", "setup.py"})
_NOT_SOURCE_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_NOT_SOURCE_INFIXES = (".config.",)


@dataclass(frozen=True)
class NamingRule:
    """A test file name: `prefix + <source stem> + suffix`. With `in_test_folder`, the name only
    counts inside a test folder (`tests/upload.rs`, `__tests__/Cart.tsx`)."""

    prefix: str
    suffix: str
    in_test_folder: bool = False

    def for_stem(self, stem: str) -> str:
        return f"{self.prefix}{stem}{self.suffix}"

    def matches(self, name: str) -> bool:
        return len(name) > len(self.prefix) + len(self.suffix) and (
            name.startswith(self.prefix) and name.endswith(self.suffix)
        )


@dataclass(frozen=True)
class Convention:
    ecosystem: str
    source_extensions: tuple[str, ...]
    test_names: tuple[NamingRule, ...]


def _names(*templates: tuple[str, str], extensions: Iterable[str]) -> tuple[NamingRule, ...]:
    return tuple(
        NamingRule(prefix, suffix + ext) for ext in extensions for prefix, suffix in templates
    )


_JS_TS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_JVM = (".java", ".kt")

CONVENTIONS: tuple[Convention, ...] = (
    Convention("Python", (".py",), _names(("test_", ""), ("", "_test"), extensions=[".py"])),
    Convention(
        "JavaScript/TypeScript",
        _JS_TS,
        (
            *_names(("", ".test"), ("", ".spec"), extensions=_JS_TS),
            *(NamingRule("", ext, in_test_folder=True) for ext in _JS_TS),
        ),
    ),
    Convention("Go", (".go",), _names(("", "_test"), extensions=[".go"])),
    Convention(
        "Java/Kotlin",
        _JVM,
        _names(("", "Test"), ("", "Tests"), ("", "IT"), ("Test", ""), extensions=_JVM),
    ),
    Convention("C#", (".cs",), _names(("", "Tests"), ("", "Test"), extensions=[".cs"])),
    Convention(
        "Ruby", (".rb",), _names(("", "_spec"), ("", "_test"), ("test_", ""), extensions=[".rb"])
    ),
    Convention(
        "Rust",
        (".rs",),
        (*_names(("", "_test"), ("", "_tests"), extensions=[".rs"]), NamingRule("", ".rs", True)),
    ),
)


@dataclass(frozen=True)
class SourceTests:
    source_path: str
    ecosystem: str
    changed_tests: tuple[str, ...]
    likely_tests: tuple[str, ...]

    @property
    def no_test_found(self) -> bool:
        return not self.changed_tests and not self.likely_tests


@dataclass(frozen=True)
class MappedTests:
    # One entry per changed source file, sorted by path.
    sources: tuple[SourceTests, ...]
    # Every test file the pull request changed, sorted.
    changed_tests: tuple[str, ...]
    # False when the checkout couldn't be read and only the changed files were looked at.
    repository_checked: bool

    @property
    def gaps(self) -> tuple[SourceTests, ...]:
        return tuple(source for source in self.sources if source.no_test_found)


def is_test_path(path: str, conventions: Iterable[Convention] = CONVENTIONS) -> bool:
    """A test file or test support: inside a test folder, or named like a test."""
    *folders, name = path.split("/")
    in_test_folder = any(folder in _TEST_FOLDERS for folder in folders)
    for convention in conventions:
        if not name.endswith(convention.source_extensions):
            continue
        if in_test_folder:
            return True
        if any(not rule.in_test_folder and rule.matches(name) for rule in convention.test_names):
            return True
    return False


def map_tests(
    changed_files: Iterable[ChangedFile],
    repository_paths: Iterable[str] | None,
    conventions: tuple[Convention, ...] = CONVENTIONS,
) -> MappedTests:
    """Map each changed source file to tests. `repository_paths` are the files at the head
    commit, or None when the checkout is unavailable (then only changed files are used)."""
    current = sorted({file.path for file in changed_files if file.status != "removed"})
    changed_tests = [path for path in current if is_test_path(path, conventions)]
    repository = set(repository_paths) if repository_paths is not None else set()
    tests_by_name = _index(changed_tests, repository, conventions)
    changed_set = set(changed_tests)

    sources: list[SourceTests] = []
    for path in current:
        convention = _source_convention(path, conventions)
        if convention is None or path in changed_set:
            continue
        found = _nearest(path, _candidates(path, convention, tests_by_name))
        sources.append(
            SourceTests(
                source_path=path,
                ecosystem=convention.ecosystem,
                changed_tests=tuple(test for test in found if test in changed_set),
                likely_tests=tuple(test for test in found if test not in changed_set)[
                    :MAX_LIKELY_TESTS
                ],
            )
        )
    return MappedTests(
        sources=tuple(sources),
        changed_tests=tuple(changed_tests),
        repository_checked=repository_paths is not None,
    )


def _index(
    changed_tests: list[str], repository: set[str], conventions: tuple[Convention, ...]
) -> dict[str, list[str]]:
    """Test files by file name, from the changed tests and the repository's test files."""
    index: dict[str, list[str]] = {}
    tests = set(changed_tests) | {path for path in repository if is_test_path(path, conventions)}
    for path in sorted(tests):
        index.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    return index


def _source_convention(path: str, conventions: tuple[Convention, ...]) -> Convention | None:
    name = path.rsplit("/", 1)[-1]
    if (
        is_low_value(path)
        or name.startswith(".")
        or name in _NOT_SOURCE_NAMES
        or name.endswith(_NOT_SOURCE_SUFFIXES)
        or any(infix in name for infix in _NOT_SOURCE_INFIXES)
        or is_test_path(path, conventions)
    ):
        return None
    return next((c for c in conventions if name.endswith(c.source_extensions)), None)


def _candidates(
    path: str, convention: Convention, tests_by_name: dict[str, list[str]]
) -> list[str]:
    name = path.rsplit("/", 1)[-1]
    stem = name[: name.rindex(".")]
    found: list[str] = []
    for rule in convention.test_names:
        for test in tests_by_name.get(rule.for_stem(stem), ()):
            if test != path and (not rule.in_test_folder or _in_test_folder(test)):
                found.append(test)
    return sorted(set(found))


def _nearest(source: str, tests: list[str]) -> list[str]:
    """The tests whose folders share the most specific folder names with the source's."""
    if not tests:
        return []
    source_folders = _specific_folders(source)
    scores = {test: sum((source_folders & _specific_folders(test)).values()) for test in tests}
    best = max(scores.values())
    return sorted(test for test, score in scores.items() if score == best)


def _specific_folders(path: str) -> Counter[str]:
    return Counter(folder for folder in path.split("/")[:-1] if folder not in _GENERIC_FOLDERS)


def _in_test_folder(path: str) -> bool:
    return any(folder in _TEST_FOLDERS for folder in path.split("/")[:-1])
