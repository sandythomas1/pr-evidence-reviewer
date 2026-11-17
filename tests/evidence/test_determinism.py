"""AC7: the same PR at the same head commit gives the same packet bytes, whatever the input order."""

import json
import re
from itertools import permutations
from pathlib import Path

from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot
from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.store import PacketStore
from tests.checkout.fakes import HEAD_SHA
from tests.evidence.helpers import reader_for
from tests.evidence.test_builder import CLAIM, FILES, at_head
from tests.review.builders import TEST_PATCH, UPLOAD_PATCH, changed_file, check

CLAIMS = [
    CLAIM,
    Claim(
        id="c2",
        quote="Adds a unit test for the retry loop.",
        statement="A unit test covers the retry loop.",
        kind="test",
    ),
    Claim(id="c10", quote="Retries three times.", statement="Uploads retry.", kind="behavior"),
]
TIMESTAMP = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:")


ORDERS = list(permutations(range(3)))


def pull_request(order: tuple[int, ...] = (0, 1, 2)) -> PullRequestSnapshot:
    files = [
        changed_file("src/upload.py", UPLOAD_PATCH),
        changed_file("tests/test_upload.py", TEST_PATCH, status="added", deletions=0),
        changed_file("docs/notes.md", "@@ -1 +1 @@\n-old\n+new"),
    ]
    checks = [check("tests"), check("lint", "failure"), check("build", None, status="queued")]
    return at_head([files[i] for i in order], checks=[checks[i] for i in reversed(order)])


def reordered_claims(order: tuple[int, ...]) -> list[Claim]:
    return [CLAIMS[i] for i in order]


def packet_at(root: Path, snap: PullRequestSnapshot, claims: list[Claim]) -> EvidencePacket:
    return build_packet(snap, reader_for(root, FILES), claims)


def test_shuffled_files_checks_and_claims_give_identical_bytes(tmp_path: Path) -> None:
    reference = packet_at(tmp_path / "first", pull_request(), CLAIMS)

    for run, order in enumerate(ORDERS):
        packet = packet_at(tmp_path / f"run{run}", pull_request(order), reordered_claims(order))

        assert packet.canonical_json() == reference.canonical_json()
        assert packet.content_hash() == reference.content_hash()


def test_the_packet_holds_repository_evidence_and_sorted_claims(tmp_path: Path) -> None:
    packet = packet_at(tmp_path, pull_request(), CLAIMS)

    assert [claim.id for claim in packet.claims] == ["c1", "c2", "c10"]
    assert [file.path for file in packet.diff] == sorted(file.path for file in packet.diff)
    assert packet.snapshot.checkout_status == "ok"
    assert packet.claim_evidence
    assert packet.symbols
    assert packet.test_mapping


def test_an_oversized_pull_request_is_cut_named_and_limited(tmp_path: Path) -> None:
    filler = "\n".join(
        f"+    log_line({n}, 'padding for an oversized change')" for n in range(4_000)
    )
    oversized = changed_file("src/big.py", f"@@ -1 +1 @@\n+short\n@@ -50,0 +50,4000 @@\n{filler}")
    snap = at_head([oversized, changed_file("src/upload.py", UPLOAD_PATCH)])

    packet = build_packet(snap, reader_for(tmp_path, FILES), CLAIMS)

    [big] = [file for file in packet.diff if file.path == "src/big.py"]
    assert big.cut
    assert len(big.hunks) == 1
    cuts = [gap for gap in packet.gaps if gap.kind == "budget_cut" and gap.claim_id is None]
    assert cuts
    assert all(gap.message in packet.scope_limitations for gap in cuts)
    assert packet.evidence_incomplete


def test_packets_are_stored_under_the_head_commit_and_saved_once(tmp_path: Path) -> None:
    packet = packet_at(tmp_path / "checkout", pull_request(), CLAIMS)
    store = PacketStore(tmp_path / "packets")

    assert store.save(packet)
    [path] = (tmp_path / "packets").rglob("*.json")
    assert path.parent.name == HEAD_SHA
    assert path.name == f"{packet.content_hash()}.json"
    written = path.read_bytes()
    modified = path.stat().st_mtime_ns

    assert store.save(packet)
    assert list((tmp_path / "packets").rglob("*")) == [path.parent.parent, path.parent, path]
    assert path.read_bytes() == written
    assert path.stat().st_mtime_ns == modified


def test_packet_bytes_hold_no_local_path_and_no_time(tmp_path: Path) -> None:
    packet = packet_at(tmp_path, pull_request(), CLAIMS)
    store = PacketStore(tmp_path / "packets")
    store.save(packet)
    [path] = (tmp_path / "packets").rglob("*.json")

    for data in (packet.canonical_json(), path.read_bytes()):
        text = data.decode()
        for local in (str(tmp_path), tmp_path.as_posix(), json.dumps(str(tmp_path))[1:-1]):
            assert local not in text
        assert not re.search(r"[A-Za-z]:[\\/]|(?<![\w.])/(?:Users|home|tmp)/", text)
        assert not TIMESTAMP.search(data)
