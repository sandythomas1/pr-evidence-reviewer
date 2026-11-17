"""C5: files GitHub's download leaves out are fetched one by one up to a cap; past it, or when a
changed file can't be read at all, the evidence is marked incomplete and a supported verdict warns.
"""

from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.archive import BlobStore
from pr_evidence_reviewer.checkout.cache import CACHE_UNWRITABLE, CheckoutCache
from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot
from pr_evidence_reviewer.domain.verdicts import VerdictSet
from pr_evidence_reviewer.evidence.store import PacketStore
from pr_evidence_reviewer.review.standard import StandardReview, checkout_target
from pr_evidence_reviewer.review.verdicts import INCOMPLETE_NOTE
from tests.checkout.fakes import FakeArchiveSource, make_archive, regular
from tests.llm.fakes import FakeProvider
from tests.review.builders import UPLOAD_PATCH, changed_file, head_files, snapshot
from tests.review.test_verdicts import assessment

pytestmark = pytest.mark.anyio

REFETCH_CAP = 300
CHANGED = "app/upload.py"
CLAIM = Claim(
    id="c1",
    quote="Retries failed uploads three times.",
    statement="A failed upload is retried up to three times.",
    kind="behavior",
)
SUPPORTED = {"assessments": [assessment("c1", check_citations=["tests"])]}


def module_files(count: int) -> dict[str, bytes]:
    """Distinct small modules, so each has its own blob and none dedupe."""
    return {f"pkg/m{n:03d}.py": f"VALUE_{n:03d} = {n}\n".encode() for n in range(count)}


def changed_upload() -> PullRequestSnapshot:
    return snapshot(files=[changed_file(CHANGED, UPLOAD_PATCH)])


async def review(
    tmp_path: Path, source: FakeArchiveSource, snap: PullRequestSnapshot
) -> tuple[VerdictSet, EvidencePacket]:
    store = PacketStore(tmp_path / "packets")
    run = StandardReview(
        FakeProvider(SUPPORTED),
        CheckoutCache(tmp_path / "cache", source),
        store,
        new_boundary=lambda: "b0und4ry",
    )
    verdicts = await run.run(snap, [CLAIM])
    assert verdicts.packet_hash is not None
    stored = store.load(f"{snap.ref.owner}/{snap.ref.repo}", snap.head.sha, verdicts.packet_hash)
    assert stored is not None
    return verdicts, stored.packet


async def read_checkout(
    tmp_path: Path, source: FakeArchiveSource, snap: PullRequestSnapshot
) -> CheckoutReader:
    """The checkout the review published, leased again from the same cache."""
    async with CheckoutCache(tmp_path / "cache", source).lease(checkout_target(snap)) as checkout:
        return CheckoutReader(checkout)


async def test_a_mass_export_ignore_is_refetched_up_to_the_cap_and_marked(tmp_path: Path) -> None:
    snap = changed_upload()
    files = {**head_files(snap), **module_files(REFETCH_CAP + 5)}
    source = FakeArchiveSource(files, archive=make_archive([]))

    verdicts, packet = await review(tmp_path, source, snap)

    refetched = [sha for batch in source.blob_requests for sha in batch]
    assert len(refetched) == REFETCH_CAP
    reader = await read_checkout(tmp_path, source, snap)
    assert reader.refetch_capped
    assert reader.has(CHANGED)
    ignored = sorted(skip.path for skip in reader.skipped() if skip.reason == "export_ignored")
    assert ignored == [f"pkg/m{n:03d}.py" for n in range(REFETCH_CAP - 1, REFETCH_CAP + 5)]
    [capped] = [gap for gap in packet.gaps if gap.kind == "export_ignored"]
    assert capped.path is None
    assert capped.message == (
        "6 files weren't read: the repository's .gitattributes leaves them out of GitHub's "
        "download (export-ignore), and there are too many to fetch one by one."
    )
    assert capped.message in packet.scope_limitations
    assert packet.evidence_incomplete
    [verdict] = verdicts.verdicts
    assert verdict.status == "supported"
    assert INCOMPLETE_NOTE in verdict.notes


async def test_a_changed_file_that_cant_be_read_alone_marks_the_evidence(tmp_path: Path) -> None:
    snap = changed_upload()
    others = module_files(3)
    files = {**head_files(snap), **others}
    source = FakeArchiveSource(files, archive=make_archive(regular(others)), blobs={})

    verdicts, packet = await review(tmp_path, source, snap)

    [gap] = [gap for gap in packet.gaps if gap.kind == "skipped_files"]
    assert gap.path == CHANGED
    assert (
        gap.message
        == f"{CHANGED} wasn't read: its content couldn't be confirmed to match this commit."
    )
    assert gap.message in packet.scope_limitations
    assert not any(gap.kind == "export_ignored" for gap in packet.gaps)
    assert packet.evidence_incomplete
    [verdict] = verdicts.verdicts
    assert verdict.status == "supported"
    assert INCOMPLETE_NOTE in verdict.notes


async def test_a_complete_download_needs_no_refetch_and_no_warning(tmp_path: Path) -> None:
    snap = changed_upload()
    files = {**head_files(snap), **module_files(3)}
    source = FakeArchiveSource(files)

    verdicts, packet = await review(tmp_path, source, snap)

    assert source.blob_requests == []
    assert not packet.evidence_incomplete
    [verdict] = verdicts.verdicts
    assert verdict.status == "supported"
    assert INCOMPLETE_NOTE not in verdict.notes


async def test_a_refetched_file_that_cant_be_saved_falls_back_to_the_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S11: a disk error while storing a refetched file makes the repository unavailable,
    so the review uses the diff and CI checks and says why, instead of failing."""

    def disk_full(*args: object) -> None:
        raise OSError("disk full")

    snap = changed_upload()
    source = FakeArchiveSource(head_files(snap), archive=make_archive([]))
    monkeypatch.setattr(BlobStore, "add", disk_full)

    verdicts, packet = await review(tmp_path, source, snap)

    assert source.blob_requests
    assert packet.snapshot.checkout_status == "unavailable"
    [gap] = [gap for gap in packet.gaps if gap.kind == "checkout_unavailable"]
    assert gap.message.endswith(CACHE_UNWRITABLE)
    assert packet.evidence_incomplete
    [verdict] = verdicts.verdicts
    assert verdict.status == "supported"
    assert INCOMPLETE_NOTE in verdict.notes
