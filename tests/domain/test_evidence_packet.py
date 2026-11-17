import json

import pytest
from pydantic import ValidationError

from pr_evidence_reviewer.domain.evidence import (
    RULESET_VERSION,
    SCHEMA_VERSION,
    ClaimRef,
    EvidenceGap,
    EvidencePacket,
    PacketSnapshot,
    StoredPacket,
)

SHA = "a" * 40


def packet(**overrides: object) -> EvidencePacket:
    fields: dict[str, object] = {
        "snapshot": PacketSnapshot(
            repo="acme/shop",
            pr_number=7,
            base_sha="b" * 40,
            head_sha=SHA,
            tree_sha=None,
            checkout_status="unavailable",
            files_verified=0,
            files_skipped=0,
        ),
        "claims": (ClaimRef(id="c1", statement="Retries uploads.", kind="behavior"),),
        "gaps": (EvidenceGap(id="g1", kind="no_ci", message="No CI checks were reported."),),
        "evidence_incomplete": True,
    }
    fields.update(overrides)
    return EvidencePacket.model_validate(fields)


def test_the_canonical_bytes_do_not_depend_on_field_order() -> None:
    first = packet()
    data = json.loads(first.canonical_json())
    shuffled = json.dumps(dict(reversed(list(data.items()))))

    second = EvidencePacket.model_validate_json(shuffled)

    assert second.canonical_json() == first.canonical_json()
    assert second.content_hash() == first.content_hash()


def test_the_hash_is_sha256_of_sorted_compact_json() -> None:
    first = packet()
    raw = first.canonical_json()

    assert raw == json.dumps(
        first.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert len(first.content_hash()) == 64
    assert first.content_hash() != packet(evidence_incomplete=False).content_hash()


def test_the_packet_records_its_schema_and_ruleset_versions() -> None:
    data = json.loads(packet().canonical_json())

    assert data["schema_version"] == SCHEMA_VERSION
    assert data["ruleset_version"] == RULESET_VERSION


def test_unknown_fields_are_refused_and_models_are_frozen() -> None:
    with pytest.raises(ValidationError):
        EvidencePacket.model_validate({**packet().model_dump(), "approved": True})
    with pytest.raises(ValidationError):
        packet().evidence_incomplete = False  # type: ignore[misc]


def test_non_ascii_text_is_kept_as_is_in_the_canonical_bytes() -> None:
    claim = ClaimRef(id="c1", statement="Café résumé", kind="docs")

    raw = packet(claims=(claim,)).canonical_json()

    assert "Café".encode() in raw


def test_a_stored_packet_round_trips() -> None:
    original = packet()
    stored = StoredPacket(content_hash=original.content_hash(), packet=original)

    loaded = StoredPacket.model_validate_json(stored.model_dump_json())

    assert loaded.packet == original
