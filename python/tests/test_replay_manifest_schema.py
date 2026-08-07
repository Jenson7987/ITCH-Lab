"""TASK-014 replay-manifest JSON contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas"
PACKAGED_ROOT = REPOSITORY_ROOT / "python" / "src" / "itchlab_research" / "_schemas"


def _validator() -> Draft202012Validator:
    replay_config = json.loads(
        (SCHEMA_ROOT / "replay-config.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((SCHEMA_ROOT / "replay-manifest.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resources(
        [
            (replay_config["$id"], Resource.from_contents(replay_config)),
            (manifest["$id"], Resource.from_contents(manifest)),
        ]
    )
    return Draft202012Validator(
        manifest,
        registry=registry,
        format_checker=FormatChecker(),
    )


def _completed_manifest() -> dict[str, Any]:
    digest = "01" * 32
    return {
        "artefacts": [
            {
                "kind": "events",
                "path": "events.ilb",
                "record_count": 2,
                "record_size": 72,
                "schema_version": 1,
                "sha256": digest,
                "size_bytes": 264,
            },
            {
                "depth": 2,
                "kind": "snapshots",
                "path": "snapshots.ilb",
                "record_count": 1,
                "record_size": 104,
                "schema_version": 1,
                "sha256": digest,
                "size_bytes": 224,
            },
        ],
        "build": {
            "application_version": "0.1.0",
            "build_type": "Release",
            "compiler": "Clang",
            "compiler_version": "18.1.0",
            "target": "Linux-x86_64",
        },
        "code_revision": "a" * 40,
        "completed_at": "2026-08-07T12:00:01.000000000Z",
        "config": {
            "input": {
                "exchange_timezone": "America/New_York",
                "path": "synthetic.itch",
                "sha256": digest,
                "trading_date": "2019-01-30",
            },
            "output": {"depth": 2, "emit_unchanged_trade_snapshots": False},
            "schema_version": 1,
            "selection": {
                "require_trading_state": False,
                "session_end_ns": 57_600_000_000_000,
                "session_start_ns": 34_200_000_000_000,
                "symbols": ["AAPL"],
            },
            "validation": {
                "invariant_interval": 1,
                "max_skipped_messages": 0,
                "mode": "strict",
            },
        },
        "config_sha256": digest,
        "counts": {
            "all_by_type": {"A": 1, "R": 1},
            "decoded_messages": 2,
            "directory_messages": 1,
            "errors_observed": 0,
            "filtered_instrument_messages": 0,
            "global_system_messages": 0,
            "messages_processed": 2,
            "selected_by_type": {"A": 1},
            "selected_events": 1,
            "selected_instrument_messages": 1,
            "skipped_messages": 0,
            "snapshots_written": 1,
        },
        "error_summary": {},
        "executable_sha256": digest,
        "global_session_events": [],
        "identity_config_sha256": digest,
        "identity_sha256": digest,
        "instruments": [
            {
                "final_book_digest": digest,
                "final_order_count": 1,
                "final_trading_state": "trading",
                "financial_status": "N",
                "market_category": "Q",
                "round_lot_size": 100,
                "round_lots_only": False,
                "stock_locate": 1,
                "symbol": "AAPL",
                "symbol_id": 1,
            }
        ],
        "publishable": True,
        "replay_id": "20260807T120000.000000000Z-010101010101",
        "schema_version": 1,
        "source": {
            "canonical_name": "synthetic.itch",
            "compression": "none",
            "exchange_timezone": "America/New_York",
            "framing": "itch-length-v1",
            "sha256": digest,
            "size_bytes": 184,
            "trading_date": "2019-01-30",
        },
        "started_at": "2026-08-07T12:00:00.000000000Z",
        "status": "completed",
    }


def test_ct_json_001_completed_replay_manifest_validates_and_unknown_key_fails() -> None:
    validator = _validator()
    manifest = _completed_manifest()
    validator.validate(manifest)

    unknown = copy.deepcopy(manifest)
    unknown["unexpected"] = True
    assert list(validator.iter_errors(unknown))

    debug_publishable = copy.deepcopy(manifest)
    debug_publishable["build"]["build_type"] = "Debug"
    assert list(validator.iter_errors(debug_publishable))

    dirty_publishable = copy.deepcopy(manifest)
    dirty_publishable["code_revision"] += "+dirty"
    assert list(validator.iter_errors(dirty_publishable))


def test_task_014_manifest_rejects_absolute_publishable_paths() -> None:
    validator = _validator()
    for field in ("source", "config"):
        manifest = _completed_manifest()
        if field == "source":
            manifest["source"]["canonical_name"] = "/Users/alice/private.itch"
        else:
            manifest["config"]["input"]["path"] = "C:\\Users\\alice\\private.itch"
        assert list(validator.iter_errors(manifest))


def test_task_014_manifest_schema_is_valid_and_packaged_identically() -> None:
    root = SCHEMA_ROOT / "replay-manifest.schema.json"
    schema = json.loads(root.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert root.read_bytes() == (PACKAGED_ROOT / root.name).read_bytes()
