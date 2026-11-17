"""Review round 1 for the standard review and its citation checks: refused names degrade visibly
(CR1), a mid-build read failure names its reason (CR5), shown diff lines stay citable when the head
file isn't readable text (CR2), and a code citation may use the path as the evidence showed it (CR4).
"""

from pathlib import Path
from typing import Any

import pytest

from pr_evidence_reviewer.checkout.archive import MIB, TreeEntry
from pr_evidence_reviewer.checkout.cache import CheckoutCache
from pr_evidence_reviewer.checkout.reader import CheckoutReadError, UnsafePathError
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.domain.pull_request import CommitRef, PullRequestSnapshot
from pr_evidence_reviewer.domain.verdicts import (
    CodeCitation,
    DiffCitation,
    ProposedCodeCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.render import render_packet
from pr_evidence_reviewer.evidence.store import PacketStore
from pr_evidence_reviewer.review import standard
from pr_evidence_reviewer.review.citations import CheckoutCitationChecker
from pr_evidence_reviewer.review.standard import (
    CHANGED_WHILE_READING,
    UNSAFE_NAME,
    StandardReview,
)
from tests.checkout.fakes import HEAD_SHA, FakeArchiveSource, blob
from tests.evidence.helpers import reader_for
from tests.llm.fakes import FakeProvider
from tests.review.builders import UPLOAD_PATCH, changed_file, head_files, shown_for, snapshot
from tests.review.test_verdicts import assessment

CLAIM = Claim(
    id="c1",
    quote="Retries failed uploads three times.",
    statement="A failed upload is retried up to three times.",
    kind="behavior",
)
SUPPORTED = {"assessments": [assessment("c1", check_citations=["tests"])]}
ONE_LINE_PATCH = "@@ -1 +1 @@\n-old\n+new"


async def _review(
    tmp_path: Path, files: dict[str, bytes], snap: PullRequestSnapshot, **source: Any
) -> EvidencePacket:
    store = PacketStore(tmp_path / "packets")
    run = StandardReview(
        FakeProvider(SUPPORTED),
        CheckoutCache(tmp_path / "cache", FakeArchiveSource(files, **source)),
        store,
        new_boundary=lambda: "b0und4ry",
    )
    verdicts = await run.run(snap, [CLAIM])
    assert verdicts.packet_hash is not None
    stored = store.load("acme/shop", snap.head.sha, verdicts.packet_hash)
    assert stored is not None
    return stored.packet


@pytest.mark.anyio
async def test_a_changed_file_with_a_refused_name_reviews_with_a_gap(tmp_path: Path) -> None:
    snap = snapshot(files=[changed_file("src/upload.py"), changed_file("a:b.py", UPLOAD_PATCH)])
    files = head_files(snapshot(files=[changed_file("src/upload.py")]))
    entries = [blob(path, data) for path, data in files.items()]

    packet = await _review(
        tmp_path, files, snap, entries=[*entries, blob("a:b.py", b"def f():\n    pass\n")]
    )

    assert packet.snapshot.checkout_status == "ok"
    assert any(gap.path == "a:b.py" and gap.kind == "skipped_files" for gap in packet.gaps)
    assert packet.evidence_incomplete


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "problem"),
    [(CheckoutReadError("blob gone"), CHANGED_WHILE_READING), (UnsafePathError("x"), UNSAFE_NAME)],
)
async def test_a_read_failure_while_building_falls_back_and_names_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, problem: str
) -> None:
    def failing_with_a_checkout(snap: Any, reader: Any, *args: Any, **kwargs: Any) -> Any:
        if reader is not None:
            raise error
        return build_packet(snap, reader, *args, **kwargs)

    monkeypatch.setattr(standard, "build_packet", failing_with_a_checkout)
    snap = snapshot(files=[changed_file("src/upload.py")])

    packet = await _review(tmp_path, head_files(snap), snap)

    [gap] = [gap for gap in packet.gaps if gap.kind == "checkout_unavailable"]
    assert gap.message.endswith(problem)
    assert "blob gone" not in gap.message
    assert packet.evidence_incomplete


def _shown_diff_checker(tmp_path: Path, files: dict[str, bytes], **extra: Any) -> Any:
    snap = snapshot(
        files=[changed_file(path, ONE_LINE_PATCH) for path in ("docs/latin1.txt", "data/big.txt")],
        head=CommitRef(ref="feature/retry", sha=HEAD_SHA),
    )
    reader = reader_for(tmp_path, files, **extra)
    return CheckoutCitationChecker(reader, HEAD_SHA, None, index=shown_for(snap))


def test_shown_diff_lines_of_a_file_that_isnt_text_at_head_stay_citable(tmp_path: Path) -> None:
    latin1 = {"docs/latin1.txt": "caf\xe9\n".encode("latin-1")}
    too_large = TreeEntry(
        path="data/big.txt", mode="100644", kind="blob", sha="e" * 40, size=6 * MIB
    )
    checks = _shown_diff_checker(tmp_path, latin1, extra_entries=[too_large])

    for path in ("docs/latin1.txt", "data/big.txt"):
        cited = checks.check_diff(ProposedDiffCitation(path=path, start_line=1, end_line=1))
        assert cited == DiffCitation(path=path, start_line=1, end_line=1)
    assert checks.drop_reasons() == {}


def test_a_code_citation_may_use_the_path_as_the_evidence_showed_it(tmp_path: Path) -> None:
    path = 'src/say"hi".py'
    body = b"def upload(data):\n    return data\n"
    snap = snapshot(
        files=[
            changed_file(path, "@@ -1,2 +1,2 @@\n def upload(data):\n-    pass\n+    return data")
        ],
        head=CommitRef(ref="feature/retry", sha=HEAD_SHA),
    )
    reader = reader_for(tmp_path, {path: body})
    text, shown = render_packet(build_packet(snap, reader, [CLAIM]))
    displayed = 'src/say\\"hi\\".py'
    assert displayed in text
    checks = CheckoutCitationChecker(reader, HEAD_SHA, shown, index=shown)

    cited = checks.check_code(
        ProposedCodeCitation(path=displayed, start_line=1, end_line=2, commit=HEAD_SHA, quote="")
    )

    assert cited == CodeCitation(path=path, start_line=1, end_line=2, commit=HEAD_SHA)
