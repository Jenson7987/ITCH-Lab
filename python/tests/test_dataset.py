"""TASK-019 authenticated causal-dataset construction tests."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import itchlab_research.datasets.service as service_module
from itchlab_research.canonical_json import config_document
from itchlab_research.config import (
    DatasetConfig,
    FeatureConfig,
    LabelConfig,
    PartitionConfig,
    SamplingConfig,
)
from itchlab_research.datasets import build_dataset
from itchlab_research.errors import DatasetBuildError, ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PATH = REPOSITORY_ROOT / "tests" / "golden" / "datasets" / "it008-expected.json"


def _config(tmp_path: Path, manifest: Path, *, row_stride: int = 1) -> DatasetConfig:
    return DatasetConfig(
        schema_version=1,
        conversion_manifests=(manifest.relative_to(tmp_path).as_posix(),),
        symbols=("AAPL",),
        tick_size4_by_symbol=(("AAPL", 100),),
        features=FeatureConfig(
            depth_levels=(1, 5, 10),
            event_windows=(20, 100, 500),
            clock_windows_ns=(100_000_000, 1_000_000_000),
        ),
        labels=LabelConfig(
            primary_event_horizon=100,
            secondary_event_horizons=(20, 500),
            flat_threshold_ticks=0,
        ),
        sampling=SamplingConfig(row_stride=row_stride),
        partitions=PartitionConfig(
            train_dates=("2019-01-30",),
            validation_dates=("2019-01-31",),
            test_dates=("2019-02-01",),
        ),
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_it_008_builds_frozen_causal_dataset_with_expected_rows_and_lineage(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    result = build_dataset(_config(tmp_path, parent), base_directory=tmp_path)

    assert result.status == "completed"
    assert result.reused is False
    assert result.retained_rows == 9
    assert result.parquet_files == 3
    assert dict(result.partition_rows) == {"train": 3, "validation": 3, "test": 3}
    assert dict(result.class_counts) == {"down": 3, "flat": 3, "up": 3}

    manifest = _read_manifest(result.manifest_path)
    config_schema = json.loads(
        (REPOSITORY_ROOT / "schemas" / "dataset-config.schema.json").read_text(encoding="utf-8")
    )
    manifest_schema = json.loads(
        (REPOSITORY_ROOT / "schemas" / "dataset-manifest.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(
        manifest_schema,
        registry=Registry().with_resources(
            [
                (config_schema["$id"], Resource.from_contents(config_schema)),
                (manifest_schema["$id"], Resource.from_contents(manifest_schema)),
            ]
        ),
        format_checker=FormatChecker(),
    )
    validator.validate(manifest)
    unknown = copy.deepcopy(manifest)
    unknown["unexpected"] = True
    assert list(validator.iter_errors(unknown))
    unsafe_child = copy.deepcopy(manifest)
    unsafe_child["artefacts"][0]["path"] = "/private/dataset.parquet"
    assert list(validator.iter_errors(unsafe_child))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    assert manifest["counts"]["rows"] == expected["rows"]
    assert manifest["counts"]["classes"] == expected["classes"]
    assert manifest["counts"]["label_availability"] == expected["label_availability"]
    assert manifest["partitions"] == {
        "train_dates": ["2019-01-30"],
        "validation_dates": ["2019-01-31"],
        "test_dates": ["2019-02-01"],
    }
    assert manifest["partition_keys"] == ["partition", "trading_date", "symbol"]
    assert manifest["sort_keys"] == ["message_index"]
    assert manifest["labels"] == {
        "primary_horizon": 100,
        "horizons": [20, 100, 500],
        "dtype": "int8",
        "classes": [
            {"name": "down", "value": -1},
            {"name": "flat", "value": 0},
            {"name": "up", "value": 1},
        ],
        "tail_policy": "exclude_unavailable_primary_retain_nullable_secondary",
    }
    assert len(manifest["feature_catalogue"]["features"]) > 20
    assert str(tmp_path) not in result.manifest_path.read_text(encoding="utf-8")

    expected_partitions = {
        "2019-01-30": "train",
        "2019-01-31": "validation",
        "2019-02-01": "test",
    }
    rows_by_date: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest["artefacts"]:
        path = result.manifest_path.parent / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        parquet_file = pq.ParquetFile(path)
        assert parquet_file.metadata.num_rows == 3
        assert parquet_file.metadata.num_row_groups == 1
        assert "partition" not in parquet_file.schema_arrow.names
        assert "trading_date" not in parquet_file.schema_arrow.names
        assert "symbol" not in parquet_file.schema_arrow.names
        trading_date = entry["trading_date"]
        assert entry["partition"] == expected_partitions[trading_date]
        rows_by_date[trading_date] = parquet_file.read().to_pylist()

    for rows in rows_by_date.values():
        assert [row["message_index"] for row in rows] == [5010, 5020, 5030]
        assert [row["qualifying_ordinal"] for row in rows] == [500, 501, 502]
        assert [row["label_horizon_100"] for row in rows] == [-1, 0, 1]
        assert all(row["history_complete"] for row in rows)

    for entry in manifest["counts"]["by_day_symbol"]:
        assert entry["rows"] == expected["per_day"]["rows"]
        assert entry["classes"] == expected["per_day"]["classes"]
    for entry in manifest["supporting_artefacts"]:
        path = result.manifest_path.parent / entry["path"]
        assert path.stat().st_size == entry["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_task_019_verified_identity_is_reused_and_force_never_overwrites(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)

    first = build_dataset(config, base_directory=tmp_path)
    reused = build_dataset(config, base_directory=tmp_path)
    forced = build_dataset(config, base_directory=tmp_path, force_new_run=True)

    assert reused.reused is True
    assert reused.dataset_id == first.dataset_id
    assert forced.reused is False
    assert forced.dataset_id != first.dataset_id
    assert (
        _read_manifest(forced.manifest_path)["identity_sha256"]
        == _read_manifest(first.manifest_path)["identity_sha256"]
    )
    assert first.manifest_path.is_file()


def test_task_019_reuse_rejects_manifest_and_parquet_tampering(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)
    first = build_dataset(config, base_directory=tmp_path)
    original_manifest = first.manifest_path.read_bytes()
    document = _read_manifest(first.manifest_path)
    document["feature_catalogue"]["features"][0]["formula"] += " tampered"
    first.manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(DatasetBuildError) as manifest_failure:
        build_dataset(config, base_directory=tmp_path)
    assert manifest_failure.value.code is ErrorCode.HASH_MISMATCH

    first.manifest_path.write_bytes(original_manifest)
    document = _read_manifest(first.manifest_path)
    parquet_path = first.manifest_path.parent / document["artefacts"][0]["path"]
    parquet_path.write_bytes(parquet_path.read_bytes() + b"tampered")

    with pytest.raises(DatasetBuildError) as parquet_failure:
        build_dataset(config, base_directory=tmp_path)
    assert parquet_failure.value.code is ErrorCode.HASH_MISMATCH


def test_task_019_parent_hash_tampering_is_rejected_before_output(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    parent_document = _read_manifest(parent)
    input_path = parent.parent / parent_document["artefacts"][0]["path"]
    input_path.write_bytes(input_path.read_bytes() + b"tampered")

    with pytest.raises(DatasetBuildError) as captured:
        build_dataset(_config(tmp_path, parent), base_directory=tmp_path)

    assert captured.value.code is ErrorCode.HASH_MISMATCH
    assert not (tmp_path / "runs" / "dataset").exists()


def test_task_019_cancellation_leaves_only_a_partial_run(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)
    cancelled = threading.Event()

    with pytest.raises(DatasetBuildError) as cancellation:
        build_dataset(
            config,
            base_directory=tmp_path,
            cancel_requested=cancelled.is_set,
            progress=lambda progress: (
                cancelled.set() if progress.partitions_completed == 1 else None
            ),
        )
    assert cancellation.value.code is ErrorCode.CANCELLED
    assert cancellation.value.partial_exists is True
    dataset_root = tmp_path / "runs" / "dataset"
    assert list(dataset_root.glob("*.partial"))
    assert not list(dataset_root.glob("*/dataset-manifest.json"))


def test_task_019_write_failure_leaves_only_a_partial_run(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)

    def fail_write(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("synthetic write failure")

    monkeypatch.setattr(service_module, "_write_partition", fail_write)
    with pytest.raises(DatasetBuildError) as write_failure:
        build_dataset(config, base_directory=tmp_path)
    assert write_failure.value.code is ErrorCode.DISK_WRITE
    assert write_failure.value.partial_exists is True
    dataset_root = tmp_path / "runs" / "dataset"
    assert list(dataset_root.glob("*.partial"))
    assert not list(dataset_root.glob("*/dataset-manifest.json"))


def test_task_019_service_rejects_unsafe_locator_and_empty_partitions(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)

    with pytest.raises(DatasetBuildError) as unsafe:
        build_dataset(
            replace(config, conversion_manifests=(str(parent.resolve()),)),
            base_directory=tmp_path,
        )
    assert unsafe.value.code is ErrorCode.INPUT_PATH

    with pytest.raises(DatasetBuildError) as empty:
        build_dataset(
            replace(
                config,
                partitions=PartitionConfig(
                    train_dates=("2019-01-30",),
                    validation_dates=("2019-01-31",),
                    test_dates=("2019-02-02",),
                ),
            ),
            base_directory=tmp_path,
        )
    assert empty.value.code is ErrorCode.PARTITION


def test_task_019_manifest_schema_is_packaged_and_strict() -> None:
    root_schema = REPOSITORY_ROOT / "schemas" / "dataset-manifest.schema.json"
    packaged_schema = (
        REPOSITORY_ROOT
        / "python"
        / "src"
        / "itchlab_research"
        / "_schemas"
        / "dataset-manifest.schema.json"
    )
    assert root_schema.read_bytes() == packaged_schema.read_bytes()
    schema = json.loads(root_schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["config"]["$ref"].endswith("dataset-config.schema.json")


def test_task_019_stride_uses_original_qualifying_ordinal(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory(rows_per_day=606)
    result = build_dataset(_config(tmp_path, parent, row_stride=2), base_directory=tmp_path)
    document = _read_manifest(result.manifest_path)

    assert result.retained_rows == 9
    assert document["counts"]["rows"] == {
        "qualifying_rows": 1818,
        "dropped_incomplete_history": 1500,
        "dropped_unavailable_primary_label": 300,
        "dropped_by_row_stride": 9,
        "retained_rows": 9,
    }
    for entry in document["artefacts"]:
        rows = pq.read_table(result.manifest_path.parent / entry["path"]).to_pylist()
        assert [row["qualifying_ordinal"] for row in rows] == [500, 502, 504]


def test_task_019_config_document_remains_manifest_relative(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    parent = dataset_conversion_factory()
    config = _config(tmp_path, parent)
    document = config_document(config)

    assert document["conversion_manifests"] == [parent.relative_to(tmp_path).as_posix()]
    assert str(tmp_path) not in json.dumps(document)
