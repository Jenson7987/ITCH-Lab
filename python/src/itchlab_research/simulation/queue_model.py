"""Exact-known visible queue tracking and conservative execution eligibility."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import cast

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.interchange import EventKind
from itchlab_research.simulation.market_events import MarketEvent, adapt_market_event
from itchlab_research.simulation.order import MAX_UINT64, OrderState, SimulatedOrder
from itchlab_research.simulation.state_machine import (
    OrderStateMachine,
    OrderTransition,
)

_MAX_SAFE_JSON_INTEGER = (1 << 53) - 1
_EXPOSED_STATES = {
    OrderState.ACTIVE,
    OrderState.PARTIALLY_FILLED,
    OrderState.PENDING_CANCEL,
}


class QueueDiagnosticCode(StrEnum):
    """Stable non-fatal diagnostic categories produced by the queue model."""

    EVENT_SKIPPED = "DIAG_QUEUE_EVENT_SKIPPED"
    COUNTERFACTUAL_CROSS = "DIAG_COUNTERFACTUAL_CROSS"
    BROKEN_TRADE_OBSERVED = "DIAG_BROKEN_TRADE_OBSERVED"


class QueueAnomalyReason(StrEnum):
    """Bounded reasons for lifecycle events that cannot safely affect simulation state."""

    DUPLICATE_REFERENCE = "duplicate_reference"
    MISSING_REFERENCE = "missing_reference"
    REFERENCE_MISMATCH = "reference_mismatch"
    QUANTITY_MISMATCH = "quantity_mismatch"
    EXECUTION_BEHIND_AHEAD = "execution_behind_known_ahead"
    DUPLICATE_FILL_MATCH = "duplicate_fill_match"


@dataclass(frozen=True, slots=True)
class QueueDiagnostic:
    """One payload-free queue diagnostic suitable for later aggregation."""

    code: QueueDiagnosticCode
    message_index: int
    symbol_id: int
    simulated_order_id: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Exact current queue state for one exposed simulated order."""

    simulated_order_id: int
    initial_quantity: int
    current_quantity: int
    ahead_references: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class QueueFill:
    """One causal queue fill consumed by the separate integer accounting ledger."""

    simulated_order_id: int
    market_message_index: int
    timestamp_ns: int
    match_number: int
    price4: int
    quantity: int
    queue_ahead_before: int
    queue_ahead_after: int
    remaining_quantity_after: int


@dataclass(frozen=True, slots=True)
class QueueEventResult:
    """Deterministic effects of one adapted market event."""

    event: MarketEvent
    transitions: tuple[OrderTransition, ...]
    fills: tuple[QueueFill, ...]
    diagnostics: tuple[QueueDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class _VisibleOrder:
    reference: int
    symbol_id: int
    side: int
    price4: int
    remaining_quantity: int
    priority_message_index: int


@dataclass(slots=True)
class _TrackedQueue:
    simulated_order_id: int
    symbol_id: int
    side: int
    price4: int
    initial_quantity: int
    ahead: dict[int, int]


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


class VisibleQueueModel:
    """Adapt source events, maintain exact visible priority and drive eligible fills."""

    def __init__(
        self,
        state_machine: OrderStateMachine,
        *,
        max_queue_anomalies: int,
    ) -> None:
        if not isinstance(state_machine, OrderStateMachine):
            raise _fail(ErrorCode.QUEUE_STATE, "Queue model state machine is invalid.")
        if not _valid_int(
            max_queue_anomalies,
            minimum=0,
            maximum=_MAX_SAFE_JSON_INTEGER,
        ):
            raise _fail(ErrorCode.QUEUE_STATE, "Queue anomaly budget is invalid.")
        self._state_machine = state_machine
        self._max_queue_anomalies = max_queue_anomalies
        self._visible_orders: dict[int, _VisibleOrder] = {}
        self._queues: dict[int, _TrackedQueue] = {}
        self._used_fill_matches: dict[int, int] = {}
        self._diagnostics: list[QueueDiagnostic] = []
        self._anomaly_count = 0

    @property
    def state_machine(self) -> OrderStateMachine:
        """Return the lifecycle owner coordinated by this queue model."""
        return self._state_machine

    @property
    def diagnostics(self) -> tuple[QueueDiagnostic, ...]:
        """Return all diagnostics in source order."""
        return tuple(self._diagnostics)

    @property
    def anomaly_count(self) -> int:
        """Return inconsistent queue events counted against the configured budget."""
        return self._anomaly_count

    def queue_snapshot(self, simulated_order_id: int) -> QueueSnapshot:
        """Return the exact current ahead references for one exposed order."""
        tracker = self._queues.get(simulated_order_id)
        if (
            tracker is None
            or self._state_machine.order(simulated_order_id).state not in _EXPOSED_STATES
        ):
            raise _fail(
                ErrorCode.QUEUE_STATE,
                "Simulated order has no active queue position.",
                simulated_order_id=simulated_order_id,
            )
        current = self._queue_total(tracker)
        return QueueSnapshot(
            simulated_order_id=simulated_order_id,
            initial_quantity=tracker.initial_quantity,
            current_quantity=current,
            ahead_references=tuple(tracker.ahead.items()),
        )

    def process_market_event(self, row: Mapping[str, object] | MarketEvent) -> QueueEventResult:
        """Apply one source event after earlier scheduled actions and before equal-time actions."""
        event = adapt_market_event(row)
        diagnostic_start = len(self._diagnostics)
        transitions = list(
            self._state_machine.before_market_event(
                event.timestamp_ns,
                event.message_index,
                activation_queue_resolver=self._resolve_activation,
            )
        )
        self._synchronise_transitions(transitions)
        fills: list[QueueFill] = []
        visible_event_applied = False

        if event.event_kind is EventKind.ADD:
            visible_event_applied = self._apply_add(event)
        elif event.event_kind in {EventKind.EXECUTE, EventKind.EXECUTE_PRICE}:
            fill, transition, visible_event_applied = self._apply_execution(event)
            if fill is not None:
                fills.append(fill)
            if transition is not None:
                transitions.append(transition)
                self._synchronise_transitions((transition,))
        elif event.event_kind is EventKind.CANCEL:
            visible_event_applied = self._apply_cancel(event)
        elif event.event_kind is EventKind.DELETE:
            visible_event_applied = self._apply_delete(event)
        elif event.event_kind is EventKind.REPLACE:
            visible_event_applied = self._apply_replace(event)
        elif event.event_kind is EventKind.BROKEN_TRADE:
            self._apply_broken_trade(event)

        if visible_event_applied:
            invalidations = self._invalidate_crossed_remainders(event)
            transitions.extend(invalidations)
            self._synchronise_transitions(invalidations)

        return QueueEventResult(
            event=event,
            transitions=tuple(transitions),
            fills=tuple(fills),
            diagnostics=tuple(self._diagnostics[diagnostic_start:]),
        )

    def complete_market_timestamp(self, timestamp_ns: int) -> tuple[OrderTransition, ...]:
        """Apply equal-time actions after the caller has supplied every source message."""
        transitions = self._state_machine.after_market_timestamp(
            timestamp_ns,
            activation_queue_resolver=self._resolve_activation,
        )
        self._synchronise_transitions(transitions)
        return transitions

    def _resolve_activation(self, order: SimulatedOrder) -> int | None:
        if self._is_marketable(order):
            return None
        ahead: dict[int, int] = {}
        total = 0
        for visible in self._visible_orders.values():
            if (
                visible.symbol_id == order.symbol_id
                and visible.side == order.side
                and visible.price4 == order.price4
            ):
                if total > MAX_UINT64 - visible.remaining_quantity:
                    raise _fail(
                        ErrorCode.QUEUE_STATE,
                        "Activation queue-ahead quantity overflowed.",
                        simulated_order_id=order.simulated_order_id,
                    )
                ahead[visible.reference] = visible.remaining_quantity
                total += visible.remaining_quantity
        self._queues[order.simulated_order_id] = _TrackedQueue(
            simulated_order_id=order.simulated_order_id,
            symbol_id=order.symbol_id,
            side=order.side,
            price4=order.price4,
            initial_quantity=total,
            ahead=ahead,
        )
        return total

    def _is_marketable(self, order: SimulatedOrder) -> bool:
        opposite_prices = [
            visible.price4
            for visible in self._visible_orders.values()
            if visible.symbol_id == order.symbol_id and visible.side == -order.side
        ]
        if not opposite_prices:
            return False
        if order.side == 1:
            return min(opposite_prices) <= order.price4
        return max(opposite_prices) >= order.price4

    def _apply_add(self, event: MarketEvent) -> bool:
        reference = cast(int, event.primary_reference)
        if reference in self._visible_orders:
            self._record_anomaly(event, QueueAnomalyReason.DUPLICATE_REFERENCE)
            return False
        self._visible_orders[reference] = _VisibleOrder(
            reference=reference,
            symbol_id=event.symbol_id,
            side=cast(int, event.side),
            price4=cast(int, event.price4),
            remaining_quantity=cast(int, event.quantity),
            priority_message_index=event.message_index,
        )
        return True

    def _apply_execution(
        self, event: MarketEvent
    ) -> tuple[QueueFill | None, OrderTransition | None, bool]:
        visible = self._validated_existing_order(event)
        if visible is None:
            return None, None, False
        quantity = cast(int, event.quantity)
        remaining = cast(int, event.remaining_quantity)
        if (
            quantity > visible.remaining_quantity
            or remaining != visible.remaining_quantity - quantity
        ):
            self._record_anomaly(event, QueueAnomalyReason.QUANTITY_MISMATCH)
            return None, None, False

        tracker = self._matching_queue(
            event.symbol_id,
            cast(int, event.side),
            cast(int, event.price4),
        )
        queue_before = 0 if tracker is None else self._queue_total(tracker)
        target_ahead = tracker is not None and visible.reference in tracker.ahead
        fill_quantity = 0
        match_number = cast(int, event.secondary_reference)
        if tracker is not None and not target_ahead:
            if queue_before != 0:
                self._record_anomaly(
                    event,
                    QueueAnomalyReason.EXECUTION_BEHIND_AHEAD,
                    simulated_order_id=tracker.simulated_order_id,
                )
            elif match_number in self._used_fill_matches:
                self._record_anomaly(
                    event,
                    QueueAnomalyReason.DUPLICATE_FILL_MATCH,
                    simulated_order_id=tracker.simulated_order_id,
                )
            else:
                simulated = self._state_machine.order(tracker.simulated_order_id)
                fill_quantity = min(quantity, simulated.remaining_quantity)

        self._decrement_visible(visible, quantity)
        if target_ahead:
            self._decrement_ahead(cast(_TrackedQueue, tracker), visible.reference, quantity)

        if fill_quantity == 0 or tracker is None:
            return None, None, True
        transition = self._state_machine.record_fill(
            tracker.simulated_order_id,
            quantity=fill_quantity,
            timestamp_ns=event.timestamp_ns,
            market_message_index=event.message_index,
        )
        self._used_fill_matches[match_number] = tracker.simulated_order_id
        order_after = self._state_machine.order(tracker.simulated_order_id)
        fill = QueueFill(
            simulated_order_id=tracker.simulated_order_id,
            market_message_index=event.message_index,
            timestamp_ns=event.timestamp_ns,
            match_number=match_number,
            price4=tracker.price4,
            quantity=fill_quantity,
            queue_ahead_before=queue_before,
            queue_ahead_after=self._queue_total(tracker),
            remaining_quantity_after=order_after.remaining_quantity,
        )
        return fill, transition, True

    def _apply_cancel(self, event: MarketEvent) -> bool:
        visible = self._validated_existing_order(event)
        if visible is None:
            return False
        quantity = cast(int, event.quantity)
        remaining = cast(int, event.remaining_quantity)
        if (
            quantity > visible.remaining_quantity
            or remaining != visible.remaining_quantity - quantity
        ):
            self._record_anomaly(event, QueueAnomalyReason.QUANTITY_MISMATCH)
            return False
        self._decrement_visible(visible, quantity)
        tracker = self._queue_containing(visible.reference)
        if tracker is not None:
            self._decrement_ahead(tracker, visible.reference, quantity)
        return True

    def _apply_delete(self, event: MarketEvent) -> bool:
        visible = self._validated_existing_order(event)
        if visible is None:
            return False
        if event.quantity != visible.remaining_quantity:
            self._record_anomaly(event, QueueAnomalyReason.QUANTITY_MISMATCH)
            return False
        del self._visible_orders[visible.reference]
        tracker = self._queue_containing(visible.reference)
        if tracker is not None:
            del tracker.ahead[visible.reference]
        return True

    def _apply_replace(self, event: MarketEvent) -> bool:
        visible = self._visible_orders.get(cast(int, event.primary_reference))
        if visible is None:
            self._record_anomaly(event, QueueAnomalyReason.MISSING_REFERENCE)
            return False
        new_reference = cast(int, event.secondary_reference)
        if new_reference == visible.reference or new_reference in self._visible_orders:
            self._record_anomaly(event, QueueAnomalyReason.DUPLICATE_REFERENCE)
            return False
        if visible.symbol_id != event.symbol_id or visible.side != event.side:
            self._record_anomaly(event, QueueAnomalyReason.REFERENCE_MISMATCH)
            return False

        tracker = self._queue_containing(visible.reference)
        if tracker is not None:
            del tracker.ahead[visible.reference]
        del self._visible_orders[visible.reference]
        self._visible_orders[new_reference] = _VisibleOrder(
            reference=new_reference,
            symbol_id=event.symbol_id,
            side=cast(int, event.side),
            price4=cast(int, event.price4),
            remaining_quantity=cast(int, event.quantity),
            priority_message_index=event.message_index,
        )
        return True

    def _apply_broken_trade(self, event: MarketEvent) -> None:
        match_number = cast(int, event.primary_reference)
        simulated_order_id = self._used_fill_matches.get(match_number)
        if simulated_order_id is not None:
            raise _fail(
                ErrorCode.BROKEN_SIM_FILL,
                "Broken trade references an execution used for a simulated fill.",
                simulated_order_id=simulated_order_id,
                message_index=event.message_index,
            )
        self._diagnostics.append(
            QueueDiagnostic(
                code=QueueDiagnosticCode.BROKEN_TRADE_OBSERVED,
                message_index=event.message_index,
                symbol_id=event.symbol_id,
                simulated_order_id=None,
                reason="match_not_used_for_simulated_fill",
            )
        )

    def _validated_existing_order(self, event: MarketEvent) -> _VisibleOrder | None:
        visible = self._visible_orders.get(cast(int, event.primary_reference))
        if visible is None:
            self._record_anomaly(event, QueueAnomalyReason.MISSING_REFERENCE)
            return None
        if (
            visible.symbol_id != event.symbol_id
            or visible.side != event.side
            or visible.price4 != event.price4
        ):
            self._record_anomaly(event, QueueAnomalyReason.REFERENCE_MISMATCH)
            return None
        return visible

    def _decrement_visible(self, visible: _VisibleOrder, quantity: int) -> None:
        remaining = visible.remaining_quantity - quantity
        if remaining == 0:
            del self._visible_orders[visible.reference]
        else:
            self._visible_orders[visible.reference] = replace(visible, remaining_quantity=remaining)

    def _decrement_ahead(self, tracker: _TrackedQueue, reference: int, quantity: int) -> None:
        tracked = tracker.ahead.get(reference)
        if tracked is None or quantity > tracked:
            raise _fail(
                ErrorCode.QUEUE_STATE,
                "Exact ahead-reference quantity is inconsistent.",
                simulated_order_id=tracker.simulated_order_id,
            )
        if quantity == tracked:
            del tracker.ahead[reference]
        else:
            tracker.ahead[reference] = tracked - quantity

    def _matching_queue(self, symbol_id: int, side: int, price4: int) -> _TrackedQueue | None:
        for tracker in self._queues.values():
            if (
                tracker.symbol_id == symbol_id
                and tracker.side == side
                and tracker.price4 == price4
                and self._state_machine.order(tracker.simulated_order_id).state in _EXPOSED_STATES
            ):
                return tracker
        return None

    def _queue_containing(self, reference: int) -> _TrackedQueue | None:
        for tracker in self._queues.values():
            if reference in tracker.ahead:
                return tracker
        return None

    @staticmethod
    def _queue_total(tracker: _TrackedQueue) -> int:
        total = 0
        for quantity in tracker.ahead.values():
            if total > MAX_UINT64 - quantity:
                raise _fail(
                    ErrorCode.QUEUE_STATE,
                    "Current queue-ahead quantity overflowed.",
                    simulated_order_id=tracker.simulated_order_id,
                )
            total += quantity
        return total

    def _invalidate_crossed_remainders(self, event: MarketEvent) -> tuple[OrderTransition, ...]:
        transitions: list[OrderTransition] = []
        for tracker in tuple(self._queues.values()):
            order = self._state_machine.order(tracker.simulated_order_id)
            if order.state not in _EXPOSED_STATES:
                continue
            execution_cross = (
                event.event_kind in {EventKind.EXECUTE, EventKind.EXECUTE_PRICE}
                and event.symbol_id == tracker.symbol_id
                and event.side == tracker.side
                and (
                    (tracker.side == 1 and cast(int, event.price4) < tracker.price4)
                    or (tracker.side == -1 and cast(int, event.price4) > tracker.price4)
                )
            )
            if not execution_cross and not self._opposite_book_crosses(tracker):
                continue
            transition = self._state_machine.invalidate(
                tracker.simulated_order_id,
                timestamp_ns=event.timestamp_ns,
                market_message_index=event.message_index,
            )
            transitions.append(transition)
            self._diagnostics.append(
                QueueDiagnostic(
                    code=QueueDiagnosticCode.COUNTERFACTUAL_CROSS,
                    message_index=event.message_index,
                    symbol_id=event.symbol_id,
                    simulated_order_id=tracker.simulated_order_id,
                    reason=(
                        "execution_progressed_beyond_limit"
                        if execution_cross
                        else "opposite_visible_book_crossed_limit"
                    ),
                )
            )
        return tuple(transitions)

    def _opposite_book_crosses(self, tracker: _TrackedQueue) -> bool:
        opposing = [
            visible.price4
            for visible in self._visible_orders.values()
            if visible.symbol_id == tracker.symbol_id and visible.side == -tracker.side
        ]
        if not opposing:
            return False
        if tracker.side == 1:
            return min(opposing) <= tracker.price4
        return max(opposing) >= tracker.price4

    def _record_anomaly(
        self,
        event: MarketEvent,
        reason: QueueAnomalyReason,
        *,
        simulated_order_id: int | None = None,
    ) -> None:
        self._diagnostics.append(
            QueueDiagnostic(
                code=QueueDiagnosticCode.EVENT_SKIPPED,
                message_index=event.message_index,
                symbol_id=event.symbol_id,
                simulated_order_id=simulated_order_id,
                reason=reason.value,
            )
        )
        self._anomaly_count += 1
        if self._anomaly_count > self._max_queue_anomalies:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Queue anomaly budget was exceeded.",
                simulated_order_id=simulated_order_id,
                message_index=event.message_index,
            )

    def _synchronise_transitions(
        self, transitions: tuple[OrderTransition, ...] | list[OrderTransition]
    ) -> None:
        for transition in transitions:
            if transition.after_state not in _EXPOSED_STATES:
                self._queues.pop(transition.simulated_order_id, None)


__all__ = [
    "QueueAnomalyReason",
    "QueueDiagnostic",
    "QueueDiagnosticCode",
    "QueueEventResult",
    "QueueFill",
    "QueueSnapshot",
    "VisibleQueueModel",
]
