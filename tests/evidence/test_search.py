"""Corpus selection, literal and regex search over a checkout (task 17)."""

from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.evidence.search import (
    SearchLimits,
    SearchPatternError,
    search_literal,
    search_regex,
    select_corpus,
)
from tests.evidence.helpers import reader_for

FILES = {
    "src/upload.py": b"import time\n\ndef upload(data):\n    retry_upload(data)\n    return True\n",
    "src/retry.py": b"MAX_RETRIES = 3\n\ndef retry_upload(data):\n    return MAX_RETRIES\n",
    "README.md": b"Uploads retry MAX_RETRIES times.\n",
    "logo.png": b"\x89PNG\r\n\x1a\n\0\0\0",
    "latin1.txt": b"MAX_RETRIES caf\xe9\n",
    "big.bin": b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 9\n",
    "dist/bundle.js": b"var MAX_RETRIES = 3;\n",
}


@pytest.fixture(scope="module")
def reader(tmp_path_factory: pytest.TempPathFactory) -> CheckoutReader:
    return reader_for(tmp_path_factory.mktemp("cache"), FILES)


def test_the_corpus_is_text_files_changed_first_then_by_path(reader: CheckoutReader) -> None:
    corpus = select_corpus(reader, changed_paths=["src/upload.py"])

    assert corpus.paths == ("src/upload.py", "README.md", "src/retry.py")
    assert dict(corpus.skip_counts) == {"binary": 1, "lfs": 1, "low_value": 1, "undecodable": 1}
    assert corpus.skipped == ()


def test_changed_or_claim_relevant_files_that_cannot_be_searched_are_recorded(
    reader: CheckoutReader,
) -> None:
    corpus = select_corpus(reader, changed_paths=["logo.png"], relevant_paths=["latin1.txt"])

    assert [(skip.path, skip.reason) for skip in corpus.skipped] == [
        ("latin1.txt", "undecodable"),
        ("logo.png", "binary"),
    ]


def test_unsafe_changed_or_relevant_paths_are_recorded_not_searched(
    reader: CheckoutReader,
) -> None:
    corpus = select_corpus(reader, changed_paths=["../x"], relevant_paths=["C:/x"])

    assert corpus.paths == ("README.md", "src/retry.py", "src/upload.py")
    assert [(skip.path, skip.reason) for skip in corpus.skipped] == [
        ("../x", "unsafe_name"),
        ("C:/x", "unsafe_name"),
    ]


def test_files_over_the_size_cap_are_skipped(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {"a.py": b"x" * 100 + b"\n", "b.py": b"y\n"})

    corpus = select_corpus(reader, changed_paths=["a.py"], limits=SearchLimits(max_file_bytes=50))

    assert corpus.paths == ("b.py",)
    assert [(skip.path, skip.reason) for skip in corpus.skipped] == [("a.py", "too_large")]


def test_the_corpus_byte_cap_keeps_changed_files_first_and_names_the_rest(tmp_path: Path) -> None:
    files = {f"f{n}.py": b"x" * 99 + b"\n" for n in range(5)}
    reader = reader_for(tmp_path, files)

    corpus = select_corpus(
        reader, changed_paths=["f4.py"], limits=SearchLimits(max_corpus_bytes=250)
    )

    assert corpus.paths == ("f4.py", "f0.py")
    assert dict(corpus.skip_counts) == {"corpus_cap": 3}
    assert corpus.limitations == (
        "The repository is too large to search in full. Not searched: 3 files.",
    )


def test_a_changed_file_the_checkout_does_not_hold_is_recorded_with_its_reason(
    tmp_path: Path,
) -> None:
    files = {"src/a.py": b"a\n", "src/hidden.py": b"secret()\n"}
    reader = reader_for(tmp_path, files, archived={"src/a.py": b"a\n"})

    corpus = select_corpus(reader, changed_paths=["src/hidden.py"])

    assert corpus.paths == ("src/a.py",)
    assert [(skip.path, skip.reason) for skip in corpus.skipped] == [
        ("src/hidden.py", "unverified")
    ]


def test_literal_hits_carry_path_line_and_context(reader: CheckoutReader) -> None:
    corpus = select_corpus(reader, changed_paths=["src/upload.py"])

    result = search_literal(reader, corpus, ["retry_upload"])

    assert [(hit.path, hit.line) for hit in result.hits] == [
        ("src/upload.py", 4),
        ("src/retry.py", 3),
    ]
    first = result.hits[0]
    assert first.query == "retry_upload"
    assert first.context == (
        (2, ""),
        (3, "def upload(data):"),
        (4, "    retry_upload(data)"),
        (5, "    return True"),
    )
    assert result.timed_out == ()


def test_literal_queries_are_identifier_bounded_and_case_sensitive(reader: CheckoutReader) -> None:
    corpus = select_corpus(reader)

    result = search_literal(reader, corpus, ["upload", "max_retries", "MAX_RETRIES"])

    assert [(hit.path, hit.line) for hit in result.for_query("upload")] == [("src/upload.py", 3)]
    assert result.for_query("max_retries") == ()
    assert [(hit.path, hit.line) for hit in result.for_query("MAX_RETRIES")] == [
        ("README.md", 1),
        ("src/retry.py", 1),
        ("src/retry.py", 4),
    ]


def test_a_query_ending_in_punctuation_is_bounded_only_where_it_is_an_identifier(
    tmp_path: Path,
) -> None:
    reader = reader_for(tmp_path, {"a.py": b"xupload(1)\nupload(2)\n"})

    result = search_literal(reader, select_corpus(reader), ["upload("])

    assert [hit.line for hit in result.hits] == [2]


def test_a_later_bounded_occurrence_on_the_same_line_is_found(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {"a.py": b"uploader = upload\n"})

    result = search_literal(reader, select_corpus(reader), ["upload"])

    assert [hit.line for hit in result.hits] == [1]


def test_queries_are_deduplicated_and_capped_with_a_limitation(reader: CheckoutReader) -> None:
    corpus = select_corpus(reader)
    queries = ["MAX_RETRIES", "MAX_RETRIES", "", "  ", *(f"q{n}" for n in range(5))]

    result = search_literal(reader, corpus, queries, limits=SearchLimits(max_queries=3))

    assert result.queries == ("MAX_RETRIES", "q0", "q1")
    assert tuple(item.message for item in result.limitations) == (
        "There were too many search terms. Not searched: 3 terms.",
    )


def test_hits_are_capped_per_file_and_per_query_in_corpus_order(tmp_path: Path) -> None:
    files = {f"m{n}.py": b"hit\n" * 5 for n in range(4)}
    reader = reader_for(tmp_path, files)
    limits = SearchLimits(max_hits_per_query=7, max_hits_per_file=3)

    result = search_literal(reader, select_corpus(reader), ["hit"], limits=limits)

    assert [(hit.path, hit.line) for hit in result.hits] == [
        ("m0.py", 1), ("m0.py", 2), ("m0.py", 3),
        ("m1.py", 1), ("m1.py", 2), ("m1.py", 3),
        ("m2.py", 1),
    ]  # fmt: skip
    assert result.capped == ("hit",)


def test_context_lines_are_cut_to_the_context_width(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {"a.py": b"x = 'needle' + '" + b"y" * 500 + b"'\n"})

    result = search_literal(reader, select_corpus(reader), ["needle"])

    [(_line, text)] = result.hits[0].context
    assert len(text) == 240


def test_regex_hits_carry_path_line_and_context(reader: CheckoutReader) -> None:
    pattern = r"def \w+_upload\("

    result = search_regex(reader, select_corpus(reader), [pattern])

    assert [(hit.path, hit.line, hit.query) for hit in result.hits] == [
        ("src/retry.py", 3, pattern)
    ]
    assert result.hits[0].context[0] == (1, "MAX_RETRIES = 3")


@pytest.mark.parametrize(
    "pattern",
    ["a" * 257, "a{1001}", "(ab){2,5000}", "x{0,1001}", "(unclosed"],
)
def test_unsafe_or_invalid_patterns_are_refused(reader: CheckoutReader, pattern: str) -> None:
    with pytest.raises(SearchPatternError):
        search_regex(reader, select_corpus(reader), [pattern])


def test_a_repeat_count_at_the_limit_is_allowed(reader: CheckoutReader) -> None:
    result = search_regex(reader, select_corpus(reader), ["a{1000}", "x{2,1000}"])

    assert result.hits == ()


def test_the_phase_deadline_stops_a_literal_search_with_a_timeout(reader: CheckoutReader) -> None:
    times = iter([0.0, 100.0])

    result = search_literal(
        reader,
        select_corpus(reader),
        ["MAX_RETRIES", "upload"],
        clock=lambda: next(times, 100.0),
    )

    assert result.hits == ()
    assert result.timed_out == ("MAX_RETRIES", "upload")
    assert tuple(item.message for item in result.limitations) == (
        "The search ran out of time. Search terms not searched in full: 2.",
    )
