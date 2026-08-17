"""Immutable orchestration models for conservative historical simulation runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from itchlab_research.strategies import SignalPrediction

StrategyName = Literal[
    "inventory_aware_avellaneda_stoikov",
    "signal_adjusted_avellaneda_stoikov",
]

REQUIRED_LATENCIES_NS = (0, 100_000, 1_000_000)
REQUIRED_MAKER_FEES_MICROUSD_PER_SHARE = (-2_000, 3_000)
REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE = 3_000


@dataclass(frozen=True, slots=True)
class ExecutionScenario:
    """One symmetric latency and signed maker-cost sensitivity cell."""

    scenario_id: str
    submission_latency_ns: int
    cancellation_latency_ns: int
    maker_fee_microusd_per_share: int
    taker_fee_microusd_per_share: int


@dataclass(frozen=True, slots=True)
class SimulationSymbol:
    """Stable symbol identity and Price4 tick size for one input day."""

    symbol: str
    symbol_id: int
    tick_size4: int


@dataclass(frozen=True, slots=True)
class SimulationDayInput:
    """Authenticated, source-ordered market inputs for one replay session."""

    trading_date: date
    session_start_ns: int
    session_end_ns: int
    symbols: tuple[SimulationSymbol, ...]
    events: tuple[Mapping[str, object], ...]
    snapshots: tuple[Mapping[str, object], ...]
    predictions: tuple[SignalPrediction, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """In-memory rows and summaries produced by one complete scenario."""

    scenario: ExecutionScenario
    strategy_name: StrategyName
    signal_weight_ticks: float
    orders: tuple[dict[str, Any], ...]
    fills: tuple[dict[str, Any], ...]
    liquidations: tuple[dict[str, Any], ...]
    equity: tuple[dict[str, Any], ...]
    daily_metrics: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """A completed or safely reused immutable simulation publication."""

    simulation_id: str
    status: Literal["completed"]
    manifest_path: Path
    experiment_id: str | None
    scenario_count: int
    strategy_count: int
    order_rows: int
    fill_rows: int
    warnings: tuple[str, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class AuthenticatedSimulation:
    """Hash-authenticated simulation evidence for report generation."""

    simulation_id: str
    manifest_path: Path
    manifest_sha256: str
    manifest: dict[str, Any]
    metrics: dict[str, Any]
    diagnostics: dict[str, Any]


def required_scenarios() -> tuple[ExecutionScenario, ...]:
    """Return the fixed version-1 3-by-2 test sensitivity grid."""
    return tuple(
        ExecutionScenario(
            scenario_id=f"latency-{latency}-maker-{maker_fee}",
            submission_latency_ns=latency,
            cancellation_latency_ns=latency,
            maker_fee_microusd_per_share=maker_fee,
            taker_fee_microusd_per_share=REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE,
        )
        for latency in REQUIRED_LATENCIES_NS
        for maker_fee in REQUIRED_MAKER_FEES_MICROUSD_PER_SHARE
    )


__all__ = [
    "AuthenticatedSimulation",
    "ExecutionScenario",
    "REQUIRED_LATENCIES_NS",
    "REQUIRED_MAKER_FEES_MICROUSD_PER_SHARE",
    "REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE",
    "ScenarioResult",
    "SimulationDayInput",
    "SimulationResult",
    "SimulationSymbol",
    "StrategyName",
    "required_scenarios",
]
