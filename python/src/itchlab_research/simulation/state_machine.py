"""Atomic simulated-order lifecycle transitions over the latency scheduler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import (
    MAX_LATENCY_NS,
    MAX_TIMESTAMP_NS,
    MAX_UINT64,
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


class TransitionCause(StrEnum):
    """Stable reasons emitted by successful lifecycle transitions."""

    SUBMITTED = "submitted"
    ACTIVATED = "activated"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class OrderTransition:
    """One deterministic state change or cancellation-request metadata change."""

    simulated_order_id: int
    before_state: OrderState | None
    after_state: OrderState
    cause: TransitionCause
    timestamp_ns: int
    market_message_index: int | None
    quantity: int | None
    rejection_reason: RejectionReason | None


# A resolver returns the checked exact queue-ahead shares; None requests passive-only rejection.
ActivationQueueResolver: TypeAlias = Callable[[SimulatedOrder], int | None]


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


class OrderStateMachine:
    """Own simulated orders and apply valid transitions in causal market order."""

    def __init__(self, *, submission_latency_ns: int, cancellation_latency_ns: int) -> None:
        LatencyScheduler.effective_timestamp(0, submission_latency_ns)
        LatencyScheduler.effective_timestamp(0, cancellation_latency_ns)
        if submission_latency_ns > MAX_LATENCY_NS or cancellation_latency_ns > MAX_LATENCY_NS:
            raise _fail(ErrorCode.LATENCY, "Configured latency is outside version-1 bounds.")
        self._submission_latency_ns = submission_latency_ns
        self._cancellation_latency_ns = cancellation_latency_ns
        self._scheduler = LatencyScheduler()
        self._orders: dict[int, SimulatedOrder] = {}
        self._occupied_slots: dict[tuple[int, int], int] = {}
        self._scheduled_actions: dict[int, ScheduledAction] = {}

    def order(self, simulated_order_id: int) -> SimulatedOrder:
        """Return one owned order or fail with a stable domain error."""
        if not _valid_int(simulated_order_id, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Simulated order ID is invalid.")
        try:
            return self._orders[simulated_order_id]
        except (KeyError, TypeError) as error:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Simulated order does not exist.",
                simulated_order_id=(
                    simulated_order_id if isinstance(simulated_order_id, int) else None
                ),
            ) from error

    def occupied_order(self, symbol_id: int, side: int) -> SimulatedOrder | None:
        """Return the non-terminal order occupying one symbol-side slot, if any."""
        order_id = self._occupied_slots.get((symbol_id, side))
        if order_id is None:
            return None
        order = self.order(order_id)
        if order.terminal:
            raise _fail(
                ErrorCode.INVARIANT,
                "A terminal order still occupies its symbol-side slot.",
                simulated_order_id=order_id,
            )
        return order

    @property
    def orders(self) -> tuple[SimulatedOrder, ...]:
        """Return every owned order in deterministic identifier order."""
        return tuple(self._orders[order_id] for order_id in sorted(self._orders))

    @property
    def pending_actions(self) -> tuple[ScheduledAction, ...]:
        """Return scheduler actions that have not yet become effective."""
        return self._scheduler.pending_actions

    def before_market_event(
        self,
        timestamp_ns: int,
        message_index: int,
        *,
        activation_queue_resolver: ActivationQueueResolver | None = None,
    ) -> tuple[OrderTransition, ...]:
        """Apply actions strictly earlier than one ordered source market message."""
        actions = self._scheduler.actions_before_market(timestamp_ns, message_index)
        return self._apply_actions(actions, activation_queue_resolver)

    def after_market_timestamp(
        self,
        timestamp_ns: int,
        *,
        activation_queue_resolver: ActivationQueueResolver | None = None,
    ) -> tuple[OrderTransition, ...]:
        """Apply equal-time actions after all source messages at that timestamp."""
        actions = self._scheduler.actions_after_market_timestamp(timestamp_ns)
        return self._apply_actions(actions, activation_queue_resolver)

    def submit(self, request: OrderRequest) -> OrderTransition:
        """Create one pending order and schedule its submission activation."""
        validate_order_request(request)
        self._require_current_market_key(
            request.requested_timestamp_ns,
            request.decision_message_index,
            simulated_order_id=request.simulated_order_id,
        )
        if request.simulated_order_id in self._orders:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Simulated order ID is not unique within the scenario.",
                simulated_order_id=request.simulated_order_id,
            )
        slot = (request.symbol_id, request.side)
        if slot in self._occupied_slots:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "A live or pending order already occupies this symbol and side.",
                simulated_order_id=request.simulated_order_id,
                message_index=request.decision_message_index,
            )
        effective_timestamp_ns = LatencyScheduler.effective_timestamp(
            request.requested_timestamp_ns, self._submission_latency_ns
        )
        order = SimulatedOrder(
            simulated_order_id=request.simulated_order_id,
            decision_message_index=request.decision_message_index,
            prediction_message_index=request.prediction_message_index,
            requested_timestamp_ns=request.requested_timestamp_ns,
            effective_timestamp_ns=effective_timestamp_ns,
            symbol_id=request.symbol_id,
            side=request.side,
            price4=request.price4,
            original_quantity=request.quantity,
            remaining_quantity=request.quantity,
            queue_ahead_initial=None,
            state=OrderState.PENDING_SUBMIT,
            cancel_requested_ns=None,
            terminal_timestamp_ns=None,
            rejection_reason=None,
        )
        validate_simulated_order(order)
        action = self._scheduler.schedule(
            ScheduledActionKind.ACTIVATE,
            simulated_order_id=request.simulated_order_id,
            requested_timestamp_ns=request.requested_timestamp_ns,
            request_message_index=request.decision_message_index,
            latency_ns=self._submission_latency_ns,
        )
        self._orders[request.simulated_order_id] = order
        self._occupied_slots[slot] = request.simulated_order_id
        self._scheduled_actions[action.sequence] = action
        return OrderTransition(
            simulated_order_id=request.simulated_order_id,
            before_state=None,
            after_state=OrderState.PENDING_SUBMIT,
            cause=TransitionCause.SUBMITTED,
            timestamp_ns=request.requested_timestamp_ns,
            market_message_index=request.decision_message_index,
            quantity=None,
            rejection_reason=None,
        )

    def request_cancel(
        self,
        simulated_order_id: int,
        *,
        timestamp_ns: int,
        message_index: int,
    ) -> OrderTransition:
        """Request cancellation without removing exposure before its scheduled effect."""
        order = self.order(simulated_order_id)
        self._require_current_market_key(
            timestamp_ns, message_index, simulated_order_id=simulated_order_id
        )
        if order.state not in {
            OrderState.PENDING_SUBMIT,
            OrderState.ACTIVE,
            OrderState.PARTIALLY_FILLED,
        }:
            raise self._invalid_transition(order, "request cancellation")
        if order.cancel_requested_ns is not None:
            raise self._invalid_transition(order, "request cancellation twice")
        if (
            timestamp_ns < order.requested_timestamp_ns
            or message_index < order.decision_message_index
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Cancellation request precedes the order request.",
                simulated_order_id=simulated_order_id,
                message_index=message_index,
            )
        LatencyScheduler.effective_timestamp(timestamp_ns, self._cancellation_latency_ns)
        next_state = (
            OrderState.PENDING_SUBMIT
            if order.state is OrderState.PENDING_SUBMIT
            else OrderState.PENDING_CANCEL
        )
        updated = replace(
            order,
            state=next_state,
            cancel_requested_ns=timestamp_ns,
        )
        validate_simulated_order(updated)
        action = self._scheduler.schedule(
            ScheduledActionKind.CANCEL,
            simulated_order_id=simulated_order_id,
            requested_timestamp_ns=timestamp_ns,
            request_message_index=message_index,
            latency_ns=self._cancellation_latency_ns,
        )
        self._orders[simulated_order_id] = updated
        self._scheduled_actions[action.sequence] = action
        return OrderTransition(
            simulated_order_id=simulated_order_id,
            before_state=order.state,
            after_state=next_state,
            cause=TransitionCause.CANCEL_REQUESTED,
            timestamp_ns=timestamp_ns,
            market_message_index=message_index,
            quantity=None,
            rejection_reason=None,
        )

    def record_fill(
        self,
        simulated_order_id: int,
        *,
        quantity: int,
        timestamp_ns: int,
        market_message_index: int,
    ) -> OrderTransition:
        """Apply one already-eligible observed fill without performing queue modelling."""
        order = self.order(simulated_order_id)
        self._require_current_market_key(
            timestamp_ns,
            market_message_index,
            simulated_order_id=simulated_order_id,
        )
        if order.state is OrderState.PENDING_SUBMIT:
            raise _fail(
                ErrorCode.LATENCY,
                "Order cannot fill before submission becomes effective.",
                simulated_order_id=simulated_order_id,
                message_index=market_message_index,
            )
        if order.state not in {
            OrderState.ACTIVE,
            OrderState.PARTIALLY_FILLED,
            OrderState.PENDING_CANCEL,
        }:
            raise self._invalid_transition(order, "fill")
        if not _valid_int(quantity, minimum=1, maximum=order.remaining_quantity):
            raise _fail(
                ErrorCode.QUANTITY,
                "Fill quantity must be positive and cannot exceed order remainder.",
                simulated_order_id=simulated_order_id,
                message_index=market_message_index,
            )
        if timestamp_ns < order.effective_timestamp_ns:
            raise _fail(
                ErrorCode.LATENCY,
                "Order cannot fill before its effective timestamp.",
                simulated_order_id=simulated_order_id,
                message_index=market_message_index,
            )
        remaining = order.remaining_quantity - quantity
        if remaining == 0:
            next_state = OrderState.FILLED
            cause = TransitionCause.FILLED
            terminal_timestamp_ns: int | None = timestamp_ns
        else:
            next_state = (
                OrderState.PENDING_CANCEL
                if order.state is OrderState.PENDING_CANCEL
                else OrderState.PARTIALLY_FILLED
            )
            cause = TransitionCause.PARTIAL_FILL
            terminal_timestamp_ns = None
        updated = replace(
            order,
            remaining_quantity=remaining,
            state=next_state,
            terminal_timestamp_ns=terminal_timestamp_ns,
        )
        return self._commit(
            order,
            updated,
            cause=cause,
            timestamp_ns=timestamp_ns,
            market_message_index=market_message_index,
            quantity=quantity,
        )

    def reject(self, simulated_order_id: int, *, timestamp_ns: int) -> OrderTransition:
        """Reject a pending order found non-passive at its activation point."""
        order = self.order(simulated_order_id)
        if order.state is not OrderState.PENDING_SUBMIT:
            raise self._invalid_transition(order, "reject")
        self._validate_timestamp(timestamp_ns, simulated_order_id=simulated_order_id)
        if timestamp_ns < order.effective_timestamp_ns:
            raise _fail(
                ErrorCode.LATENCY,
                "Order cannot be rejected before its activation check.",
                simulated_order_id=simulated_order_id,
            )
        updated = replace(
            order,
            state=OrderState.REJECTED,
            terminal_timestamp_ns=timestamp_ns,
            rejection_reason=RejectionReason.MARKETABLE_AT_ACTIVATION,
        )
        return self._commit(
            order,
            updated,
            cause=TransitionCause.REJECTED,
            timestamp_ns=timestamp_ns,
            rejection_reason=RejectionReason.MARKETABLE_AT_ACTIVATION,
        )

    def invalidate(
        self,
        simulated_order_id: int,
        *,
        timestamp_ns: int,
        market_message_index: int,
    ) -> OrderTransition:
        """Invalidate exposed remainder after a conservative historical cross anomaly."""
        order = self.order(simulated_order_id)
        self._require_current_market_key(
            timestamp_ns,
            market_message_index,
            simulated_order_id=simulated_order_id,
        )
        if order.state not in {
            OrderState.ACTIVE,
            OrderState.PARTIALLY_FILLED,
            OrderState.PENDING_CANCEL,
        }:
            raise self._invalid_transition(order, "invalidate")
        updated = replace(
            order,
            state=OrderState.INVALIDATED,
            terminal_timestamp_ns=timestamp_ns,
            rejection_reason=RejectionReason.COUNTERFACTUAL_CROSS,
        )
        return self._commit(
            order,
            updated,
            cause=TransitionCause.INVALIDATED,
            timestamp_ns=timestamp_ns,
            market_message_index=market_message_index,
            rejection_reason=RejectionReason.COUNTERFACTUAL_CROSS,
        )

    def expire(self, simulated_order_id: int, *, timestamp_ns: int) -> OrderTransition:
        """Expire any non-terminal order at the configured session boundary."""
        order = self.order(simulated_order_id)
        if order.terminal:
            raise self._invalid_transition(order, "expire")
        self._validate_timestamp(timestamp_ns, simulated_order_id=simulated_order_id)
        if timestamp_ns < order.requested_timestamp_ns:
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Expiry precedes the order request.",
                simulated_order_id=simulated_order_id,
            )
        updated = replace(
            order,
            state=OrderState.EXPIRED,
            terminal_timestamp_ns=timestamp_ns,
        )
        return self._commit(
            order,
            updated,
            cause=TransitionCause.EXPIRED,
            timestamp_ns=timestamp_ns,
        )

    def _apply_actions(
        self,
        actions: tuple[ScheduledAction, ...],
        activation_queue_resolver: ActivationQueueResolver | None,
    ) -> tuple[OrderTransition, ...]:
        transitions: list[OrderTransition] = []
        for action in actions:
            transition = self._apply_action(action, activation_queue_resolver)
            if transition is not None:
                transitions.append(transition)
        return tuple(transitions)

    def _apply_action(
        self,
        action: ScheduledAction,
        activation_queue_resolver: ActivationQueueResolver | None,
    ) -> OrderTransition | None:
        expected = self._scheduled_actions.get(action.sequence)
        if expected != action:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Scheduler returned an unknown action.",
                simulated_order_id=action.simulated_order_id,
            )
        order = self.order(action.simulated_order_id)
        if order.terminal:
            del self._scheduled_actions[action.sequence]
            return None
        if action.kind is ScheduledActionKind.ACTIVATE:
            if order.state is not OrderState.PENDING_SUBMIT:
                raise self._invalid_transition(order, "activate")
            queue_ahead_initial = order.queue_ahead_initial
            if activation_queue_resolver is not None:
                queue_ahead_initial = activation_queue_resolver(order)
                if queue_ahead_initial is not None and not _valid_int(
                    queue_ahead_initial, minimum=0, maximum=MAX_UINT64
                ):
                    raise _fail(
                        ErrorCode.QUEUE_STATE,
                        "Activation queue resolver returned an invalid quantity.",
                        simulated_order_id=order.simulated_order_id,
                    )
            if activation_queue_resolver is not None and queue_ahead_initial is None:
                updated = replace(
                    order,
                    state=OrderState.REJECTED,
                    terminal_timestamp_ns=action.effective_timestamp_ns,
                    rejection_reason=RejectionReason.MARKETABLE_AT_ACTIVATION,
                )
                transition = self._commit(
                    order,
                    updated,
                    cause=TransitionCause.REJECTED,
                    timestamp_ns=action.effective_timestamp_ns,
                    rejection_reason=RejectionReason.MARKETABLE_AT_ACTIVATION,
                )
                del self._scheduled_actions[action.sequence]
                return transition
            next_state = (
                OrderState.PENDING_CANCEL
                if order.cancel_requested_ns is not None
                else OrderState.ACTIVE
            )
            updated = replace(
                order,
                queue_ahead_initial=queue_ahead_initial,
                state=next_state,
            )
            transition = self._commit(
                order,
                updated,
                cause=TransitionCause.ACTIVATED,
                timestamp_ns=action.effective_timestamp_ns,
            )
        elif action.kind is ScheduledActionKind.CANCEL:
            if order.state not in {OrderState.PENDING_SUBMIT, OrderState.PENDING_CANCEL}:
                raise self._invalid_transition(order, "apply cancellation")
            if order.cancel_requested_ns is None:
                raise self._invalid_transition(order, "apply unrequested cancellation")
            updated = replace(
                order,
                state=OrderState.CANCELLED,
                terminal_timestamp_ns=action.effective_timestamp_ns,
            )
            transition = self._commit(
                order,
                updated,
                cause=TransitionCause.CANCELLED,
                timestamp_ns=action.effective_timestamp_ns,
            )
        else:  # pragma: no cover - exhaustive StrEnum guard
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Scheduled action kind is unsupported.",
                simulated_order_id=action.simulated_order_id,
            )
        del self._scheduled_actions[action.sequence]
        return transition

    def _commit(
        self,
        before: SimulatedOrder,
        after: SimulatedOrder,
        *,
        cause: TransitionCause,
        timestamp_ns: int,
        market_message_index: int | None = None,
        quantity: int | None = None,
        rejection_reason: RejectionReason | None = None,
    ) -> OrderTransition:
        validate_simulated_order(after)
        if after.terminal:
            slot = (after.symbol_id, after.side)
            if self._occupied_slots.get(slot) != after.simulated_order_id:
                raise _fail(
                    ErrorCode.INVARIANT,
                    "Terminal order did not own its symbol-side slot.",
                    simulated_order_id=after.simulated_order_id,
                )
        self._orders[after.simulated_order_id] = after
        if after.terminal:
            del self._occupied_slots[slot]
        return OrderTransition(
            simulated_order_id=after.simulated_order_id,
            before_state=before.state,
            after_state=after.state,
            cause=cause,
            timestamp_ns=timestamp_ns,
            market_message_index=market_message_index,
            quantity=quantity,
            rejection_reason=rejection_reason,
        )

    def _require_current_market_key(
        self,
        timestamp_ns: int,
        message_index: int,
        *,
        simulated_order_id: int,
    ) -> None:
        if self._scheduler.current_market_key != (timestamp_ns, message_index) or (
            self._scheduler.current_market_timestamp_completed
        ):
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Order action does not match the current open market message.",
                simulated_order_id=simulated_order_id,
                message_index=message_index,
            )

    @staticmethod
    def _validate_timestamp(timestamp_ns: int, *, simulated_order_id: int) -> None:
        if not _valid_int(timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Lifecycle timestamp is outside the exchange day.",
                simulated_order_id=simulated_order_id,
            )

    @staticmethod
    def _invalid_transition(order: SimulatedOrder, action: str) -> SimulationError:
        return _fail(
            ErrorCode.SIMULATION_ANOMALY,
            f"Cannot {action} an order in state {order.state.value}.",
            simulated_order_id=order.simulated_order_id,
        )


__all__ = [
    "ActivationQueueResolver",
    "OrderStateMachine",
    "OrderTransition",
    "TransitionCause",
]
