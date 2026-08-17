"""Deterministic simulated-order lifecycle and latency scheduling primitives."""

from collections.abc import Callable
from pathlib import Path

from itchlab_research.config import SimulationConfig
from itchlab_research.errors import SimulationError
from itchlab_research.simulation.accounting import (
    MAX_FEE_MICROUSD_PER_SHARE,
    MAX_INT64,
    MAX_MID2,
    MIN_INT64,
    AccountedFill,
    AccountingLedger,
    AccountingSnapshot,
    LiquidationAccounting,
    SymbolAccounting,
)
from itchlab_research.simulation.liquidation import (
    TerminalQuote,
    TerminalSettlement,
    settle_session_end,
)
from itchlab_research.simulation.market_events import (
    MarketEvent,
    adapt_market_event,
    validate_market_event,
)
from itchlab_research.simulation.metrics import (
    AccountingMetrics,
    TemporalMetrics,
    accounting_metrics,
    temporal_metrics,
)
from itchlab_research.simulation.models import (
    AuthenticatedSimulation,
    ExecutionScenario,
    ScenarioResult,
    SimulationDayInput,
    SimulationResult,
    SimulationSymbol,
    required_scenarios,
)
from itchlab_research.simulation.order import (
    MAX_LATENCY_NS,
    MAX_TIMESTAMP_NS,
    TERMINAL_STATES,
    OrderRequest,
    OrderState,
    RejectionReason,
    SimulatedOrder,
    validate_order_request,
    validate_simulated_order,
)
from itchlab_research.simulation.queue_model import (
    QueueAnomalyReason,
    QueueDiagnostic,
    QueueDiagnosticCode,
    QueueEventResult,
    QueueFill,
    QueueSnapshot,
    VisibleQueueModel,
)
from itchlab_research.simulation.risk_limits import (
    InventoryRiskDecision,
    InventoryRiskLimit,
    RiskDecisionReason,
)
from itchlab_research.simulation.scheduler import (
    LatencyScheduler,
    ScheduledAction,
    ScheduledActionKind,
)
from itchlab_research.simulation.state_machine import (
    ActivationQueueResolver,
    OrderStateMachine,
    OrderTransition,
    TransitionCause,
)


def simulate(
    config: SimulationConfig,
    *,
    base_directory: Path | None = None,
    force_new_run: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
) -> SimulationResult:
    """Lazily invoke the immutable simulation service without strategy import cycles."""
    from itchlab_research.simulation.service import simulate as run

    return run(
        config,
        base_directory=base_directory,
        force_new_run=force_new_run,
        cancel_requested=cancel_requested,
    )


def load_completed_simulation(
    simulation_id: str,
    *,
    base_directory: Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> AuthenticatedSimulation:
    """Lazily authenticate one completed simulation publication."""
    from itchlab_research.simulation.service import load_completed_simulation as load

    return load(
        simulation_id,
        base_directory=base_directory,
        cancel_requested=cancel_requested,
    )


__all__ = [
    "MAX_FEE_MICROUSD_PER_SHARE",
    "MAX_INT64",
    "ActivationQueueResolver",
    "AccountedFill",
    "AccountingLedger",
    "AccountingMetrics",
    "AccountingSnapshot",
    "AuthenticatedSimulation",
    "ExecutionScenario",
    "TemporalMetrics",
    "MAX_LATENCY_NS",
    "MAX_MID2",
    "MAX_TIMESTAMP_NS",
    "MIN_INT64",
    "InventoryRiskDecision",
    "InventoryRiskLimit",
    "LatencyScheduler",
    "MarketEvent",
    "OrderRequest",
    "OrderState",
    "OrderStateMachine",
    "OrderTransition",
    "ScenarioResult",
    "SimulationDayInput",
    "SimulationResult",
    "SimulationSymbol",
    "QueueAnomalyReason",
    "QueueDiagnostic",
    "QueueDiagnosticCode",
    "QueueEventResult",
    "QueueFill",
    "QueueSnapshot",
    "RejectionReason",
    "RiskDecisionReason",
    "ScheduledAction",
    "ScheduledActionKind",
    "SimulatedOrder",
    "SimulationError",
    "SymbolAccounting",
    "TERMINAL_STATES",
    "TerminalQuote",
    "TerminalSettlement",
    "LiquidationAccounting",
    "TransitionCause",
    "VisibleQueueModel",
    "adapt_market_event",
    "accounting_metrics",
    "temporal_metrics",
    "settle_session_end",
    "simulate",
    "load_completed_simulation",
    "required_scenarios",
    "validate_market_event",
    "validate_order_request",
    "validate_simulated_order",
]
