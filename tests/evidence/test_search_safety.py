"""QA search suite (task 18): ReDoS bounds, event-loop responsiveness, hidden-evidence vectors (T5, T8,
C5, C8) and deterministic results."""

import asyncio
import time
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.evidence.search import (
    SearchLimits,
    search_literal,
    search_regex,
    select_corpus,
)
from tests.evidence.helpers import reader_for

CATASTROPHIC = ["(a+)+$", "(a|aa)*b", "(.*a){20}"]
LONG_LINE = b"a" * 50_000 + b"b\n"
QUERY_SECONDS = 0.5
SLACK_SECONDS = 0.5


@pytest.fixture(scope="module")
def long_line_reader(tmp_path_factory: pytest.TempPathFactory) -> CheckoutReader:
    return reader_for(tmp_path_factory.mktemp("cache"), {"data/line.txt": LONG_LINE})


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_a_line_over_the_length_cap_is_never_given_to_the_regex_engine(
    long_line_reader: CheckoutReader, pattern: str
) -> None:
    started = time.monotonic()

    result = search_regex(long_line_reader, select_corpus(long_line_reader), [pattern])

    assert time.monotonic() - started < SLACK_SECONDS
    assert result.hits == ()
    assert result.timed_out == ()
    assert result.limitations == ("Lines over 2,000 characters weren't searched in 1 file.",)


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_a_catastrophic_pattern_on_a_50_000_character_line_returns_within_its_time_cap(
    long_line_reader: CheckoutReader, pattern: str
) -> None:
    limits = SearchLimits(max_line_chars=100_000, query_seconds=QUERY_SECONDS)
    started = time.monotonic()

    result = search_regex(
        long_line_reader, select_corpus(long_line_reader), [pattern], limits=limits
    )

    assert time.monotonic() - started < QUERY_SECONDS + SLACK_SECONDS
    if pattern == "(a+)+$":
        assert result.timed_out == (pattern,)
        assert "The search ran out of time. Search terms not searched in full: 1." in (
            result.limitations
        )


def test_a_catastrophic_pattern_under_the_line_cap_times_out_as_a_search_gap(
    tmp_path: Path,
) -> None:
    reader = reader_for(tmp_path, {"a.txt": b"a" * 1_999 + b"b\n", "b.txt": b"aaa\n"})
    limits = SearchLimits(query_seconds=QUERY_SECONDS)
    started = time.monotonic()

    result = search_regex(reader, select_corpus(reader), ["(a+)+$"], limits=limits)

    assert time.monotonic() - started < QUERY_SECONDS + SLACK_SECONDS
    assert result.timed_out == ("(a+)+$",)
    assert result.hits == ()


def test_the_phase_deadline_bounds_several_slow_patterns_together(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {"a.txt": b"a" * 1_999 + b"b\n"})
    patterns = ["(a+)+$", "(?:a+)+$", "((a+)+)+$"]
    limits = SearchLimits(query_seconds=QUERY_SECONDS, phase_seconds=0.7)
    started = time.monotonic()

    result = search_regex(reader, select_corpus(reader), patterns, limits=limits)

    assert time.monotonic() - started < 0.7 + SLACK_SECONDS
    assert result.timed_out == tuple(patterns)


def test_the_event_loop_keeps_running_while_a_catastrophic_regex_runs_in_a_thread(
    long_line_reader: CheckoutReader,
) -> None:
    limits = SearchLimits(max_line_chars=100_000, query_seconds=1.0)
    corpus = select_corpus(long_line_reader)

    async def run() -> tuple[float, int]:
        done = asyncio.Event()
        gaps: list[float] = []

        async def ticker() -> None:
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        ticking = asyncio.create_task(ticker())
        result = await asyncio.to_thread(
            search_regex, long_line_reader, corpus, ["(a+)+$"], limits=limits
        )
        done.set()
        await ticking
        assert result.timed_out == ("(a+)+$",)
        return max(gaps), len(gaps)

    worst_gap, ticks = asyncio.run(run())

    assert worst_gap < 0.25
    assert ticks > 20


def test_a_literal_on_a_line_over_the_length_cap_is_never_matched(tmp_path: Path) -> None:
    long_line = b"x" * 1_500 + b" needle " + b"y" * 1_492 + b"\n"
    reader = reader_for(tmp_path, {"long.py": long_line, "short.py": b"needle\n"})

    result = search_literal(reader, select_corpus(reader), ["needle"])

    assert [(hit.path, hit.line) for hit in result.hits] == [("short.py", 1)]
    assert result.limitations == ("Lines over 2,000 characters weren't searched in 1 file.",)


def test_a_tracked_file_listed_in_gitignore_is_searched(tmp_path: Path) -> None:
    files = {
        ".gitignore": b"secret_config.py\nbuild-output/\n",
        "secret_config.py": b"BYPASS_AUTH = True\n",
        "build-output/flag.py": b"BYPASS_AUTH = False\n",
    }
    reader = reader_for(tmp_path, files)

    result = search_literal(reader, select_corpus(reader), ["BYPASS_AUTH"])

    assert [hit.path for hit in result.hits] == ["build-output/flag.py", "secret_config.py"]


@pytest.mark.parametrize("folder", ["dist", "vendor"])
def test_a_changed_file_in_a_vendored_folder_is_searched_and_an_unchanged_one_is_not(
    tmp_path: Path, folder: str
) -> None:
    files = {
        f"{folder}/changed.js": b"skipAuthCheck();\n",
        f"{folder}/unchanged.js": b"skipAuthCheck();\n",
        "src/app.min.js": b"skipAuthCheck();\n",
    }
    reader = reader_for(tmp_path, files)
    corpus = select_corpus(reader, changed_paths=[f"{folder}/changed.js"])

    result = search_literal(reader, corpus, ["skipAuthCheck"])

    assert [hit.path for hit in result.hits] == [f"{folder}/changed.js"]
    assert dict(corpus.skip_counts) == {"low_value": 2}


def test_caps_hold_in_path_order_with_changed_files_first(tmp_path: Path) -> None:
    files = {f"pkg/m{n:02}.py": b"token = 1\ntoken = 2\ntoken = 3\ntoken = 4\n" for n in range(10)}
    reader = reader_for(tmp_path, files)
    corpus = select_corpus(reader, changed_paths=["pkg/m09.py"])

    result = search_literal(reader, corpus, ["token"])

    assert len(result.hits) == 20
    assert [hit.path for hit in result.hits[::3]] == [
        "pkg/m09.py",
        *(f"pkg/m{n:02}.py" for n in range(6)),
    ]
    assert {hit.line for hit in result.hits} == {1, 2, 3}
    assert result.capped == ("token",)


def test_the_same_inputs_give_identical_results(tmp_path: Path) -> None:
    files = {f"src/f{n}.py": f"alpha = {n}\nbeta(alpha)\n".encode() for n in range(30)}
    reader = reader_for(tmp_path, files)
    changed = ["src/f7.py", "src/f3.py"]

    first = search_literal(reader, select_corpus(reader, changed_paths=changed), ["alpha", "beta"])
    second = search_literal(
        reader, select_corpus(reader, changed_paths=changed[::-1]), ["alpha", "beta"]
    )

    assert first == second
    assert first.hits[0].path == "src/f3.py"
