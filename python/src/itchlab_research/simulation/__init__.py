"""Deterministic simulated-order lifecycle and latency scheduling primitives."""

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
from itchlab_research.simulation.metrics import AccountingMetrics, accounting_metrics
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

__all__ = [
    "MAX_FEE_MICROUSD_PER_SHARE",
    "MAX_INT64",
    "ActivationQueueResolver",
    "AccountedFill",
    "AccountingLedger",
    "AccountingMetrics",
    "AccountingSnapshot",
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
    "settle_session_end",
    "validate_market_event",
    "validate_order_request",
    "validate_simulated_order",
]
