"""Whole-day partition assignment and exact feature/label leakage guards."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final, Literal, cast

import pyarrow as pa

from itchlab_research.config import (
    FeatureConfig,
    LabelConfig,
    PartitionConfig,
    SamplingConfig,
)
from itchlab_research.datasets.features import feature_schema
from itchlab_research.datasets.labels import label_column, label_horizons, label_schema
from itchlab_research.errors import DatasetBuildError, ErrorCode

PartitionName = Literal["train", "validation", "test"]

_OUTPUT_BATCH_ROWS: Final = 65_536
_MAX_INPUT_BATCH_ROWS: Final = 1_048_576
_CLASS_NAME: Final = {-1: "down", 0: "flat", 1: "up"}


@dataclass(slots=True)
class PartitionJoinCounts:
    """Disjoint filtering and label counts for one complete day/symbol partition."""

    qualifying_rows: int = 0
    dropped_incomplete_history: int = 0
    dropped_unavailable_primary_label: int = 0
    dropped_by_row_stride: int = 0
    retained_rows: int = 0
    class_counts: dict[str, int] = field(default_factory=lambda: {"down": 0, "flat": 0, "up": 0})
    label_available: dict[int, int] = field(default_factory=dict)
    label_unavailable: dict[int, int] = field(default_factory=dict)


def _fail(code: ErrorCode, message: str) -> DatasetBuildError:
    return DatasetBuildError(code, message)


def partition_mapping(config: PartitionConfig) -> dict[date, PartitionName]:
    """Validate and map each complete configured date to exactly one frozen split."""
    if not isinstance(config, PartitionConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Partition config has the wrong domain type.")
    groups: tuple[tuple[PartitionName, tuple[str, ...]], ...] = (
        ("train", config.train_dates),
        ("validation", config.validation_dates),
        ("test", config.test_dates),
    )
    parsed: dict[PartitionName, tuple[date, ...]] = {}
    try:
        for name, values in groups:
            if not values or any(not isinstance(value, str) for value in values):
                raise ValueError
            dates = tuple(date.fromisoformat(value) for value in values)
            if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
                raise ValueError
            parsed[name] = dates
    except ValueError as error:
        raise _fail(
            ErrorCode.PARTITION, "Partition dates must be non-empty, valid, sorted and unique."
        ) from error
    all_dates = [item for name in ("train", "validation", "test") for item in parsed[name]]
    if len(set(all_dates)) != len(all_dates):
        raise _fail(ErrorCode.PARTITION, "A trading day appears in more than one partition.")
    if not (
        max(parsed["train"]) < min(parsed["validation"])
        and max(parsed["validation"]) < min(parsed["test"])
    ):
        raise _fail(ErrorCode.PARTITION, "Train, validation and test days must be chronological.")
    return {
        trading_date: name
        for name in ("train", "validation", "test")
        for trading_date in parsed[name]
    }


def dataset_schema(feature_config: FeatureConfig, label_config: LabelConfig) -> pa.Schema:
    """Return the exact joined, frozen dataset Arrow schema."""
    fields: list[pa.Field[Any]] = [pa.field("partition", pa.string(), nullable=False)]
    fields.extend(feature_schema(feature_config))
    primary_name = label_column(label_config.primary_event_horizon)
    for horizon in label_horizons(label_config):
        name = label_column(horizon)
        fields.append(pa.field(name, pa.int8(), nullable=name != primary_name))
    return pa.schema(fields)


def _rows(
    batches: Iterable[pa.RecordBatch], expected_schema: pa.Schema, *, kind: str
) -> Iterator[dict[str, Any]]:
    for batch in batches:
        if (
            not isinstance(batch, pa.RecordBatch)
            or batch.num_rows > _MAX_INPUT_BATCH_ROWS
            or not batch.schema.equals(expected_schema, check_metadata=False)
        ):
            raise _fail(ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} join batch is invalid.")
        yield from cast(list[dict[str, Any]], batch.to_pylist())


def _next_or_none(rows: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(rows)
    except StopIteration:
        return None


def _record_batch(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    try:
        return pa.RecordBatch.from_pylist(list(rows), schema=schema)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.INVARIANT, "Joined dataset rows do not match schema version 1."
        ) from error


def join_feature_label_batches(
    feature_batches: Iterable[pa.RecordBatch],
    label_batches: Iterable[pa.RecordBatch],
    feature_config: FeatureConfig,
    label_config: LabelConfig,
    sampling: SamplingConfig,
    partitions: PartitionConfig,
    expected_date: date,
    counts: PartitionJoinCounts,
) -> Iterator[pa.RecordBatch]:
    """Join, filter, sample and partition one complete day/symbol without row leakage."""
    mapping = partition_mapping(partitions)
    if expected_date not in mapping:
        raise _fail(ErrorCode.PARTITION, "Input day is not assigned to a frozen partition.")
    if (
        not isinstance(sampling, SamplingConfig)
        or isinstance(sampling.row_stride, bool)
        or not isinstance(sampling.row_stride, int)
        or sampling.row_stride <= 0
    ):
        raise _fail(ErrorCode.ROW_STRIDE, "Dataset row stride must be a positive integer.")
    horizons = label_horizons(label_config)
    counts.label_available = {horizon: 0 for horizon in horizons}
    counts.label_unavailable = {horizon: 0 for horizon in horizons}
    features = _rows(feature_batches, feature_schema(feature_config), kind="feature")
    labels = _rows(label_batches, label_schema(label_config), kind="label")
    output_schema = dataset_schema(feature_config, label_config)
    output: list[dict[str, Any]] = []
    partition = mapping[expected_date]
    primary_name = label_column(label_config.primary_event_horizon)

    while True:
        feature = _next_or_none(features)
        label = _next_or_none(labels)
        if feature is None and label is None:
            break
        if feature is None or label is None:
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Feature and label streams do not contain the same immutable row keys.",
            )
        key = (feature["trading_date"], feature["symbol"], feature["message_index"])
        label_key = (label["trading_date"], label["symbol"], label["message_index"])
        if key != label_key:
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Feature and label immutable row keys do not match exactly.",
            )
        if feature["trading_date"] != expected_date:
            raise _fail(ErrorCode.PARTITION, "A feature row crossed its complete trading day.")
        for name in ("symbol_id", "timestamp_ns", "qualifying_ordinal"):
            if feature[name] != label[name]:
                raise _fail(
                    ErrorCode.LEAKAGE_GUARD,
                    f"Feature and label {name} metadata disagree for an immutable row key.",
                )

        counts.qualifying_rows += 1
        for horizon in horizons:
            if label[label_column(horizon)] is None:
                counts.label_unavailable[horizon] += 1
            else:
                counts.label_available[horizon] += 1
        if not feature["history_complete"]:
            counts.dropped_incomplete_history += 1
            continue
        primary_label = label[primary_name]
        if primary_label is None:
            counts.dropped_unavailable_primary_label += 1
            continue
        ordinal = feature["qualifying_ordinal"]
        if not isinstance(ordinal, int) or ordinal % sampling.row_stride:
            counts.dropped_by_row_stride += 1
            continue
        if primary_label not in _CLASS_NAME:
            raise _fail(ErrorCode.INVARIANT, "Primary label is outside the three-class domain.")

        row = {"partition": partition, **feature}
        for horizon in horizons:
            row[label_column(horizon)] = label[label_column(horizon)]
        counts.retained_rows += 1
        counts.class_counts[_CLASS_NAME[cast(int, primary_label)]] += 1
        output.append(row)
        if len(output) == _OUTPUT_BATCH_ROWS:
            yield _record_batch(output, output_schema)
            output.clear()

    if counts.qualifying_rows != (
        counts.dropped_incomplete_history
        + counts.dropped_unavailable_primary_label
        + counts.dropped_by_row_stride
        + counts.retained_rows
    ):
        raise _fail(ErrorCode.INVARIANT, "Dataset row filtering counts do not reconcile.")
    if output:
        yield _record_batch(output, output_schema)


__all__ = [
    "PartitionJoinCounts",
    "PartitionName",
    "dataset_schema",
    "join_feature_label_batches",
    "partition_mapping",
]
