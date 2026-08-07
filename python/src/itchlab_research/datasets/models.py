"""Immutable public models for causal feature construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FeaturePartitionContext:
    """Authenticated identity, price grid and session bounds for one input partition."""

    trading_date: date
    symbol: str
    symbol_id: int
    tick_size4: int
    session_start_ns: int
    session_end_ns: int


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """One deterministic version-1 feature-catalogue entry."""

    name: str
    dtype: str
    nullable: bool
    formula: str
    lookback_kind: str
    lookback_value: int | None
    unit: str
    null_policy: str
    owner: str


@dataclass(frozen=True, slots=True)
class DatasetProgress:
    """One bounded progress observation emitted between complete dataset batches."""

    stage: str
    partitions_completed: int
    rows_processed: int
    parquet_files: int
    output_bytes: int


@dataclass(frozen=True, slots=True)
class DatasetResult:
    """A completed or safely reused immutable dataset run."""

    dataset_id: str
    status: str
    manifest_path: Path
    retained_rows: int
    parquet_files: int
    parent_conversion_ids: tuple[str, ...]
    partition_rows: tuple[tuple[str, int], ...]
    class_counts: tuple[tuple[str, int], ...]
    reused: bool


__all__ = [
    "DatasetProgress",
    "DatasetResult",
    "FeatureDefinition",
    "FeaturePartitionContext",
]
