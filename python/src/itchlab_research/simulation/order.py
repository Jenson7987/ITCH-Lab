"""Immutable simulated-order domain values and version-1 invariants."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from itchlab_research.errors import ErrorCode, SimulationError

MAX_UINT16: Final = (1 << 16) - 1
MAX_UINT32: Final = (1 << 32) - 1
MAX_UINT64: Final = (1 << 64) - 1
MAX_TIMESTAMP_NS: Final = 86_400_000_000_000 - 1
MAX_LATENCY_NS: Final = 10_000_000_000


class OrderState(StrEnum):
    """Canonical version-1 simulated-order states."""

    PENDING_SUBMIT = "pending_submit"
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    PENDING_CANCEL = "pending_cancel"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class RejectionReason(StrEnum):
    """Terminal reasons that distinguish rejection from historical invalidation."""

    MARKETABLE_AT_ACTIVATION = "marketable_at_activation"
    COUNTERFACTUAL_CROSS = "counterfactual_cross"


TERMINAL_STATES: Final[frozenset[OrderState]] = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
        OrderState.REJECTED,
        OrderState.INVALIDATED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """One causal strategy request before submission latency is applied."""

    simulated_order_id: int
    decision_message_index: int
    prediction_message_index: int | None
    requested_timestamp_ns: int
    symbol_id: int
    side: int
    price4: int
    quantity: int


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """One immutable simulated passive order at a lifecycle point."""

    simulated_order_id: int
    decision_message_index: int
    prediction_message_index: int | None
    requested_timestamp_ns: int
    effective_timestamp_ns: int
    symbol_id: int
    side: int
    price4: int
    original_quantity: int
    remaining_quantity: int
    queue_ahead_initial: int | None
    state: OrderState
    cancel_requested_ns: int | None
    terminal_timestamp_ns: int | None
    rejection_reason: RejectionReason | None

    @property
    def terminal(self) -> bool:
        """Return whether no later lifecycle transition may change this order."""
        return self.state in TERMINAL_STATES


def _fail(
    code: ErrorCode,
    message: str,
    *,
    simulated_order_id: int | None = None,
    message_index: int | None = None,
) -> SimulationError:
    return SimulationError(
        code,
        message,
        simulated_order_id=simulated_order_id,
        message_index=message_index,
    )


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def validate_order_request(request: OrderRequest) -> None:
    """Validate an untrusted order request before scheduler or state mutation."""
    if not isinstance(request, OrderRequest):
        raise _fail(ErrorCode.SIMULATION_ANOMALY, "Order request has the wrong domain type.")
    order_id = request.simulated_order_id
    if not _valid_int(order_id, minimum=0, maximum=MAX_UINT64):
        raise _fail(ErrorCode.SIMULATION_ANOMALY, "Simulated order ID is invalid.")
    if not _valid_int(request.decision_message_index, minimum=0, maximum=MAX_UINT64):
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Decision message index is invalid.",
            simulated_order_id=order_id,
        )
    if request.prediction_message_index is not None and (
        not _valid_int(request.prediction_message_index, minimum=0, maximum=MAX_UINT64)
        or request.prediction_message_index > request.decision_message_index
    ):
        raise _fail(
            ErrorCode.PREDICTION_KEY,
            "Prediction message index must not follow the decision.",
            simulated_order_id=order_id,
        )
    if not _valid_int(request.requested_timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
        raise _fail(
            ErrorCode.TIMESTAMP,
            "Order request timestamp is outside the exchange day.",
            simulated_order_id=order_id,
            message_index=request.decision_message_index,
        )
    if not _valid_int(request.symbol_id, minimum=1, maximum=MAX_UINT16):
        raise _fail(
            ErrorCode.UNKNOWN_SYMBOL,
            "Simulated order symbol ID is invalid.",
            simulated_order_id=order_id,
        )
    if request.side not in (-1, 1) or isinstance(request.side, bool):
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Simulated order side must be +1 or -1.",
            simulated_order_id=order_id,
        )
    if not _valid_int(request.price4, minimum=0, maximum=MAX_UINT32):
        raise _fail(
            ErrorCode.PRICE,
            "Simulated order Price4 is invalid.",
            simulated_order_id=order_id,
        )
    if not _valid_int(request.quantity, minimum=1, maximum=MAX_UINT64):
        raise _fail(
            ErrorCode.QUANTITY,
            "Simulated order quantity must be positive.",
            simulated_order_id=order_id,
        )


def validate_simulated_order(order: SimulatedOrder) -> None:
    """Validate one complete immutable lifecycle state."""
    if not isinstance(order, SimulatedOrder):
        raise _fail(ErrorCode.SIMULATION_ANOMALY, "Order has the wrong domain type.")
    request = OrderRequest(
        simulated_order_id=order.simulated_order_id,
        decision_message_index=order.decision_message_index,
        prediction_message_index=order.prediction_message_index,
        requested_timestamp_ns=order.requested_timestamp_ns,
        symbol_id=order.symbol_id,
        side=order.side,
        price4=order.price4,
        quantity=order.original_quantity,
    )
    validate_order_request(request)
    order_id = order.simulated_order_id
    if not _valid_int(
        order.effective_timestamp_ns,
        minimum=order.requested_timestamp_ns,
        maximum=MAX_TIMESTAMP_NS,
    ) or not _valid_int(
        order.remaining_quantity,
        minimum=0,
        maximum=order.original_quantity,
    ):
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Simulated order timestamps or quantities are inconsistent.",
            simulated_order_id=order_id,
        )
    if order.queue_ahead_initial is not None and not _valid_int(
        order.queue_ahead_initial, minimum=0, maximum=MAX_UINT64
    ):
        raise _fail(
            ErrorCode.QUEUE_STATE,
            "Initial queue-ahead quantity is invalid.",
            simulated_order_id=order_id,
        )
    if order.cancel_requested_ns is not None and not _valid_int(
        order.cancel_requested_ns,
        minimum=order.requested_timestamp_ns,
        maximum=MAX_TIMESTAMP_NS,
    ):
        raise _fail(
            ErrorCode.TIMESTAMP,
            "Cancellation request timestamp is invalid.",
            simulated_order_id=order_id,
        )
    if order.terminal_timestamp_ns is not None and not _valid_int(
        order.terminal_timestamp_ns,
        minimum=order.requested_timestamp_ns,
        maximum=MAX_TIMESTAMP_NS,
    ):
        raise _fail(
            ErrorCode.TIMESTAMP,
            "Terminal timestamp is invalid.",
            simulated_order_id=order_id,
        )

    if order.state in TERMINAL_STATES:
        if order.terminal_timestamp_ns is None:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Terminal order is missing its terminal timestamp.",
                simulated_order_id=order_id,
            )
    elif order.terminal_timestamp_ns is not None:
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Non-terminal order has a terminal timestamp.",
            simulated_order_id=order_id,
        )

    if order.state is OrderState.FILLED:
        if order.remaining_quantity != 0:
            raise _fail(
                ErrorCode.QUANTITY,
                "Filled order must have zero remaining quantity.",
                simulated_order_id=order_id,
            )
    elif order.remaining_quantity == 0:
        raise _fail(
            ErrorCode.QUANTITY,
            "Only a filled order may have zero remaining quantity.",
            simulated_order_id=order_id,
        )
    if order.state is OrderState.PARTIALLY_FILLED and not (
        0 < order.remaining_quantity < order.original_quantity
    ):
        raise _fail(
            ErrorCode.QUANTITY,
            "Partially filled order must retain less than its original quantity.",
            simulated_order_id=order_id,
        )
    if order.state is OrderState.PENDING_CANCEL and order.cancel_requested_ns is None:
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Pending-cancel order is missing its cancellation request.",
            simulated_order_id=order_id,
        )
    if order.state in {OrderState.ACTIVE, OrderState.PARTIALLY_FILLED} and (
        order.cancel_requested_ns is not None
    ):
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Exposed order with a cancellation request must be pending cancel.",
            simulated_order_id=order_id,
        )
    expected_reason = {
        OrderState.REJECTED: RejectionReason.MARKETABLE_AT_ACTIVATION,
        OrderState.INVALIDATED: RejectionReason.COUNTERFACTUAL_CROSS,
    }.get(order.state)
    if order.rejection_reason is not expected_reason:
        raise _fail(
            ErrorCode.SIMULATION_ANOMALY,
            "Order rejection reason does not match its state.",
            simulated_order_id=order_id,
        )


__all__ = [
    "MAX_LATENCY_NS",
    "MAX_TIMESTAMP_NS",
    "MAX_UINT16",
    "MAX_UINT32",
    "MAX_UINT64",
    "OrderRequest",
    "OrderState",
    "RejectionReason",
    "SimulatedOrder",
    "TERMINAL_STATES",
    "validate_order_request",
    "validate_simulated_order",
]
