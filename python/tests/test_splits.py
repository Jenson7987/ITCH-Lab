"""TASK-019 whole-day partition and feature/label join property tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pyarrow as pa
import pytest

from itchlab_research.config import (
    FeatureConfig,
    LabelConfig,
    PartitionConfig,
    SamplingConfig,
)
from itchlab_research.datasets import (
    DatasetBuildError,
    PartitionJoinCounts,
    dataset_schema,
    feature_schema,
    join_feature_label_batches,
    label_schema,
    partition_mapping,
)
from itchlab_research.errors import ErrorCode

FEATURES = FeatureConfig((1, 5, 10), (20, 100, 500), (100_000_000, 1_000_000_000))
LABELS = LabelConfig(2, (1, 3), 0)
PARTITIONS = PartitionConfig(("2019-01-30",), ("2019-01-31",), ("2019-02-01",))
TRADING_DATE = date(2019, 1, 30)


def _feature_row(ordinal: int, *, history_complete: bool = True) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for field in feature_schema(FEATURES):
        if field.name == "trading_date":
            row[field.name] = TRADING_DATE
        elif field.name == "symbol":
            row[field.name] = "AAPL"
        elif field.name == "symbol_id":
            row[field.name] = 1
        elif field.name == "message_index":
            row[field.name] = ordinal * 10 + 10
        elif field.name == "timestamp_ns":
            row[field.name] = ordinal + 1
        elif field.name == "qualifying_ordinal":
            row[field.name] = ordinal
        elif field.name == "history_complete":
            row[field.name] = history_complete
        elif not field.nullable:
            row[field.name] = 0.0
        else:
            row[field.name] = None
    return row


def _label_row(ordinal: int, primary: int | None) -> dict[str, Any]:
    return {
        "trading_date": TRADING_DATE,
        "symbol": "AAPL",
        "symbol_id": 1,
        "message_index": ordinal * 10 + 10,
        "timestamp_ns": ordinal + 1,
        "qualifying_ordinal": ordinal,
        "label_horizon_1": 0,
        "label_horizon_2": primary,
        "label_horizon_3": None,
    }


def _join(
    features: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> tuple[pa.Table, PartitionJoinCounts]:
    feature_batch = pa.RecordBatch.from_pylist(features, schema=feature_schema(FEATURES))
    label_batch = pa.RecordBatch.from_pylist(labels, schema=label_schema(LABELS))
    counts = PartitionJoinCounts()
    batches = list(
        join_feature_label_batches(
            [feature_batch],
            [label_batch],
            FEATURES,
            LABELS,
            SamplingConfig(2),
            PARTITIONS,
            TRADING_DATE,
            counts,
        )
    )
    return pa.Table.from_batches(batches, schema=dataset_schema(FEATURES, LABELS)), counts


def test_task_019_join_filters_in_documented_order_and_samples_original_ordinal() -> None:
    features = [
        _feature_row(0, history_complete=False),
        _feature_row(1),
        _feature_row(2),
        _feature_row(3),
        _feature_row(4),
    ]
    labels = [
        _label_row(0, -1),
        _label_row(1, None),
        _label_row(2, 1),
        _label_row(3, -1),
        _label_row(4, 0),
    ]

    table, counts = _join(features, labels)

    assert [row["qualifying_ordinal"] for row in table.to_pylist()] == [2, 4]
    assert set(table.column("partition").to_pylist()) == {"train"}
    assert counts.qualifying_rows == 5
    assert counts.dropped_incomplete_history == 1
    assert counts.dropped_unavailable_primary_label == 1
    assert counts.dropped_by_row_stride == 1
    assert counts.retained_rows == 2
    assert counts.class_counts == {"down": 0, "flat": 1, "up": 1}
    assert counts.label_available[2] == 4
    assert counts.label_unavailable[3] == 5


def test_task_019_join_rejects_missing_or_mismatched_immutable_keys() -> None:
    features = [_feature_row(0), _feature_row(1)]
    labels = [_label_row(0, 0)]
    with pytest.raises(DatasetBuildError) as captured:
        _join(features, labels)
    assert captured.value.code is ErrorCode.LEAKAGE_GUARD

    labels.append(_label_row(1, 1))
    labels[1]["timestamp_ns"] = 999
    with pytest.raises(DatasetBuildError) as captured:
        _join(features, labels)
    assert captured.value.code is ErrorCode.LEAKAGE_GUARD


@pytest.mark.parametrize(
    "partitions",
    [
        PartitionConfig(("2019-01-31", "2019-01-30"), ("2019-02-01",), ("2019-02-02",)),
        PartitionConfig(("2019-01-30",), ("2019-01-30",), ("2019-02-02",)),
        PartitionConfig(("2019-02-01",), ("2019-01-31",), ("2019-02-02",)),
        PartitionConfig((), ("2019-01-31",), ("2019-02-02",)),
    ],
)
def test_task_019_partition_properties_reject_unsorted_overlap_and_nonchronology(
    partitions: PartitionConfig,
) -> None:
    with pytest.raises(DatasetBuildError) as captured:
        partition_mapping(partitions)
    assert captured.value.code is ErrorCode.PARTITION


def test_task_019_partition_mapping_assigns_each_whole_day_exactly_once() -> None:
    expanded = replace(
        PARTITIONS,
        train_dates=("2019-01-28", "2019-01-29", "2019-01-30"),
        validation_dates=("2019-01-31", "2019-02-01"),
        test_dates=("2019-02-04", "2019-02-05"),
    )
    mapping = partition_mapping(expanded)

    assert len(mapping) == 7
    assert set(mapping.values()) == {"train", "validation", "test"}
    assert mapping[date(2019, 1, 30)] == "train"
    assert mapping[date(2019, 2, 1)] == "validation"
    assert mapping[date(2019, 2, 5)] == "test"
