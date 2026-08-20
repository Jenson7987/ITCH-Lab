"""TASK-017 authenticated binary-to-Parquet conversion tests."""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import time
import tracemalloc
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

import itchlab_research.conversion.service as service_module
from itchlab_research.config import ConversionConfig, ConversionParquetConfig
from itchlab_research.conversion import convert_replays, event_schema, snapshot_schema
from itchlab_research.errors import ConversionError, ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "interchange"


def _config(
    tmp_path: Path, manifests: list[Path], *, allow_degraded: bool = False
) -> ConversionConfig:
    return ConversionConfig(
        schema_version=1,
        replay_manifests=tuple(path.relative_to(tmp_path).as_posix() for path in manifests),
        output_root="output",
        parquet=ConversionParquetConfig(
            compression="zstd",
            row_group_size=2,
            partition_keys=("trading_date", "symbol"),
        ),
        allow_degraded=allow_degraded,
    )


def _dataset(path: Path, schema: pa.Schema) -> ds.Dataset:
    partition_schema = pa.schema([schema.field("trading_date"), schema.field("symbol")])
    return ds.dataset(
        path,
        format="parquet",
        partitioning=ds.partitioning(partition_schema, flavor="hive"),
    )


def _flatten_snapshot(record: dict[str, Any], symbol: str) -> dict[str, Any]:
    result = {key: value for key, value in record.items() if key != "levels"}
    result["symbol"] = symbol
    for index, level in enumerate(record["levels"], start=1):
        for name, value in level.items():
            result[f"{name}_{index}"] = value
    return result


def test_it_007_conversion_preserves_dtypes_nulls_partitions_order_and_values(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    result = convert_replays(_config(tmp_path, [parent]), base_directory=tmp_path)

    assert result.status == "completed"
    assert result.event_rows == 10
    assert result.snapshot_rows == 2
    run = result.manifest_path.parent
    event_paths = sorted((run / "events").rglob("*.parquet"))
    snapshot_paths = sorted((run / "snapshots").rglob("*.parquet"))
    assert {
        path.relative_to(run).parent.as_posix() for path in [*event_paths, *snapshot_paths]
    } == {
        "events/trading_date=2019-01-30/symbol=AAPL",
        "events/trading_date=2019-01-30/symbol=MSFT.X",
        "snapshots/trading_date=2019-01-30/symbol=AAPL",
        "snapshots/trading_date=2019-01-30/symbol=MSFT.X",
    }
    assert all(
        parquet_file.metadata.row_group(index).num_rows <= 2
        for path in [*event_paths, *snapshot_paths]
        for parquet_file in [pq.ParquetFile(path)]
        for index in range(parquet_file.metadata.num_row_groups)
    )

    event_dataset = _dataset(run / "events", event_schema())
    assert event_dataset.schema.field("trading_date").type == pa.date32()
    assert event_dataset.schema.field("message_index").type == pa.uint64()
    assert event_dataset.schema.field("side").type == pa.int8()
    assert event_dataset.schema.field("price4").type == pa.uint32()
    assert event_dataset.schema.field("quantity").type == pa.uint64()
    actual_events = sorted(
        event_dataset.to_table().to_pylist(), key=lambda row: row["message_index"]
    )
    expected_events = json.loads(
        (GOLDEN_ROOT / "synthetic_events_v1.json").read_text(encoding="utf-8")
    )["records"]
    symbol_by_id = {1: "AAPL", 2: "MSFT.X"}
    for row in expected_events:
        row["symbol"] = symbol_by_id[row["symbol_id"]]
        row["trading_date"] = date.fromisoformat(row["trading_date"])
    assert actual_events == expected_events

    snapshots = snapshot_schema(2)
    snapshot_dataset = _dataset(run / "snapshots", snapshots)
    assert snapshot_dataset.schema.field("bid_price4_1").type == pa.uint32()
    assert snapshot_dataset.schema.field("ask_quantity_2").type == pa.uint64()
    actual_snapshots = sorted(
        snapshot_dataset.to_table().to_pylist(), key=lambda row: row["message_index"]
    )
    expected_snapshot_records = json.loads(
        (GOLDEN_ROOT / "synthetic_snapshots_v1.json").read_text(encoding="utf-8")
    )["records"]
    expected_snapshots = []
    for row in expected_snapshot_records:
        converted = _flatten_snapshot(row, symbol_by_id[row["symbol_id"]])
        converted["trading_date"] = date.fromisoformat(converted["trading_date"])
        expected_snapshots.append(converted)
    assert actual_snapshots == expected_snapshots

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "events": 10,
        "snapshots": 2,
        "parquet_files": 4,
        "by_partition": [
            {"trading_date": "2019-01-30", "symbol": "AAPL", "events": 6, "snapshots": 1},
            {"trading_date": "2019-01-30", "symbol": "MSFT.X", "events": 4, "snapshots": 1},
        ],
    }
    assert manifest["partition_keys"] == ["trading_date", "symbol"]
    assert manifest["sort_keys"] == ["message_index"]
    assert manifest["schemas"]["events"]["fields"][0] == {
        "name": "trading_date",
        "dtype": "date32[day]",
        "nullable": False,
    }
    assert str(tmp_path) not in result.manifest_path.read_text(encoding="utf-8")


def test_task_017_degraded_parent_requires_override_and_propagates_status(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory(degraded=True)
    config = _config(tmp_path, [parent])

    with pytest.raises(ConversionError) as captured:
        convert_replays(config, base_directory=tmp_path)

    assert captured.value.code is ErrorCode.INVARIANT
    assert not (tmp_path / "output").exists()

    result = convert_replays(
        replace(config, allow_degraded=True),
        base_directory=tmp_path,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.status == "degraded"
    assert manifest["status"] == "degraded"
    assert manifest["config"]["allow_degraded"] is True
    assert manifest["parents"][0]["status"] == "degraded"


def test_task_017_identity_reuses_verified_run_and_force_never_overwrites(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    config = _config(tmp_path, [parent])

    first = convert_replays(config, base_directory=tmp_path)
    reused = convert_replays(config, base_directory=tmp_path)
    forced = convert_replays(config, base_directory=tmp_path, force_new_run=True)

    assert reused.reused is True
    assert reused.conversion_id == first.conversion_id
    assert forced.reused is False
    assert forced.conversion_id != first.conversion_id
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    forced_manifest = json.loads(forced.manifest_path.read_text(encoding="utf-8"))
    assert forced_manifest["identity_sha256"] == first_manifest["identity_sha256"]
    assert first.manifest_path.is_file()


def test_task_017_reuse_rejects_manifest_or_parquet_tampering(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    config = _config(tmp_path, [parent])
    first = convert_replays(config, base_directory=tmp_path)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["events"] += 1
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ConversionError) as manifest_failure:
        convert_replays(config, base_directory=tmp_path)
    assert manifest_failure.value.code is ErrorCode.HASH_MISMATCH

    manifest["counts"]["events"] -= 1
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    event_entry = next(item for item in manifest["artefacts"] if item["kind"] == "events")
    snapshot_entry = next(item for item in manifest["artefacts"] if item["kind"] == "snapshots")
    event_path = first.manifest_path.parent / event_entry["path"]
    snapshot_path = first.manifest_path.parent / snapshot_entry["path"]
    event_path.write_bytes(snapshot_path.read_bytes())
    event_entry["sha256"] = hashlib.sha256(event_path.read_bytes()).hexdigest()
    event_entry["size_bytes"] = event_path.stat().st_size
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ConversionError) as parquet_failure:
        convert_replays(config, base_directory=tmp_path)
    assert parquet_failure.value.code is ErrorCode.HASH_MISMATCH


def test_task_017_multiple_days_convert_and_mismatched_depth_fails(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    first = replay_factory(trading_date="2019-01-30")
    second = replay_factory(trading_date="2019-01-31")
    result = convert_replays(_config(tmp_path, [first, second]), base_directory=tmp_path)

    assert result.event_rows == 20
    assert result.snapshot_rows == 4
    assert {path.parent.parent.name for path in result.manifest_path.parent.rglob("*.parquet")} == {
        "trading_date=2019-01-30",
        "trading_date=2019-01-31",
    }

    different_depth = replay_factory(trading_date="2019-02-01", large_event_count=1)
    mismatch_config = replace(
        _config(tmp_path, [first, different_depth]),
        output_root="depth-output",
    )
    with pytest.raises(ConversionError) as depth_failure:
        convert_replays(mismatch_config, base_directory=tmp_path)
    assert depth_failure.value.code is ErrorCode.SCHEMA_VERSION
    assert not (tmp_path / "depth-output").exists()


def test_task_017_service_rejects_unsafe_or_overlapping_output_paths(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    config = _config(tmp_path, [parent])

    with pytest.raises(ConversionError) as unsafe_parent:
        convert_replays(
            replace(config, replay_manifests=("../replay-manifest.json",)),
            base_directory=tmp_path,
        )
    assert unsafe_parent.value.code is ErrorCode.INPUT_PATH

    overlapping = replace(
        config,
        output_root=parent.parent.relative_to(tmp_path).as_posix(),
    )
    with pytest.raises(ConversionError) as unsafe_output:
        convert_replays(overlapping, base_directory=tmp_path)
    assert unsafe_output.value.code is ErrorCode.OUTPUT_PATH
    assert not (parent.parent / "conversion").exists()

    outside = tmp_path / "outside-output"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    with pytest.raises(ConversionError) as symlinked_output:
        convert_replays(replace(config, output_root="linked-output"), base_directory=tmp_path)
    assert symlinked_output.value.code is ErrorCode.OUTPUT_PATH
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert parent.is_file()


def test_task_017_partition_symbol_is_uri_encoded_without_changing_value(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory(first_symbol="A/B")
    result = convert_replays(_config(tmp_path, [parent]), base_directory=tmp_path)
    run = result.manifest_path.parent

    assert list((run / "events").glob("trading_date=*/symbol=A%2FB/*.parquet"))
    table = _dataset(run / "events", event_schema()).to_table(filter=ds.field("symbol") == "A/B")
    assert table.num_rows == 6
    assert set(table.column("symbol").to_pylist()) == {"A/B"}


def test_task_017_cancellation_and_write_failure_leave_only_partial_output(
    tmp_path: Path,
    replay_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = replay_factory()
    config = _config(tmp_path, [parent])
    cancelled = False

    def progress(_value: Any) -> None:
        nonlocal cancelled
        cancelled = True

    with pytest.raises(ConversionError) as captured:
        convert_replays(
            config,
            base_directory=tmp_path,
            cancel_requested=lambda: cancelled,
            progress=progress,
        )
    assert captured.value.code is ErrorCode.CANCELLED
    conversion_root = tmp_path / "output" / "conversion"
    assert list(conversion_root.glob("*.partial"))
    assert not list(conversion_root.glob("*/conversion-manifest.json"))
    assert parent.is_file()

    second_root = tmp_path / "failure-output"
    failing_config = replace(config, output_root="failure-output")

    def fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(service_module, "_write_dataset", fail_write)
    with pytest.raises(ConversionError) as write_failure:
        convert_replays(failing_config, base_directory=tmp_path)
    assert write_failure.value.code is ErrorCode.DISK_WRITE
    assert list((second_root / "conversion").glob("*.partial"))
    assert not list((second_root / "conversion").glob("*/conversion-manifest.json"))
    assert parent.is_file()


def test_task_017_hash_tampering_fails_before_output_creation(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory()
    events = parent.parent / "events.ilb"
    with events.open("ab") as stream:
        stream.write(b"X")

    with pytest.raises(ConversionError) as captured:
        convert_replays(_config(tmp_path, [parent]), base_directory=tmp_path)

    assert captured.value.code in {ErrorCode.HASH_MISMATCH, ErrorCode.PARTIAL_ARTEFACT}
    assert not (tmp_path / "output").exists()


def test_task_017_perf_007_perf_008_large_stream_conversion_throughput_and_memory(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    record_count = 120_000
    parent = replay_factory(large_event_count=record_count)
    config = replace(
        _config(tmp_path, [parent]),
        parquet=ConversionParquetConfig(
            compression="zstd",
            row_group_size=4096,
            partition_keys=("trading_date", "symbol"),
        ),
    )

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    started = time.perf_counter_ns()
    result = convert_replays(config, base_directory=tmp_path)
    elapsed_ns = max(time.perf_counter_ns() - started, 1)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_scale = 1 if sys.platform == "darwin" else 1024
    records_per_second = record_count * 1_000_000_000 / elapsed_ns
    print(
        json.dumps(
            {
                "PERF-007-records-per-second": records_per_second,
                "PERF-007-peak-rss-bytes": rss_after * rss_scale,
                "PERF-008-rss-growth-bytes": (rss_after - rss_before) * rss_scale,
            },
            sort_keys=True,
        )
    )

    assert result.event_rows == record_count
    assert result.snapshot_rows == 0
    assert peak < 128 * 1024 * 1024
    assert records_per_second > 0
    assert (rss_after - rss_before) * rss_scale < 256 * 1024 * 1024
    for path in result.manifest_path.parent.rglob("*.parquet"):
        assert all(
            pq.ParquetFile(path).metadata.row_group(index).num_rows <= 4096
            for index in range(pq.ParquetFile(path).metadata.num_row_groups)
        )
