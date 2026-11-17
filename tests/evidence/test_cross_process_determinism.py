"""AC7 across processes (review CR7): symbol order can't depend on string hashing.

A TypeScript interface and class may share a name and a line (declaration merging), so two
definitions can differ only in `kind`. The packet must still come out byte-identical whatever
PYTHONHASHSEED a process uses. This is the one test that starts a subprocess: hash seeds are fixed
per process, so nothing else can show the difference.
"""

import os
import subprocess
import sys
from pathlib import Path

from pr_evidence_reviewer.evidence.builder import _symbol_order
from pr_evidence_reviewer.evidence.symbols import Symbol

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ("0", "1", "2", "3", "4", "5")

_BUILD = """
import sys
from pathlib import Path
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.evidence.builder import build_packet
from tests.evidence.helpers import reader_for
from tests.evidence.test_builder import at_head
from tests.review.builders import changed_file

files = {"src/merged.ts": b"interface Retry { a: number } class Retry { a = 1 }\\n"}
claim = Claim(id="c1", quote="Adds Retry.", statement="Adds `Retry`.", kind="behavior")
snap = at_head([changed_file("src/merged.ts", "@@ -1 +1 @@\\n-x\\n+y")])
packet = build_packet(snap, reader_for(Path(sys.argv[1]), files), [claim])
assert sorted(symbol.kind for symbol in packet.symbols) == ["class", "interface"], packet.symbols
sys.stdout.buffer.write(packet.canonical_json())
"""


def _packet_in_process(tmp_path: Path, seed: str) -> bytes:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script, test only
        [sys.executable, "-c", _BUILD, str(tmp_path / f"seed{seed}")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def test_symbols_tied_but_for_their_kind_come_out_in_the_same_order_in_every_process(
    tmp_path: Path,
) -> None:
    packets = {_packet_in_process(tmp_path, seed) for seed in SEEDS}

    assert len(packets) == 1


def test_the_symbol_order_tells_apart_every_field_a_symbol_shows() -> None:
    base = {"name": "Retry", "qualified_name": "Retry", "path": "a.ts", "start_line": 1}
    interface = Symbol(kind="interface", end_line=1, **base)
    klass = Symbol(kind="class", end_line=1, **base)

    assert _symbol_order(interface) != _symbol_order(klass)
