"""TASK-002 RFC 8785 and cross-language canonical-hash contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from itchlab_research.canonical_json import (
    canonical_json_bytes,
    config_hashes,
    identity_config_document,
)
from itchlab_research.config import ReplayConfig, load_config, parse_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "configs"


def _golden_text(name: str) -> str:
    return (GOLDEN_ROOT / name).read_text(encoding="utf-8").rstrip("\n")


def test_task_002_rfc_8785_reference_example() -> None:
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": '€$\u000f\nA\'B"\\\\"/',
        "literals": [None, True, False],
    }

    assert canonical_json_bytes(value) == (
        b'{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,'
        b'0.002,1e-27],"string":"\xe2\x82\xac$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )


def test_task_002_python_hashes_match_shared_cpp_goldens() -> None:
    config = load_config(GOLDEN_ROOT / "valid" / "replay.json", "replay")
    hashes = config_hashes(config)

    assert canonical_json_bytes(config_to_full_document(config)).decode() == _golden_text(
        "replay.canonical.json"
    )
    assert canonical_json_bytes(identity_config_document(config)).decode() == _golden_text(
        "replay.identity.canonical.json"
    )
    assert hashes.config_sha256 == _golden_text("replay.sha256")
    assert hashes.identity_config_sha256 == _golden_text("replay.identity.sha256")


def config_to_full_document(config: object) -> object:
    from itchlab_research.canonical_json import config_document

    return config_document(cast(ReplayConfig, config))


def test_task_002_property_order_and_whitespace_do_not_change_hash() -> None:
    original_text = (GOLDEN_ROOT / "valid" / "replay.json").read_text(encoding="utf-8")
    original_document = json.loads(original_text)
    reordered_text = json.dumps(
        dict(reversed(list(original_document.items()))), separators=(",", ":")
    )

    original = parse_config(original_text, "replay")
    reordered = parse_config(reordered_text, "replay")

    assert config_hashes(original) == config_hashes(reordered)


def test_task_002_locator_changes_affect_full_but_not_identity_hash() -> None:
    original_text = (GOLDEN_ROOT / "valid" / "replay.json").read_text(encoding="utf-8")
    changed_document = json.loads(original_text)
    changed_document["input"]["path"] = "different/local/source.gz"
    changed_document["input"]["sha256"] = "00" * 32

    original_hashes = config_hashes(parse_config(original_text, "replay"))
    changed_hashes = config_hashes(parse_config(json.dumps(changed_document), "replay"))

    assert original_hashes.config_sha256 != changed_hashes.config_sha256
    assert original_hashes.identity_config_sha256 == changed_hashes.identity_config_sha256


def test_task_002_map_property_order_does_not_change_dataset_model_or_hash() -> None:
    original_text = (GOLDEN_ROOT / "valid" / "dataset.json").read_text(encoding="utf-8")
    changed_document = json.loads(original_text)
    tick_sizes = changed_document["tick_size4_by_symbol"]
    changed_document["tick_size4_by_symbol"] = dict(reversed(list(tick_sizes.items())))

    original = parse_config(original_text, "dataset")
    changed = parse_config(json.dumps(changed_document), "dataset")

    assert original == changed
    assert config_hashes(original) == config_hashes(changed)
