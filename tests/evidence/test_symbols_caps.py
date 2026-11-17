"""QA symbol caps (task 23, T6/C9): oversized or pathological files return nothing, quickly, with a
reason, from both `enclosing()` and `definitions()`."""

import time
from pathlib import Path

import pytest

from pr_evidence_reviewer.evidence.symbols import SymbolIndex, SymbolLimits
from tests.evidence.helpers import reader_for

FAST_SECONDS = 1.0
MINIFIED_FUNCTION = b"function rareName(a){return a+1};"
WIDE_600_KIB_PYTHON = b"def rareName():\n    x = '" + b"y" * 10_000 + b"'\n" + b"# pad\n" * 101_000


def _minified_js(total_bytes: int) -> bytes:
    repeats = total_bytes // len(MINIFIED_FUNCTION)
    return MINIFIED_FUNCTION * repeats + b"\n"


@pytest.mark.parametrize(
    ("path", "data", "reason"),
    [
        ("static/app.js", _minified_js(5_000_000), "too_large"),
        ("src/wide.py", WIDE_600_KIB_PYTHON, "too_large"),
        ("src/long.py", b"def rareName():\n    x = '" + b"y" * 10_000 + b"'\n", "long_line"),
    ],
    ids=["5-mb-minified-js", "600-kib-with-a-long-line", "small-with-a-long-line"],
)
def test_oversized_files_return_nothing_quickly_from_both_operations(
    tmp_path: Path, path: str, data: bytes, reason: str
) -> None:
    index = SymbolIndex(reader_for(tmp_path, {path: data}))

    started = time.monotonic()
    enclosing = index.enclosing(path, 1)
    definitions = index.definitions("rareName")
    elapsed = time.monotonic() - started

    assert elapsed < FAST_SECONDS
    assert enclosing.symbols == ()
    assert definitions.symbols == ()
    assert [(skip.path, skip.reason) for skip in enclosing.skipped] == [(path, reason)]
    assert [(skip.path, skip.reason) for skip in definitions.skipped] == [(path, reason)]


def test_the_600_kib_file_is_over_the_size_cap() -> None:
    assert len(WIDE_600_KIB_PYTHON) > 600 * 1024 > SymbolLimits().max_file_bytes


def test_the_node_cap_stops_the_walk(tmp_path: Path) -> None:
    source = b"".join(b"def f%d():\n    return %d\n" % (n, n) for n in range(200))
    index = SymbolIndex(
        reader_for(tmp_path, {"many.py": source}), limits=SymbolLimits(max_nodes=50)
    )

    enclosing = index.enclosing("many.py", 2)
    definitions = index.definitions("f1")

    assert enclosing.symbols == ()
    assert definitions.symbols == ()
    assert [skip.reason for skip in enclosing.skipped] == ["too_many_nodes"]
    assert [skip.reason for skip in definitions.skipped] == ["too_many_nodes"]


def test_the_default_node_cap_allows_an_ordinary_file(tmp_path: Path) -> None:
    source = b"".join(b"def f%d():\n    return %d\n" % (n, n) for n in range(200))
    index = SymbolIndex(reader_for(tmp_path, {"many.py": source}))

    [symbol] = index.enclosing("many.py", 2).symbols

    assert symbol.qualified_name == "f0"
