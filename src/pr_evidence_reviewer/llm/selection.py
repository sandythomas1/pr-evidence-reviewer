"""Picks the model provider for a review: the one the person chose, or else the first that's ready.

Every endpoint that calls a model resolves its provider here, so the rules and messages stay the same.
Status checks are local and cheap (no model call).
"""

from collections.abc import Sequence

from pr_evidence_reviewer.errors import ProviderUnavailableError, UnknownProviderError
from pr_evidence_reviewer.llm.base import LLMProvider


def select_provider(
    providers: Sequence[LLMProvider], provider_id: str | None = None
) -> LLMProvider:
    """Return the chosen provider if it's ready, or the first ready one when nothing was chosen.

    Raises UnknownProviderError (422) for an id this runner doesn't offer, and
    ProviderUnavailableError (503) with the provider's own reason when it isn't ready.
    """
    if provider_id is None:
        return _first_ready(providers)
    chosen = next((provider for provider in providers if provider.id == provider_id), None)
    if chosen is None:
        # The id comes from the request, so it is never echoed back.
        names = ", ".join(provider.name for provider in providers)
        raise UnknownProviderError(
            f"That model provider isn't offered here. Choose one of: {names}."
        )
    status = chosen.status()
    if not status.available:
        raise ProviderUnavailableError(status.detail)
    return chosen


def _first_ready(providers: Sequence[LLMProvider]) -> LLMProvider:
    statuses = [(provider, provider.status()) for provider in providers]
    for provider, status in statuses:
        if status.available:
            return provider
    details = " ".join(status.detail for _, status in statuses)
    raise ProviderUnavailableError(f"No model provider is ready. {details}".strip())
