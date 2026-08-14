"""Deterministic simulated-order lifecycle and latency scheduling primitives."""

from itchlab_research.errors import SimulationError
from itchlab_research.simulation.market_events import (
    MarketEvent,
    adapt_market_event,
    validate_market_event,
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
    "ActivationQueueResolver",
    "MAX_LATENCY_NS",
    "MAX_TIMESTAMP_NS",
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
    "ScheduledAction",
    "ScheduledActionKind",
    "SimulatedOrder",
    "SimulationError",
    "TERMINAL_STATES",
    "TransitionCause",
    "VisibleQueueModel",
    "adapt_market_event",
    "validate_market_event",
    "validate_order_request",
    "validate_simulated_order",
]
