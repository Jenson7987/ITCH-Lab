"""TASK-022 simulated-order lifecycle, latency and race properties."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.errors import ErrorCode
from itchlab_research.simulation import (
    TERMINAL_STATES,
    OrderRequest,
    OrderState,
    OrderStateMachine,
    OrderTransition,
    RejectionReason,
    SimulationError,
    validate_simulated_order,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TRACE = REPOSITORY_ROOT / "tests" / "golden" / "simulation" / "task022-transition-trace.json"


def _request(
    order_id: int,
    timestamp_ns: int,
    message_index: int,
    *,
    symbol_id: int = 1,
    side: int = 1,
    quantity: int = 100,
) -> OrderRequest:
    return OrderRequest(
        simulated_order_id=order_id,
        decision_message_index=message_index,
        prediction_message_index=message_index,
        requested_timestamp_ns=timestamp_ns,
        symbol_id=symbol_id,
        side=side,
        price4=10_000,
        quantity=quantity,
    )


def _row(scenario: str, transition: OrderTransition) -> dict[str, Any]:
    row = asdict(transition)
    row["scenario"] = scenario
    row["before_state"] = None if transition.before_state is None else transition.before_state.value
    row["after_state"] = transition.after_state.value
    row["cause"] = transition.cause.value
    row["rejection_reason"] = (
        None if transition.rejection_reason is None else transition.rejection_reason.value
    )
    return row


def _golden_transitions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    lifecycle = OrderStateMachine(submission_latency_ns=10, cancellation_latency_ns=10)
    lifecycle.before_market_event(100, 1)
    rows.append(_row("partial_then_cancelled", lifecycle.submit(_request(1, 100, 1))))
    lifecycle.before_market_event(110, 2)
    rows.extend(
        _row("partial_then_cancelled", item) for item in lifecycle.after_market_timestamp(110)
    )
    lifecycle.before_market_event(120, 3)
    rows.append(
        _row(
            "partial_then_cancelled",
            lifecycle.record_fill(1, quantity=30, timestamp_ns=120, market_message_index=3),
        )
    )
    lifecycle.before_market_event(130, 4)
    rows.append(
        _row(
            "partial_then_cancelled",
            lifecycle.record_fill(1, quantity=20, timestamp_ns=130, market_message_index=4),
        )
    )
    rows.append(
        _row(
            "partial_then_cancelled",
            lifecycle.request_cancel(1, timestamp_ns=130, message_index=4),
        )
    )
    lifecycle.before_market_event(140, 5)
    rows.append(
        _row(
            "partial_then_cancelled",
            lifecycle.record_fill(1, quantity=10, timestamp_ns=140, market_message_index=5),
        )
    )
    rows.extend(
        _row("partial_then_cancelled", item) for item in lifecycle.after_market_timestamp(140)
    )

    fill_wins = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=10)
    fill_wins.before_market_event(200, 10)
    rows.append(_row("fill_before_cancel", fill_wins.submit(_request(2, 200, 10))))
    rows.extend(_row("fill_before_cancel", item) for item in fill_wins.after_market_timestamp(200))
    fill_wins.before_market_event(210, 11)
    rows.append(
        _row(
            "fill_before_cancel",
            fill_wins.request_cancel(2, timestamp_ns=210, message_index=11),
        )
    )
    fill_wins.before_market_event(220, 12)
    rows.append(
        _row(
            "fill_before_cancel",
            fill_wins.record_fill(2, quantity=100, timestamp_ns=220, market_message_index=12),
        )
    )
    assert fill_wins.after_market_timestamp(220) == ()

    invalidated = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=5)
    invalidated.before_market_event(300, 20)
    rows.append(_row("invalidated", invalidated.submit(_request(3, 300, 20))))
    rows.extend(_row("invalidated", item) for item in invalidated.after_market_timestamp(300))
    invalidated.before_market_event(310, 21)
    rows.append(
        _row(
            "invalidated",
            invalidated.invalidate(3, timestamp_ns=310, market_message_index=21),
        )
    )

    expired = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=5)
    expired.before_market_event(400, 30)
    rows.append(_row("expired", expired.submit(_request(4, 400, 30))))
    rows.extend(_row("expired", item) for item in expired.after_market_timestamp(400))
    rows.append(_row("expired", expired.expire(4, timestamp_ns=450)))

    cancel_first = OrderStateMachine(submission_latency_ns=50, cancellation_latency_ns=10)
    cancel_first.before_market_event(500, 40)
    rows.append(_row("cancel_before_activation", cancel_first.submit(_request(5, 500, 40))))
    rows.append(
        _row(
            "cancel_before_activation",
            cancel_first.request_cancel(5, timestamp_ns=500, message_index=40),
        )
    )
    cancel_first.before_market_event(510, 41)
    rows.extend(
        _row("cancel_before_activation", item) for item in cancel_first.after_market_timestamp(510)
    )

    equal_actions = OrderStateMachine(submission_latency_ns=20, cancellation_latency_ns=10)
    equal_actions.before_market_event(600, 50)
    rows.append(_row("equal_action_order", equal_actions.submit(_request(6, 600, 50))))
    equal_actions.before_market_event(610, 51)
    rows.append(
        _row(
            "equal_action_order",
            equal_actions.request_cancel(6, timestamp_ns=610, message_index=51),
        )
    )
    equal_actions.before_market_event(620, 52)
    rows.extend(
        _row("equal_action_order", item) for item in equal_actions.after_market_timestamp(620)
    )

    rejected = OrderStateMachine(submission_latency_ns=10, cancellation_latency_ns=10)
    rejected.before_market_event(700, 60)
    rows.append(_row("rejected", rejected.submit(_request(7, 700, 60))))
    rejected.before_market_event(710, 61)
    rows.append(_row("rejected", rejected.reject(7, timestamp_ns=710)))
    assert rejected.after_market_timestamp(710) == ()

    pending_expiry = OrderStateMachine(submission_latency_ns=50, cancellation_latency_ns=10)
    pending_expiry.before_market_event(800, 70)
    rows.append(_row("pending_expiry", pending_expiry.submit(_request(8, 800, 70))))
    rows.append(_row("pending_expiry", pending_expiry.expire(8, timestamp_ns=820)))
    return rows


def test_task_022_golden_transition_trace_covers_every_state() -> None:
    actual = _golden_transitions()
    expected = json.loads(GOLDEN_TRACE.read_text(encoding="utf-8"))

    assert actual == expected
    reached = {row["after_state"] for row in actual}
    assert reached == {state.value for state in OrderState}


def test_ut_sim_001_order_cannot_fill_before_or_at_equal_time_activation() -> None:
    machine = OrderStateMachine(submission_latency_ns=10, cancellation_latency_ns=10)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))

    machine.before_market_event(105, 2)
    before = machine.order(1)
    with pytest.raises(SimulationError) as captured:
        machine.record_fill(1, quantity=1, timestamp_ns=105, market_message_index=2)
    assert captured.value.code is ErrorCode.LATENCY
    assert machine.order(1) == before

    machine.before_market_event(110, 3)
    with pytest.raises(SimulationError) as captured:
        machine.record_fill(1, quantity=1, timestamp_ns=110, market_message_index=3)
    assert captured.value.code is ErrorCode.LATENCY
    assert machine.after_market_timestamp(110)[0].after_state is OrderState.ACTIVE

    machine.before_market_event(111, 4)
    transition = machine.record_fill(1, quantity=1, timestamp_ns=111, market_message_index=4)
    assert transition.after_state is OrderState.PARTIALLY_FILLED


def test_ut_sim_003_equal_time_fill_is_retained_before_cancellation_effect() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=10)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))
    machine.after_market_timestamp(100)

    machine.before_market_event(110, 2)
    machine.request_cancel(1, timestamp_ns=110, message_index=2)
    machine.before_market_event(120, 3)
    partial = machine.record_fill(1, quantity=40, timestamp_ns=120, market_message_index=3)
    assert partial.after_state is OrderState.PENDING_CANCEL
    machine.before_market_event(120, 4)
    filled = machine.record_fill(1, quantity=60, timestamp_ns=120, market_message_index=4)
    assert filled.after_state is OrderState.FILLED

    assert machine.after_market_timestamp(120) == ()
    assert machine.order(1).remaining_quantity == 0
    assert machine.order(1).terminal_timestamp_ns == 120


def test_ut_sim_003_fill_after_cancellation_effective_is_rejected() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=10)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))
    machine.after_market_timestamp(100)
    machine.before_market_event(110, 2)
    machine.request_cancel(1, timestamp_ns=110, message_index=2)
    machine.before_market_event(120, 3)
    assert machine.after_market_timestamp(120)[0].after_state is OrderState.CANCELLED

    machine.before_market_event(121, 4)
    before = machine.order(1)
    with pytest.raises(SimulationError) as captured:
        machine.record_fill(1, quantity=1, timestamp_ns=121, market_message_index=4)
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert machine.order(1) == before


def test_task_022_cancellation_before_activation_and_equal_action_order() -> None:
    cancel_first = OrderStateMachine(submission_latency_ns=20, cancellation_latency_ns=5)
    cancel_first.before_market_event(100, 1)
    cancel_first.submit(_request(1, 100, 1))
    cancel_first.request_cancel(1, timestamp_ns=100, message_index=1)
    cancel_first.before_market_event(105, 2)
    transitions = cancel_first.after_market_timestamp(105)
    assert [transition.after_state for transition in transitions] == [OrderState.CANCELLED]

    equal = OrderStateMachine(submission_latency_ns=10, cancellation_latency_ns=10)
    equal.before_market_event(200, 10)
    equal.submit(_request(2, 200, 10))
    equal.request_cancel(2, timestamp_ns=200, message_index=10)
    equal.before_market_event(210, 11)
    transitions = equal.after_market_timestamp(210)
    assert [transition.after_state for transition in transitions] == [
        OrderState.PENDING_CANCEL,
        OrderState.CANCELLED,
    ]


def test_task_022_every_documented_fill_invalidation_and_expiry_transition() -> None:
    observed: set[tuple[OrderState | None, OrderState]] = set()

    def active_machine(order_id: int) -> OrderStateMachine:
        machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=100)
        machine.before_market_event(100, 1)
        machine.submit(_request(order_id, 100, 1))
        machine.after_market_timestamp(100)
        machine.before_market_event(110, 2)
        return machine

    active_fill = active_machine(1)
    transition = active_fill.record_fill(1, quantity=100, timestamp_ns=110, market_message_index=2)
    observed.add((transition.before_state, transition.after_state))

    partial_fill = active_machine(2)
    partial_fill.record_fill(2, quantity=20, timestamp_ns=110, market_message_index=2)
    partial_fill.before_market_event(120, 3)
    transition = partial_fill.record_fill(2, quantity=80, timestamp_ns=120, market_message_index=3)
    observed.add((transition.before_state, transition.after_state))

    partial_invalidation = active_machine(3)
    partial_invalidation.record_fill(3, quantity=20, timestamp_ns=110, market_message_index=2)
    partial_invalidation.before_market_event(120, 3)
    transition = partial_invalidation.invalidate(3, timestamp_ns=120, market_message_index=3)
    observed.add((transition.before_state, transition.after_state))

    partial_expiry = active_machine(4)
    partial_expiry.record_fill(4, quantity=20, timestamp_ns=110, market_message_index=2)
    transition = partial_expiry.expire(4, timestamp_ns=120)
    observed.add((transition.before_state, transition.after_state))

    pending_invalidation = active_machine(5)
    pending_invalidation.request_cancel(5, timestamp_ns=110, message_index=2)
    pending_invalidation.before_market_event(120, 3)
    transition = pending_invalidation.invalidate(5, timestamp_ns=120, market_message_index=3)
    observed.add((transition.before_state, transition.after_state))

    pending_expiry = active_machine(6)
    pending_expiry.request_cancel(6, timestamp_ns=110, message_index=2)
    transition = pending_expiry.expire(6, timestamp_ns=120)
    observed.add((transition.before_state, transition.after_state))

    assert observed == {
        (OrderState.ACTIVE, OrderState.FILLED),
        (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
        (OrderState.PARTIALLY_FILLED, OrderState.INVALIDATED),
        (OrderState.PARTIALLY_FILLED, OrderState.EXPIRED),
        (OrderState.PENDING_CANCEL, OrderState.INVALIDATED),
        (OrderState.PENDING_CANCEL, OrderState.EXPIRED),
    }


def test_task_022_symbol_side_slot_releases_only_at_terminal_state() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))
    assert machine.occupied_order(1, 1) == machine.order(1)
    assert machine.occupied_order(1, -1) is None

    snapshot = machine.orders
    pending = machine.pending_actions
    with pytest.raises(SimulationError) as captured:
        machine.submit(_request(2, 100, 1))
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert machine.orders == snapshot
    assert machine.pending_actions == pending

    machine.after_market_timestamp(100)
    machine.before_market_event(101, 2)
    machine.record_fill(1, quantity=100, timestamp_ns=101, market_message_index=2)
    assert machine.occupied_order(1, 1) is None
    machine.submit(_request(2, 101, 2))
    assert machine.order(2).state is OrderState.PENDING_SUBMIT
    assert machine.occupied_order(1, 1) == machine.order(2)


@pytest.mark.parametrize("quantity", range(1, 65))
def test_task_022_generated_valid_fill_lifecycles_preserve_order_properties(
    quantity: int,
) -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=3)
    machine.before_market_event(100, 1)
    machine.submit(_request(quantity, 100, 1, quantity=quantity))
    machine.after_market_timestamp(100)

    remaining = quantity
    timestamp = 101
    message_index = 2
    while remaining:
        machine.before_market_event(timestamp, message_index)
        decrement = min(remaining, (message_index * 7) % 5 + 1)
        machine.record_fill(
            quantity,
            quantity=decrement,
            timestamp_ns=timestamp,
            market_message_index=message_index,
        )
        order = machine.order(quantity)
        validate_simulated_order(order)
        assert 0 <= order.remaining_quantity <= order.original_quantity
        remaining -= decrement
        timestamp += 1
        message_index += 1

    order = machine.order(quantity)
    assert order.state is OrderState.FILLED
    assert order.remaining_quantity == 0
    assert order.terminal_timestamp_ns is not None


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda state: state.value))
def test_task_022_terminal_states_reject_further_transitions_atomically(
    terminal: OrderState,
) -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))
    if terminal is OrderState.CANCELLED:
        machine.request_cancel(1, timestamp_ns=100, message_index=1)
        machine.after_market_timestamp(100)
    elif terminal is OrderState.REJECTED:
        machine.reject(1, timestamp_ns=100)
        machine.after_market_timestamp(100)
    elif terminal is OrderState.EXPIRED:
        machine.expire(1, timestamp_ns=100)
        machine.after_market_timestamp(100)
    else:
        machine.after_market_timestamp(100)
        machine.before_market_event(101, 2)
        if terminal is OrderState.FILLED:
            machine.record_fill(1, quantity=100, timestamp_ns=101, market_message_index=2)
        else:
            machine.invalidate(1, timestamp_ns=101, market_message_index=2)

    before = machine.order(1)
    with pytest.raises(SimulationError) as captured:
        machine.expire(1, timestamp_ns=200)
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert machine.order(1) == before


def test_task_022_invalid_fill_and_duplicate_cancel_leave_state_unchanged() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=10)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))
    machine.after_market_timestamp(100)
    machine.before_market_event(110, 2)

    before = machine.order(1)
    with pytest.raises(SimulationError) as captured:
        machine.record_fill(1, quantity=101, timestamp_ns=110, market_message_index=2)
    assert captured.value.code is ErrorCode.QUANTITY
    assert machine.order(1) == before

    machine.request_cancel(1, timestamp_ns=110, message_index=2)
    before = machine.order(1)
    pending = machine.pending_actions
    with pytest.raises(SimulationError) as captured:
        machine.request_cancel(1, timestamp_ns=110, message_index=2)
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert machine.order(1) == before
    assert machine.pending_actions == pending


@pytest.mark.parametrize("order_id", [True, -1, 1 << 64])
def test_task_022_invalid_order_identifiers_do_not_alias_owned_orders(order_id: int) -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    machine.before_market_event(100, 1)
    machine.submit(_request(1, 100, 1))

    with pytest.raises(SimulationError) as captured:
        machine.order(order_id)

    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert machine.order(1).simulated_order_id == 1


def test_task_022_rejection_and_invalidation_reasons_are_distinct() -> None:
    rejected = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    rejected.before_market_event(100, 1)
    rejected.submit(_request(1, 100, 1))
    transition = rejected.reject(1, timestamp_ns=100)
    assert transition.rejection_reason is RejectionReason.MARKETABLE_AT_ACTIVATION

    invalidated = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    invalidated.before_market_event(100, 1)
    invalidated.submit(_request(2, 100, 1))
    invalidated.after_market_timestamp(100)
    invalidated.before_market_event(101, 2)
    transition = invalidated.invalidate(2, timestamp_ns=101, market_message_index=2)
    assert transition.rejection_reason is RejectionReason.COUNTERFACTUAL_CROSS
