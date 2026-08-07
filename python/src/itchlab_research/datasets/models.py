"""Immutable public models for causal feature construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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


__all__ = ["FeatureDefinition", "FeaturePartitionContext"]
