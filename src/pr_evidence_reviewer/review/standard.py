"""The standard review: check out the head commit, build and save the evidence packet, ask the
model, and check every citation against what it was shown.

A checkout that can't be fetched or read never fails the review. The review falls back to the diff
and CI checks, says so in an evidence gap, and marks the evidence incomplete, so a supported verdict
carries a warning. A gap is never approval. Disk work (building, saving and citation reads) runs in
a worker thread so the event loop stays responsive; cache state is only touched on the loop.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable

from pr_evidence_reviewer.checkout.archive import RepoName
from pr_evidence_reviewer.checkout.cache import CheckoutCache, CheckoutTarget
from pr_evidence_reviewer.checkout.reader import CheckoutReader, CheckoutReadError
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot, split_repo_full_name
from pr_evidence_reviewer.domain.verdicts import VerdictSet
from pr_evidence_reviewer.errors import (
    CheckoutUnavailableError,
    GitHubAuthError,
    RepositoryTooLargeError,
)
from pr_evidence_reviewer.evidence.builder import DEFAULT_BUDGETS, Budgets, build_packet
from pr_evidence_reviewer.evidence.render import render_packet
from pr_evidence_reviewer.evidence.store import PacketStore
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.review.citations import (
    CheckoutCitationChecker,
    CitationChecker,
    SnapshotCitationChecker,
)
from pr_evidence_reviewer.review.verdicts import (
    MAX_CLAIMS_CHARS,
    NO_CLAIMS_NOTE,
    ClaimAssessor,
    verdict_set,
)

logger = logging.getLogger(__name__)

NOT_SAVED_NOTE = "The evidence for this review couldn't be saved on this computer."

# Errors from fetching or reading the checkout. Each carries a fixed, user-safe message.
_CHECKOUT_ERRORS = (CheckoutUnavailableError, RepositoryTooLargeError, GitHubAuthError)


class StandardReview:
    def __init__(
        self,
        provider: LLMProvider,
        checkouts: CheckoutCache,
        store: PacketStore,
        *,
        new_boundary: Callable[[], str] = lambda: secrets.token_hex(8),
        budgets: Budgets = DEFAULT_BUDGETS,
        max_claims_chars: int = MAX_CLAIMS_CHARS,
    ) -> None:
        self._assessor = ClaimAssessor(provider, new_boundary, max_claims_chars)
        self._checkouts = checkouts
        self._store = store
        self._budgets = budgets

    async def run(self, snapshot: PullRequestSnapshot, claims: list[Claim]) -> VerdictSet:
        """One model call for all claims that fit. Provider errors propagate; never a verdict."""
        if not claims:
            return VerdictSet(head_sha=snapshot.head.sha, verdicts=[], notes=[NO_CLAIMS_NOTE])
        sent = self._assessor.claims_to_send(claims)
        problem: str | None = None
        try:
            async with self._checkouts.lease(checkout_target(snapshot)) as checkout:
                reader = CheckoutReader(checkout)
                try:
                    packet = await asyncio.to_thread(
                        build_packet, snapshot, reader, sent, self._budgets
                    )
                except CheckoutReadError:
                    logger.warning("Reviewing without the repository", extra={"reason": "read"})
                else:
                    return await self._review(claims, sent, packet, reader)
        except _CHECKOUT_ERRORS as error:
            logger.warning("Reviewing without the repository", extra={"reason": error.code})
            problem = error.message
        packet = await asyncio.to_thread(
            build_packet, snapshot, None, sent, self._budgets, checkout_problem=problem
        )
        return await self._review(claims, sent, packet, None)

    async def _review(
        self,
        claims: list[Claim],
        sent: list[Claim],
        packet: EvidencePacket,
        reader: CheckoutReader | None,
    ) -> VerdictSet:
        saved = await asyncio.to_thread(self._store.save, packet)
        text, shown = render_packet(packet)
        result = await self._assessor.ask(sent, text)
        checker: CitationChecker = (
            SnapshotCitationChecker(shown)
            if reader is None
            else CheckoutCitationChecker(reader, packet.snapshot.head_sha, shown, index=shown)
        )
        try:
            verdicts = await asyncio.to_thread(verdict_set, claims, sent, result, packet, checker)
        except CheckoutReadError:
            # The cached copy changed under the lease (another process cleared it). Check the
            # citations against the diff and CI only, and say the evidence is incomplete.
            logger.warning("Checking citations without the repository", extra={"reason": "read"})
            verdicts = verdict_set(
                claims, sent, result, packet, SnapshotCitationChecker(shown), incomplete=True
            )
        if saved:
            return verdicts
        return verdicts.model_copy(update={"notes": [*verdicts.notes, NOT_SAVED_NOTE]})


def checkout_target(snapshot: PullRequestSnapshot) -> CheckoutTarget:
    """The head commit, fetched from the fork it comes from when there is one."""
    fork = split_repo_full_name(snapshot.head_repo)
    return CheckoutTarget(
        owner=snapshot.ref.owner,
        repo=snapshot.ref.repo,
        sha=snapshot.head.sha,
        head_repo=RepoName(*fork) if fork is not None else None,
    )
