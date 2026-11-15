import pytest

from pr_evidence_reviewer.errors import ProviderUnavailableError, UnknownProviderError
from pr_evidence_reviewer.llm.selection import select_provider
from tests.llm.fakes import FakeProvider

MISSING = FakeProvider(available=False, provider_id="missing", name="Missing")
READY = FakeProvider(provider_id="ready", name="Ready")
ALSO_READY = FakeProvider(provider_id="also_ready", name="Also ready")


def test_without_a_choice_picks_the_first_ready_provider() -> None:
    assert select_provider([MISSING, READY, ALSO_READY]) is READY


def test_without_a_choice_and_nothing_ready_explains_each_provider() -> None:
    other = FakeProvider(available=False, provider_id="other")

    with pytest.raises(ProviderUnavailableError) as raised:
        select_provider([MISSING, other])

    assert raised.value.message == "No model provider is ready. Not installed. Not installed."


def test_without_any_providers_says_none_is_ready() -> None:
    with pytest.raises(ProviderUnavailableError, match=r"No model provider is ready."):
        select_provider([])


def test_a_choice_wins_over_order() -> None:
    assert select_provider([READY, ALSO_READY], "also_ready") is ALSO_READY


def test_a_chosen_provider_that_isnt_ready_gives_its_own_reason() -> None:
    with pytest.raises(ProviderUnavailableError) as raised:
        select_provider([READY, MISSING], "missing")

    assert raised.value.message == "Not installed."


def test_an_unknown_choice_lists_the_offered_providers_without_echoing_it() -> None:
    with pytest.raises(UnknownProviderError) as raised:
        select_provider([MISSING, READY], "<script>")

    assert raised.value.http_status == 422
    assert raised.value.message == (
        "That model provider isn't offered here. Choose one of: Missing, Ready."
    )
