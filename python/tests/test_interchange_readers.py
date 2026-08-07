"""TASK-016 safe production interchange reader tests."""

from __future__ import annotations

import ast
import hashlib
import json
import struct
from dataclasses import fields, is_dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

import itchlab_research.interchange.readers as readers_module
from itchlab_research.errors import ErrorCode, InterchangeReadError
from itchlab_research.interchange import (
    EventKind,
    InterchangeKind,
    SnapshotDepthLevel,
    TradingState,
    read_events,
    read_snapshots,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "interchange"
EVENT_PATH = GOLDEN_ROOT / "synthetic_events_v1.ilb"
SNAPSHOT_PATH = GOLDEN_ROOT / "synthetic_snapshots_v1.ilb"
EVENT_DIAGNOSTIC_PATH = GOLDEN_ROOT / "synthetic_events_v1.json"
SNAPSHOT_DIAGNOSTIC_PATH = GOLDEN_ROOT / "synthetic_snapshots_v1.json"

HEADER_SIZE = 104
SYMBOL_SIZE = 16
EVENT_SIZE = 72
SNAPSHOT_SIZE = 104
EVENTS_OFFSET = HEADER_SIZE + 2 * SYMBOL_SIZE
SNAPSHOTS_OFFSET = HEADER_SIZE + 2 * SYMBOL_SIZE


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _diagnostic(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mutated_file(
    tmp_path: Path,
    source: Path,
    *changes: tuple[int, bytes],
    suffix: bytes = b"",
    truncate: int | None = None,
) -> tuple[Path, str]:
    content = bytearray(source.read_bytes())
    for offset, replacement in changes:
        content[offset : offset + len(replacement)] = replacement
    if truncate is not None:
        del content[truncate:]
    content.extend(suffix)
    destination = tmp_path / source.name
    destination.write_bytes(content)
    return destination, hashlib.sha256(content).hexdigest()


def _event_error(path: Path, expected_sha256: str) -> InterchangeReadError:
    with pytest.raises(InterchangeReadError) as captured:
        list(read_events(path, expected_sha256=expected_sha256, chunk_records=10))
    return captured.value


def _snapshot_error(path: Path, expected_sha256: str) -> InterchangeReadError:
    with pytest.raises(InterchangeReadError) as captured:
        list(read_snapshots(path, expected_sha256=expected_sha256, chunk_records=10))
    return captured.value


def test_ct_bin_001_production_event_reader_matches_independent_diagnostic() -> None:
    expected = _diagnostic(EVENT_DIAGNOSTIC_PATH)
    batches = list(read_events(EVENT_PATH, expected_sha256=expected["sha256"], chunk_records=3))

    assert [len(batch) for batch in batches] == [3, 3, 3, 1]
    assert all(batch.metadata is batches[0].metadata for batch in batches)
    assert _jsonable(batches[0].metadata) == expected["metadata"]
    records = [record for batch in batches for record in batch.records]
    assert [_jsonable(record) for record in records] == expected["records"]
    assert batches[0].metadata.kind is InterchangeKind.EVENTS
    assert batches[0].metadata.trading_date == date(2019, 1, 30)
    assert records[0].event_kind is EventKind.ADD
    assert records[0].execution_price4 is None
    assert records[6].primary_reference == 0
    assert records[7].side is None


def test_ct_bin_001_production_snapshot_reader_matches_independent_diagnostic() -> None:
    expected = _diagnostic(SNAPSHOT_DIAGNOSTIC_PATH)
    batches = list(
        read_snapshots(SNAPSHOT_PATH, expected_sha256=expected["sha256"], chunk_records=1)
    )

    assert [len(batch) for batch in batches] == [1, 1]
    assert _jsonable(batches[0].metadata) == expected["metadata"]
    records = [record for batch in batches for record in batch.records]
    assert [_jsonable(record) for record in records] == expected["records"]
    assert records[0].trading_state is TradingState.TRADING
    assert records[0].levels[1] == SnapshotDepthLevel(1_652_200, 500, None, None)
    assert records[1].trading_state is TradingState.HALTED


@pytest.mark.parametrize("chunk_records", [1, 2, 10, 100])
def test_it_006_event_chunks_preserve_exact_source_order(chunk_records: int) -> None:
    batches = read_events(
        EVENT_PATH, expected_sha256=_sha256(EVENT_PATH), chunk_records=chunk_records
    )
    indices = [record.message_index for batch in batches for record in batch.records]

    assert indices == list(range(5, 15))


def test_task_016_internal_byte_limit_can_split_a_requested_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readers_module, "_MAX_BATCH_BYTES", EVENT_SIZE * 2)

    batches = list(read_events(EVENT_PATH, expected_sha256=_sha256(EVENT_PATH), chunk_records=10))

    assert [len(batch) for batch in batches] == [2, 2, 2, 2, 2]


def test_task_016_hash_mismatch_fails_before_first_batch() -> None:
    reader = read_events(EVENT_PATH, expected_sha256="00" * 32, chunk_records=1)

    with pytest.raises(InterchangeReadError) as captured:
        next(reader)

    assert captured.value.code is ErrorCode.HASH_MISMATCH


@pytest.mark.parametrize(
    ("offset", "replacement", "expected_code"),
    [
        (0, b"X", ErrorCode.SCHEMA_VERSION),
        (8, b"\x00\x01", ErrorCode.SCHEMA_VERSION),
        (10, struct.pack("<H", 103), ErrorCode.SCHEMA_VERSION),
        (12, struct.pack("<H", 71), ErrorCode.SCHEMA_VERSION),
        (14, struct.pack("<H", 1), ErrorCode.SCHEMA_VERSION),
        (16, struct.pack("<I", 1), ErrorCode.SCHEMA_VERSION),
        (20, struct.pack("<I", 20_191_332), ErrorCode.INVARIANT),
        (24, struct.pack("<H", 0), ErrorCode.INVARIANT),
        (26, struct.pack("<H", 3), ErrorCode.INVARIANT),
        (36, b"\0" * 32, ErrorCode.PARTIAL_ARTEFACT),
        (100, b"X", ErrorCode.INVARIANT),
        (104, struct.pack("<H", 2), ErrorCode.INVARIANT),
        (106, struct.pack("<H", 0), ErrorCode.INVARIANT),
        (108, b"\0", ErrorCode.INVARIANT),
    ],
)
def test_task_016_corrupt_event_header_and_dictionary_fail_stably(
    tmp_path: Path,
    offset: int,
    replacement: bytes,
    expected_code: ErrorCode,
) -> None:
    path, digest = _mutated_file(tmp_path, EVENT_PATH, (offset, replacement))

    assert _event_error(path, digest).code is expected_code


@pytest.mark.parametrize(
    ("suffix", "truncate"),
    [(b"X", None), (b"", EVENTS_OFFSET + 9 * EVENT_SIZE)],
)
def test_task_016_declared_event_size_mismatch_is_partial(
    tmp_path: Path,
    suffix: bytes,
    truncate: int | None,
) -> None:
    path, digest = _mutated_file(
        tmp_path,
        EVENT_PATH,
        suffix=suffix,
        truncate=truncate,
    )

    assert _event_error(path, digest).code is ErrorCode.PARTIAL_ARTEFACT


@pytest.mark.parametrize(
    ("offset", "replacement", "expected_code"),
    [
        (EVENTS_OFFSET + 57, struct.pack("<H", 1 << 10), ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 59, b"X", ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 24, struct.pack("<Q", 1), ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 55, b"\x02", ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 56, b"Z", ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 32, struct.pack("<Q", 0), ErrorCode.QUANTITY),
        (EVENTS_OFFSET + 44, struct.pack("<I", 299), ErrorCode.QUANTITY),
        (EVENTS_OFFSET + 60, b"\x01", ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 8, struct.pack("<Q", 86_400_000_000_000), ErrorCode.INVARIANT),
        (EVENTS_OFFSET + EVENT_SIZE, struct.pack("<Q", 5), ErrorCode.INVARIANT),
        (EVENTS_OFFSET + 9 * EVENT_SIZE + 64, b"X", ErrorCode.INVARIANT),
    ],
)
def test_task_016_corrupt_event_record_fails_before_batch_yield(
    tmp_path: Path,
    offset: int,
    replacement: bytes,
    expected_code: ErrorCode,
) -> None:
    path, digest = _mutated_file(tmp_path, EVENT_PATH, (offset, replacement))
    reader = read_events(path, expected_sha256=digest, chunk_records=10)

    with pytest.raises(InterchangeReadError) as captured:
        next(reader)

    assert captured.value.code is expected_code
    assert captured.value.record_index is not None


@pytest.mark.parametrize(
    ("offset", "replacement"),
    [
        (SNAPSHOTS_OFFSET + 19, b"\xd7"),
        (SNAPSHOTS_OFFSET + 36, b"X"),
        (SNAPSHOTS_OFFSET + 18, b"\x09"),
        (SNAPSHOTS_OFFSET + 24, struct.pack("<Q", 0)),
        (SNAPSHOTS_OFFSET + 48, b"\x02"),
        (SNAPSHOTS_OFFSET + 50, b"X"),
        (SNAPSHOTS_OFFSET + 48 + 28 + 4, struct.pack("<I", 1_652_300)),
        (SNAPSHOTS_OFFSET + SNAPSHOT_SIZE, struct.pack("<Q", 5)),
    ],
)
def test_task_016_corrupt_snapshot_flags_depth_and_order_fail(
    tmp_path: Path,
    offset: int,
    replacement: bytes,
) -> None:
    path, digest = _mutated_file(tmp_path, SNAPSHOT_PATH, (offset, replacement))

    error = _snapshot_error(path, digest)
    assert error.code in {ErrorCode.INVARIANT, ErrorCode.QUANTITY}
    assert error.record_index is not None


def test_task_016_valid_empty_final_event_file_yields_no_batches(tmp_path: Path) -> None:
    path, digest = _mutated_file(
        tmp_path,
        EVENT_PATH,
        (28, struct.pack("<Q", 0)),
        truncate=EVENTS_OFFSET,
    )

    assert list(read_events(path, expected_sha256=digest, chunk_records=10)) == []


@pytest.mark.parametrize(
    ("expected_sha256", "chunk_records", "expected_code"),
    [
        ("A" * 64, 1, ErrorCode.HASH_MISMATCH),
        ("0" * 63, 1, ErrorCode.HASH_MISMATCH),
        ("0" * 64, 0, ErrorCode.CONFIG_SCHEMA),
        ("0" * 64, True, ErrorCode.CONFIG_SCHEMA),
    ],
)
def test_task_016_reader_arguments_fail_with_stable_codes(
    expected_sha256: str,
    chunk_records: int,
    expected_code: ErrorCode,
) -> None:
    assert _event_error_for_arguments(expected_sha256, chunk_records).code is expected_code


def _event_error_for_arguments(expected_sha256: str, chunk_records: int) -> InterchangeReadError:
    with pytest.raises(InterchangeReadError) as captured:
        list(
            read_events(
                EVENT_PATH,
                expected_sha256=expected_sha256,
                chunk_records=chunk_records,
            )
        )
    return captured.value


def test_task_016_partial_directory_and_wrong_kind_are_rejected(tmp_path: Path) -> None:
    partial = tmp_path / "events.ilb.partial"
    partial.write_bytes(EVENT_PATH.read_bytes())
    partial_directory = tmp_path / "replay.partial"
    partial_directory.mkdir()
    nested_event = partial_directory / "events.ilb"
    nested_event.write_bytes(EVENT_PATH.read_bytes())

    assert _event_error(partial, _sha256(partial)).code is ErrorCode.PARTIAL_ARTEFACT
    assert _event_error(nested_event, _sha256(nested_event)).code is ErrorCode.PARTIAL_ARTEFACT
    assert _event_error(tmp_path / "missing.ilb", "00" * 32).code is ErrorCode.INPUT_PATH
    assert _event_error(tmp_path, "00" * 32).code is ErrorCode.INPUT_PATH
    assert _event_error(SNAPSHOT_PATH, _sha256(SNAPSHOT_PATH)).code is ErrorCode.SCHEMA_VERSION


def test_task_016_pickle_shaped_input_is_never_deserialised(tmp_path: Path) -> None:
    content = b"\x80\x04malicious-pickle".ljust(HEADER_SIZE, b"\0")
    path = tmp_path / "malicious.ilb"
    path.write_bytes(content)

    assert _event_error(path, hashlib.sha256(content).hexdigest()).code is ErrorCode.SCHEMA_VERSION


def test_task_016_production_interchange_package_imports_no_pickle_or_joblib() -> None:
    package_root = REPOSITORY_ROOT / "python" / "src" / "itchlab_research" / "interchange"
    imported_roots: set[str] = set()
    for source_path in package_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])

    assert imported_roots.isdisjoint({"pickle", "joblib"})
