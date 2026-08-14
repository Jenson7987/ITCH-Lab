"""Deterministic simulated-order lifecycle and latency scheduling primitives."""

from itchlab_research.errors import SimulationError
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
from itchlab_research.simulation.scheduler import (
    LatencyScheduler,
    ScheduledAction,
    ScheduledActionKind,
)
from itchlab_research.simulation.state_machine import (
    OrderStateMachine,
    OrderTransition,
    TransitionCause,
)

__all__ = [
    "MAX_LATENCY_NS",
    "MAX_TIMESTAMP_NS",
    "LatencyScheduler",
    "OrderRequest",
    "OrderState",
    "OrderStateMachine",
    "OrderTransition",
    "RejectionReason",
    "ScheduledAction",
    "ScheduledActionKind",
    "SimulatedOrder",
    "SimulationError",
    "TERMINAL_STATES",
    "TransitionCause",
    "validate_order_request",
    "validate_simulated_order",
]
