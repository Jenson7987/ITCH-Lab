"""Immutable public models for replay-to-Parquet conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConversionProgress:
    """One bounded progress observation emitted between complete reader batches."""

    stage: str
    records_read: int
    parquet_files: int
    output_bytes: int


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """A completed or safely reused immutable conversion run."""

    conversion_id: str
    status: str
    manifest_path: Path
    event_rows: int
    snapshot_rows: int
    parquet_files: int
    parent_replay_ids: tuple[str, ...]
    partitions: int
    reused: bool


__all__ = ["ConversionProgress", "ConversionResult"]
