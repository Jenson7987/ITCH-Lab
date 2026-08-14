"""TASK-023 exact visible queue, partial-fill and anomaly properties."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.errors import ErrorCode
from itchlab_research.interchange import EventKind
from itchlab_research.simulation import (
    MarketEvent,
    OrderRequest,
    OrderState,
    OrderStateMachine,
    QueueDiagnosticCode,
    QueueFill,
    SimulationError,
    VisibleQueueModel,
    adapt_market_event,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TRACE = REPOSITORY_ROOT / "tests" / "golden" / "simulation" / "task023-queue-fill-trace.json"


def _event(
    message_index: int,
    kind: EventKind,
    *,
    timestamp_ns: int | None = None,
    symbol_id: int = 1,
    primary_reference: int | None = None,
    secondary_reference: int | None = None,
    side: int | None = None,
    price4: int | None = None,
    quantity: int | None = None,
    remaining_quantity: int | None = None,
    execution_price4: int | None = None,
) -> MarketEvent:
    return MarketEvent(
        message_index=message_index,
        timestamp_ns=100 + message_index * 10 if timestamp_ns is None else timestamp_ns,
        symbol_id=symbol_id,
        event_kind=kind,
        primary_reference=primary_reference,
        secondary_reference=secondary_reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=remaining_quantity,
        execution_price4=execution_price4,
    )


def _add(
    message_index: int,
    reference: int,
    quantity: int,
    *,
    side: int = 1,
    price4: int = 10_000,
    timestamp_ns: int | None = None,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.ADD,
        timestamp_ns=timestamp_ns,
        primary_reference=reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=quantity,
    )


def _execute(
    message_index: int,
    reference: int,
    match_number: int,
    quantity: int,
    remaining: int,
    *,
    side: int = 1,
    price4: int = 10_000,
    execution_price4: int | None = None,
    timestamp_ns: int | None = None,
) -> MarketEvent:
    kind = EventKind.EXECUTE if execution_price4 is None else EventKind.EXECUTE_PRICE
    return _event(
        message_index,
        kind,
        timestamp_ns=timestamp_ns,
        primary_reference=reference,
        secondary_reference=match_number,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=remaining,
        execution_price4=execution_price4,
    )


def _cancel(
    message_index: int,
    reference: int,
    quantity: int,
    remaining: int,
    *,
    side: int = 1,
    price4: int = 10_000,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.CANCEL,
        primary_reference=reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=remaining,
    )


def _delete(
    message_index: int,
    reference: int,
    quantity: int,
    *,
    side: int = 1,
    price4: int = 10_000,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.DELETE,
        primary_reference=reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=0,
    )


def _replace(
    message_index: int,
    old_reference: int,
    new_reference: int,
    quantity: int,
    *,
    side: int = 1,
    price4: int = 10_000,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.REPLACE,
        primary_reference=old_reference,
        secondary_reference=new_reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=quantity,
    )


def _submit(
    machine: OrderStateMachine,
    *,
    order_id: int,
    event: MarketEvent,
    side: int = 1,
    price4: int = 10_000,
    quantity: int = 100,
) -> None:
    machine.submit(
        OrderRequest(
            simulated_order_id=order_id,
            decision_message_index=event.message_index,
            prediction_message_index=event.message_index,
            requested_timestamp_ns=event.timestamp_ns,
            symbol_id=event.symbol_id,
            side=side,
            price4=price4,
            quantity=quantity,
        )
    )


def _empty_active_model(
    *,
    order_quantity: int = 100,
    cancellation_latency_ns: int = 10,
    max_queue_anomalies: int = 0,
) -> tuple[VisibleQueueModel, OrderStateMachine]:
    machine = OrderStateMachine(
        submission_latency_ns=0,
        cancellation_latency_ns=cancellation_latency_ns,
    )
    model = VisibleQueueModel(machine, max_queue_anomalies=max_queue_anomalies)
    decision = _event(1, EventKind.TRADING_STATE)
    model.process_market_event(decision)
    _submit(machine, order_id=1, event=decision, quantity=order_quantity)
    model.complete_market_timestamp(decision.timestamp_ns)
    assert machine.order(1).queue_ahead_initial == 0
    return model, machine


def _trace_fill(fill: QueueFill) -> dict[str, Any]:
    return asdict(fill)


def test_ut_sim_002_and_it_010_subset_match_hand_reconciled_queue_fill_trace() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=10)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)

    first = _add(1, 101, 60)
    model.process_market_event(first)
    model.complete_market_timestamp(first.timestamp_ns)
    second = _add(2, 102, 40)
    model.process_market_event(second)
    _submit(machine, order_id=1, event=second, quantity=70)
    model.complete_market_timestamp(second.timestamp_ns)

    trace: dict[str, Any] = {
        "activation": {
            "order": {
                "queue_ahead_initial": machine.order(1).queue_ahead_initial,
                "remaining_quantity": machine.order(1).remaining_quantity,
                "state": machine.order(1).state.value,
            },
            "queue": {
                "ahead_references": [
                    list(item) for item in model.queue_snapshot(1).ahead_references
                ],
                "current_quantity": model.queue_snapshot(1).current_quantity,
                "initial_quantity": model.queue_snapshot(1).initial_quantity,
                "simulated_order_id": 1,
            },
        },
        "events": [],
    }

    events = (
        _add(3, 103, 100),
        _execute(4, 101, 501, 20, 40),
        _cancel(5, 102, 10, 30),
        _delete(6, 102, 30),
        _replace(7, 101, 104, 50),
        _execute(8, 103, 502, 30, 70),
        _execute(9, 103, 503, 70, 0),
    )
    for event in events:
        result = model.process_market_event(event)
        order = machine.order(1)
        if order.state in {OrderState.ACTIVE, OrderState.PARTIALLY_FILLED}:
            queue_ahead = model.queue_snapshot(1).current_quantity
        elif result.fills:
            queue_ahead = result.fills[-1].queue_ahead_after
        else:
            queue_ahead = None
        trace["events"].append(
            {
                "diagnostics": [asdict(item) for item in result.diagnostics],
                "event_kind": event.event_kind.value,
                "fills": [_trace_fill(item) for item in result.fills],
                "message_index": event.message_index,
                "order_remaining": order.remaining_quantity,
                "order_state": order.state.value,
                "queue_ahead": queue_ahead,
            }
        )
        model.complete_market_timestamp(event.timestamp_ns)

    expected = json.loads(GOLDEN_TRACE.read_text(encoding="utf-8"))
    assert trace == expected
    assert sum(fill["quantity"] for row in trace["events"] for fill in row["fills"]) == 70
    assert machine.order(1).state is OrderState.FILLED


def test_task_023_equal_timestamp_adds_are_ahead_of_equal_time_activation() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)
    first = _add(1, 101, 20, timestamp_ns=100)
    model.process_market_event(first)
    _submit(machine, order_id=1, event=first)

    second = _add(2, 102, 30, timestamp_ns=100)
    assert model.process_market_event(second).transitions == ()
    transitions = model.complete_market_timestamp(100)

    assert transitions[0].after_state is OrderState.ACTIVE
    assert model.queue_snapshot(1).ahead_references == ((101, 20), (102, 30))
    assert machine.order(1).queue_ahead_initial == 50


@pytest.mark.parametrize("ahead_quantity", range(1, 33))
def test_task_023_generated_queue_and_fill_properties(ahead_quantity: int) -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)
    ahead = _add(1, 1000 + ahead_quantity, ahead_quantity)
    model.process_market_event(ahead)
    _submit(machine, order_id=ahead_quantity, event=ahead, quantity=ahead_quantity + 3)
    model.complete_market_timestamp(ahead.timestamp_ns)

    behind = _add(2, 2000 + ahead_quantity, ahead_quantity + 5)
    model.process_market_event(behind)
    assert model.queue_snapshot(ahead_quantity).current_quantity == ahead_quantity

    cancellation = _cancel(3, ahead.primary_reference or 0, ahead_quantity, 0)
    model.process_market_event(cancellation)
    assert model.queue_snapshot(ahead_quantity).current_quantity == 0

    first_fill_event = _execute(
        4,
        behind.primary_reference or 0,
        3000 + ahead_quantity,
        2,
        ahead_quantity + 3,
    )
    first_result = model.process_market_event(first_fill_event)
    assert first_result.fills[0].quantity == 2
    assert first_result.fills[0].quantity <= first_fill_event.quantity  # type: ignore[operator]

    final_event = _execute(
        5,
        behind.primary_reference or 0,
        4000 + ahead_quantity,
        ahead_quantity + 3,
        0,
    )
    final_result = model.process_market_event(final_event)
    fills = first_result.fills + final_result.fills
    assert sum(fill.quantity for fill in fills) == ahead_quantity + 3
    assert sum(fill.quantity for fill in fills) <= machine.order(ahead_quantity).original_quantity
    assert all(fill.queue_ahead_before >= fill.queue_ahead_after >= 0 for fill in fills)
    assert machine.order(ahead_quantity).state is OrderState.FILLED


def test_task_023_add_and_replacement_after_activation_stay_behind() -> None:
    model, machine = _empty_active_model()
    add = _add(2, 101, 40)
    model.process_market_event(add)
    replacement = _replace(3, 101, 102, 50)
    model.process_market_event(replacement)

    assert model.queue_snapshot(1).current_quantity == 0
    result = model.process_market_event(_execute(4, 102, 900, 25, 25))
    assert result.fills[0].quantity == 25
    assert machine.order(1).state is OrderState.PARTIALLY_FILLED


def test_task_023_c_uses_display_price_and_p_q_never_fill_or_invalidate() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)
    ahead = _add(1, 100, 10)
    model.process_market_event(ahead)
    _submit(machine, order_id=1, event=ahead)
    model.complete_market_timestamp(ahead.timestamp_ns)
    trade = _event(
        2,
        EventKind.TRADE,
        primary_reference=0,
        secondary_reference=600,
        side=1,
        price4=9_900,
        quantity=100,
    )
    cross = _event(
        3,
        EventKind.CROSS,
        secondary_reference=601,
        price4=10_000,
        quantity=100,
    )
    assert model.process_market_event(trade).fills == ()
    assert model.process_market_event(cross).fills == ()
    assert machine.order(1).state is OrderState.ACTIVE
    assert model.queue_snapshot(1).current_quantity == 10

    model.process_market_event(_delete(4, 100, 10))
    model.process_market_event(_add(5, 101, 40))
    result = model.process_market_event(_execute(6, 101, 602, 40, 0, execution_price4=9_950))
    assert result.fills[0].quantity == 40
    assert result.fills[0].price4 == 10_000
    assert machine.order(1).state is OrderState.PARTIALLY_FILLED


def test_task_023_fill_at_cancel_effective_timestamp_wins_before_cancellation() -> None:
    model, machine = _empty_active_model(cancellation_latency_ns=10)
    add = _add(2, 101, 100)
    model.process_market_event(add)
    machine.request_cancel(1, timestamp_ns=add.timestamp_ns, message_index=add.message_index)
    model.complete_market_timestamp(add.timestamp_ns)

    execution = _execute(3, 101, 700, 100, 0, timestamp_ns=add.timestamp_ns + 10)
    result = model.process_market_event(execution)
    assert result.fills[0].quantity == 100
    assert machine.order(1).state is OrderState.FILLED
    assert model.complete_market_timestamp(execution.timestamp_ns) == ()


@pytest.mark.parametrize(
    "crossing_event",
    [
        _add(2, 201, 10, side=-1, price4=10_000),
        _execute(3, 202, 800, 10, 0, price4=9_900),
    ],
)
def test_task_023_cross_through_invalidates_without_inventing_fill(
    crossing_event: MarketEvent,
) -> None:
    model, machine = _empty_active_model()
    if crossing_event.event_kind is EventKind.EXECUTE:
        resting = _add(2, 202, 10, price4=9_900)
        model.process_market_event(resting)
        crossing_event = _execute(3, 202, 800, 10, 0, price4=9_900)

    result = model.process_market_event(crossing_event)
    assert result.fills == ()
    assert result.diagnostics[-1].code is QueueDiagnosticCode.COUNTERFACTUAL_CROSS
    assert machine.order(1).state is OrderState.INVALIDATED
    assert model.anomaly_count == 0


def test_task_023_marketable_order_is_rejected_at_activation() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)
    ask = _add(1, 101, 10, side=-1, price4=10_000)
    model.process_market_event(ask)
    _submit(machine, order_id=1, event=ask, price4=10_000)

    transition = model.complete_market_timestamp(ask.timestamp_ns)[0]
    assert transition.after_state is OrderState.REJECTED
    assert machine.order(1).queue_ahead_initial is None


def test_task_023_behind_execution_with_known_ahead_is_diagnosed_without_fill() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=1)
    ahead = _add(1, 101, 20)
    model.process_market_event(ahead)
    _submit(machine, order_id=1, event=ahead)
    model.complete_market_timestamp(ahead.timestamp_ns)
    model.process_market_event(_add(2, 102, 30))

    result = model.process_market_event(_execute(3, 102, 900, 10, 20))
    assert result.fills == ()
    assert result.diagnostics[0].reason == "execution_behind_known_ahead"
    assert model.queue_snapshot(1).current_quantity == 20

    model.process_market_event(_delete(4, 101, 20))
    fill = model.process_market_event(_execute(5, 102, 901, 20, 0)).fills[0]
    assert fill.quantity == 20


def test_task_023_anomaly_budget_is_exact_and_rejected_events_are_atomic() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=1)
    original = _add(1, 101, 10)
    model.process_market_event(original)
    duplicate = _add(2, 101, 999)
    result = model.process_market_event(duplicate)
    assert result.diagnostics[0].code is QueueDiagnosticCode.EVENT_SKIPPED
    assert model.anomaly_count == 1

    _submit(machine, order_id=1, event=duplicate)
    model.complete_market_timestamp(duplicate.timestamp_ns)
    assert model.queue_snapshot(1).ahead_references == ((101, 10),)
    before = model.queue_snapshot(1)

    with pytest.raises(SimulationError) as captured:
        model.process_market_event(_delete(3, 999, 5))
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY
    assert model.queue_snapshot(1) == before
    assert model.anomaly_count == 2


def test_task_023_broken_fill_match_is_fatal_but_other_break_is_recorded() -> None:
    model, machine = _empty_active_model(order_quantity=10)
    model.process_market_event(_add(2, 101, 20))
    fill = model.process_market_event(_execute(3, 101, 900, 10, 10)).fills[0]
    assert fill.match_number == 900
    assert machine.order(1).state is OrderState.FILLED

    unrelated = _event(4, EventKind.BROKEN_TRADE, primary_reference=901)
    result = model.process_market_event(unrelated)
    assert result.diagnostics[0].code is QueueDiagnosticCode.BROKEN_TRADE_OBSERVED

    broken_fill = _event(5, EventKind.BROKEN_TRADE, primary_reference=900)
    with pytest.raises(SimulationError) as captured:
        model.process_market_event(broken_fill)
    assert captured.value.code is ErrorCode.BROKEN_SIM_FILL
    assert machine.order(1).state is OrderState.FILLED


def test_task_023_adapter_rejects_malformed_rows_before_scheduler_state_changes() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    model = VisibleQueueModel(machine, max_queue_anomalies=0)
    row: dict[str, object] = {
        "message_index": True,
        "timestamp_ns": 100,
        "symbol_id": 1,
        "event_kind": "add",
        "primary_reference": 1,
        "side": 1,
        "price4": 10_000,
        "quantity": 10,
        "remaining_quantity": 10,
        "execution_price4": None,
    }

    with pytest.raises(SimulationError) as captured:
        model.process_market_event(row)
    assert captured.value.code is ErrorCode.QUEUE_STATE
    assert machine.pending_actions == ()


def test_task_023_adapter_accepts_complete_parquet_style_c_row() -> None:
    event = adapt_market_event(
        {
            "trading_date": "2019-01-30",
            "symbol": "SYNTH",
            "message_index": 7,
            "timestamp_ns": 200,
            "symbol_id": 1,
            "event_kind": "execute_price",
            "source_type": "C",
            "primary_reference": 11,
            "secondary_reference": 22,
            "side": -1,
            "price4": 10_100,
            "quantity": 5,
            "remaining_quantity": 15,
            "execution_price4": 10_050,
            "aux_code": None,
            "event_subtype": None,
            "in_session": True,
            "flags": 0,
        }
    )

    assert event.event_kind is EventKind.EXECUTE_PRICE
    assert event.price4 == 10_100
    assert event.execution_price4 == 10_050
