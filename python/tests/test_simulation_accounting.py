"""TASK-024 integer accounting, inventory risk and terminal liquidation tests."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

import itchlab_research.simulation.accounting as accounting_module
from itchlab_research.errors import ErrorCode
from itchlab_research.interchange import EventKind
from itchlab_research.simulation import (
    MAX_INT64,
    MAX_MID2,
    AccountedFill,
    AccountingLedger,
    InventoryRiskLimit,
    MarketEvent,
    OrderRequest,
    OrderState,
    OrderStateMachine,
    QueueFill,
    RiskDecisionReason,
    SimulatedOrder,
    SimulationError,
    TerminalQuote,
    VisibleQueueModel,
    accounting_metrics,
    settle_session_end,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TRACE = (
    REPOSITORY_ROOT / "tests" / "golden" / "simulation" / ("task024-accounting-trace.json")
)


def _json_value(value: object) -> Any:
    return json.loads(json.dumps(asdict(value), sort_keys=True))


def _event(
    message_index: int,
    kind: EventKind,
    *,
    symbol_id: int = 1,
    primary_reference: int | None = None,
    secondary_reference: int | None = None,
    side: int | None = None,
    price4: int | None = None,
    quantity: int | None = None,
    remaining_quantity: int | None = None,
) -> MarketEvent:
    return MarketEvent(
        message_index=message_index,
        timestamp_ns=100 + message_index * 10,
        symbol_id=symbol_id,
        event_kind=kind,
        primary_reference=primary_reference,
        secondary_reference=secondary_reference,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=remaining_quantity,
        execution_price4=None,
    )


def _add(
    message_index: int,
    reference: int,
    quantity: int,
    *,
    side: int,
    price4: int,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.ADD,
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
    *,
    side: int,
    price4: int,
) -> MarketEvent:
    return _event(
        message_index,
        EventKind.EXECUTE,
        primary_reference=reference,
        secondary_reference=match_number,
        side=side,
        price4=price4,
        quantity=quantity,
        remaining_quantity=0,
    )


def _submit(
    machine: OrderStateMachine,
    event: MarketEvent,
    *,
    order_id: int,
    side: int,
    price4: int,
    quantity: int,
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


def _order_after_fill(
    *,
    order_id: int = 1,
    symbol_id: int = 1,
    side: int = 1,
    price4: int = 10_000,
    original_quantity: int = 100,
    remaining_quantity: int = 0,
) -> SimulatedOrder:
    filled = remaining_quantity == 0
    return SimulatedOrder(
        simulated_order_id=order_id,
        decision_message_index=1,
        prediction_message_index=1,
        requested_timestamp_ns=100,
        effective_timestamp_ns=100,
        symbol_id=symbol_id,
        side=side,
        price4=price4,
        original_quantity=original_quantity,
        remaining_quantity=remaining_quantity,
        queue_ahead_initial=0,
        state=OrderState.FILLED if filled else OrderState.PARTIALLY_FILLED,
        cancel_requested_ns=None,
        terminal_timestamp_ns=110 if filled else None,
        rejection_reason=None,
    )


def _queue_fill(
    order: SimulatedOrder,
    *,
    quantity: int,
    message_index: int = 2,
    match_number: int = 500,
) -> QueueFill:
    return QueueFill(
        simulated_order_id=order.simulated_order_id,
        market_message_index=message_index,
        timestamp_ns=110,
        match_number=match_number,
        price4=order.price4,
        quantity=quantity,
        queue_ahead_before=0,
        queue_ahead_after=0,
        remaining_quantity_after=order.remaining_quantity,
    )


def _record_direct_fill(
    ledger: AccountingLedger,
    *,
    order_id: int,
    symbol_id: int,
    side: int,
    price4: int,
    quantity: int,
    mark_mid2: int,
    message_index: int,
) -> AccountedFill:
    order = _order_after_fill(
        order_id=order_id,
        symbol_id=symbol_id,
        side=side,
        price4=price4,
        original_quantity=quantity,
    )
    return ledger.record_queue_fill(
        _queue_fill(
            order,
            quantity=quantity,
            message_index=message_index,
            match_number=1_000 + message_index,
        ),
        order,
        mark_mid2=mark_mid2,
    )


def test_ut_sim_004_matches_hand_reconciled_accounting_and_liquidation_trace() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    queue = VisibleQueueModel(machine, max_queue_anomalies=0)
    ledger = AccountingLedger(maker_fee_microusd_per_share=-2_000, inventory_limit=1_000)

    buy_decision = _event(1, EventKind.TRADING_STATE)
    queue.process_market_event(buy_decision)
    _submit(machine, buy_decision, order_id=1, side=1, price4=10_000, quantity=100)
    queue.complete_market_timestamp(buy_decision.timestamp_ns)
    buy_add = _add(2, 101, 100, side=1, price4=10_000)
    queue.process_market_event(buy_add)
    queue.complete_market_timestamp(buy_add.timestamp_ns)
    buy_result = queue.process_market_event(_execute(3, 101, 501, 100, side=1, price4=10_000))
    buy_fill = ledger.record_queue_fill(buy_result.fills[0], machine.order(1), mark_mid2=20_100)
    queue.complete_market_timestamp(buy_result.event.timestamp_ns)

    sell_decision = _event(4, EventKind.TRADING_STATE)
    queue.process_market_event(sell_decision)
    _submit(machine, sell_decision, order_id=2, side=-1, price4=10_100, quantity=40)
    queue.complete_market_timestamp(sell_decision.timestamp_ns)
    sell_add = _add(5, 201, 40, side=-1, price4=10_100)
    queue.process_market_event(sell_add)
    queue.complete_market_timestamp(sell_add.timestamp_ns)
    sell_result = queue.process_market_event(_execute(6, 201, 502, 40, side=-1, price4=10_100))
    sell_fill = ledger.record_queue_fill(sell_result.fills[0], machine.order(2), mark_mid2=20_080)
    queue.complete_market_timestamp(sell_result.event.timestamp_ns)

    pre_liquidation = accounting_metrics(ledger)
    settlement = settle_session_end(
        machine,
        ledger,
        session_end_timestamp_ns=200,
        last_quotes=(
            TerminalQuote(
                symbol_id=1,
                timestamp_ns=160,
                best_bid_price4=10_020,
                best_ask_price4=10_060,
            ),
        ),
        taker_fee_microusd_per_share=3_000,
    )
    trace = {
        "passive_fills": [_json_value(buy_fill), _json_value(sell_fill)],
        "pre_liquidation_metrics": _json_value(pre_liquidation),
        "expired_order_ids": [
            transition.simulated_order_id for transition in settlement.expired_orders
        ],
        "liquidations": [_json_value(item) for item in settlement.liquidations],
        "final_metrics": _json_value(accounting_metrics(settlement.accounting)),
    }

    assert trace == json.loads(GOLDEN_TRACE.read_text(encoding="utf-8"))
    assert pre_liquidation.marked_pnl_microusd == 920_000
    assert settlement.accounting.marked_pnl_microusd == 620_000


@pytest.mark.parametrize(
    ("inventory", "side", "quantity", "permitted", "projected"),
    [
        (1_000, 1, 100, False, 1_100),
        (1_000, -1, 100, True, 900),
        (-1_000, -1, 100, False, -1_100),
        (-1_000, 1, 100, True, -900),
        (950, 1, 50, True, 1_000),
        (950, 1, 51, False, 1_001),
        (100, -1, 150, True, -50),
    ],
)
def test_task_024_inventory_risk_uses_complete_fill_projection(
    inventory: int,
    side: int,
    quantity: int,
    permitted: bool,
    projected: int,
) -> None:
    decision = InventoryRiskLimit(1_000).evaluate(
        current_inventory=inventory,
        side=side,
        quantity=quantity,
    )

    assert decision.permitted is permitted
    assert decision.projected_inventory == projected
    assert decision.reason is (None if permitted else RiskDecisionReason.PROJECTED_INVENTORY_LIMIT)


@pytest.mark.parametrize(
    ("inventory", "side", "quantity"),
    [(1_001, 1, 1), (0, 0, 1), (0, True, 1), (0, 1, 0), (0, 1, True)],
)
def test_task_024_invalid_risk_inputs_fail_with_stable_code(
    inventory: int, side: int, quantity: int
) -> None:
    with pytest.raises(SimulationError) as captured:
        InventoryRiskLimit(1_000).evaluate(
            current_inventory=inventory,
            side=side,
            quantity=quantity,
        )
    assert captured.value.code is ErrorCode.INVENTORY_LIMIT


def test_task_024_accounting_enforces_inventory_limit_atomically() -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    order = _order_after_fill(price4=0, original_quantity=101)
    before = ledger.snapshot()

    with pytest.raises(SimulationError) as captured:
        ledger.record_queue_fill(_queue_fill(order, quantity=101), order, mark_mid2=0)

    assert captured.value.code is ErrorCode.INVENTORY_LIMIT
    assert ledger.snapshot() == before


@pytest.mark.parametrize(
    ("fee_rate", "expected_fee", "expected_pnl"),
    [(-2_000, -20_000, 20_000), (3_000, 30_000, -30_000)],
)
def test_task_024_signed_maker_fee_direction(
    fee_rate: int, expected_fee: int, expected_pnl: int
) -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=fee_rate, inventory_limit=10)
    accounted = _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=10_000,
        quantity=10,
        mark_mid2=20_000,
        message_index=2,
    )

    assert accounted.fee_microusd == expected_fee
    assert ledger.snapshot().marked_pnl_microusd == expected_pnl


@pytest.mark.parametrize("fee_rate", [-1_000_001, 1_000_001, True])
def test_task_024_invalid_maker_fee_has_stable_cost_error(fee_rate: int) -> None:
    with pytest.raises(SimulationError) as captured:
        AccountingLedger(maker_fee_microusd_per_share=fee_rate, inventory_limit=10)
    assert captured.value.code is ErrorCode.COST


@pytest.mark.parametrize(
    ("price4", "quantity", "mark_mid2"),
    [
        ((1 << 32) - 1, 30_000_000, 0),
        (0, 10_000_000_000_000, 0),
    ],
)
def test_task_024_cash_and_fee_overflow_leave_ledger_unchanged(
    price4: int, quantity: int, mark_mid2: int
) -> None:
    fee_rate = 1_000_000 if price4 == 0 else 0
    ledger = AccountingLedger(
        maker_fee_microusd_per_share=fee_rate,
        inventory_limit=MAX_INT64,
    )
    order = _order_after_fill(price4=price4, original_quantity=quantity)
    before = ledger.snapshot()

    with pytest.raises(SimulationError) as captured:
        ledger.record_queue_fill(
            _queue_fill(order, quantity=quantity),
            order,
            mark_mid2=mark_mid2,
        )

    assert captured.value.code is ErrorCode.COST
    assert ledger.snapshot() == before


def test_task_024_mark_overflow_leaves_existing_accounting_unchanged() -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=MAX_INT64)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=0,
        quantity=MAX_INT64,
        mark_mid2=0,
        message_index=2,
    )
    before = ledger.snapshot()

    with pytest.raises(SimulationError) as captured:
        ledger.update_mark(1, 1)

    assert captured.value.code is ErrorCode.COST
    assert ledger.snapshot() == before


def test_task_024_duplicate_and_out_of_order_fills_are_atomic() -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=1_000)
    order = _order_after_fill(original_quantity=100)
    fill = _queue_fill(order, quantity=100)
    ledger.record_queue_fill(fill, order, mark_mid2=20_000)
    before = ledger.snapshot()

    with pytest.raises(SimulationError) as duplicate:
        ledger.record_queue_fill(fill, order, mark_mid2=20_000)
    assert duplicate.value.code is ErrorCode.SIMULATION_ANOMALY
    assert ledger.snapshot() == before

    fresh = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=1_000)
    final_order = _order_after_fill(original_quantity=100)
    with pytest.raises(SimulationError) as out_of_order:
        fresh.record_queue_fill(
            _queue_fill(final_order, quantity=60),
            final_order,
            mark_mid2=20_000,
        )
    assert out_of_order.value.code is ErrorCode.SIMULATION_ANOMALY
    assert fresh.snapshot().passive_fill_count == 0


def test_task_024_short_inventory_liquidates_at_last_visible_ask() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    ledger = AccountingLedger(maker_fee_microusd_per_share=3_000, inventory_limit=100)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=-1,
        price4=10_100,
        quantity=100,
        mark_mid2=20_100,
        message_index=2,
    )

    settlement = settle_session_end(
        machine,
        ledger,
        session_end_timestamp_ns=200,
        last_quotes=(TerminalQuote(1, 190, 10_000, 10_080),),
        taker_fee_microusd_per_share=3_000,
    )

    assert settlement.liquidations[0].side == 1
    assert settlement.liquidations[0].price4 == 10_080
    assert settlement.accounting.symbols[0].inventory == 0


def test_task_024_locked_terminal_quote_is_valid() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=10)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=10_000,
        quantity=10,
        mark_mid2=20_100,
        message_index=2,
    )

    settlement = settle_session_end(
        machine,
        ledger,
        session_end_timestamp_ns=200,
        last_quotes=(TerminalQuote(1, 190, 10_050, 10_050),),
        taker_fee_microusd_per_share=0,
    )

    assert settlement.liquidations[0].price4 == 10_050
    assert settlement.accounting.marked_pnl_microusd == 50_000


def test_task_024_session_end_expires_pending_and_active_orders() -> None:
    machine = OrderStateMachine(submission_latency_ns=10, cancellation_latency_ns=0)
    machine.before_market_event(100, 1)
    machine.submit(OrderRequest(1, 1, None, 100, 1, 1, 10_000, 100))
    machine.before_market_event(110, 2)
    machine.after_market_timestamp(110)
    machine.before_market_event(120, 3)
    machine.submit(OrderRequest(2, 3, None, 120, 1, -1, 10_100, 100))
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)

    settlement = settle_session_end(
        machine,
        ledger,
        session_end_timestamp_ns=125,
        last_quotes=(),
        taker_fee_microusd_per_share=3_000,
    )

    assert [item.simulated_order_id for item in settlement.expired_orders] == [1, 2]
    assert all(machine.order(order_id).state is OrderState.EXPIRED for order_id in (1, 2))


@pytest.mark.parametrize(
    "quotes",
    [
        (),
        (TerminalQuote(1, 190, 10_100, 10_000),),
        (TerminalQuote(1, 201, 10_000, 10_100),),
    ],
)
def test_task_024_invalid_terminal_quote_fails_before_expiry_or_accounting(
    quotes: tuple[TerminalQuote, ...],
) -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    machine.before_market_event(100, 1)
    machine.submit(OrderRequest(2, 1, None, 100, 1, -1, 10_100, 10))
    machine.after_market_timestamp(100)
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=10_000,
        quantity=10,
        mark_mid2=20_100,
        message_index=2,
    )
    accounting_before = ledger.snapshot()
    order_before = machine.order(2)

    with pytest.raises(SimulationError):
        settle_session_end(
            machine,
            ledger,
            session_end_timestamp_ns=200,
            last_quotes=quotes,
            taker_fee_microusd_per_share=3_000,
        )

    assert ledger.snapshot() == accounting_before
    assert machine.order(2) == order_before


def test_task_024_zero_fill_day_has_valid_zero_metrics_and_no_price_requirement() -> None:
    machine = OrderStateMachine(submission_latency_ns=0, cancellation_latency_ns=0)
    ledger = AccountingLedger(maker_fee_microusd_per_share=-2_000, inventory_limit=1_000)

    settlement = settle_session_end(
        machine,
        ledger,
        session_end_timestamp_ns=200,
        last_quotes=(),
        taker_fee_microusd_per_share=3_000,
    )
    metrics = accounting_metrics(settlement.accounting)

    assert metrics.passive_fill_count == 0
    assert metrics.liquidation_count == 0
    assert metrics.cash_microusd == 0
    assert metrics.marked_pnl_microusd == 0
    assert metrics.ending_inventory_by_symbol == ()
    assert metrics.reconciled
    assert metrics.settled


@pytest.mark.parametrize("quantity", range(1, 65))
@pytest.mark.parametrize("side", [-1, 1])
def test_task_024_generated_integer_reconciliation_property(quantity: int, side: int) -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=-17, inventory_limit=64)
    price4 = 10_000 + side * 3
    mark_mid2 = 20_010
    _record_direct_fill(
        ledger,
        order_id=quantity,
        symbol_id=1,
        side=side,
        price4=price4,
        quantity=quantity,
        mark_mid2=mark_mid2,
        message_index=quantity + 1,
    )
    snapshot = ledger.snapshot()
    metrics = accounting_metrics(snapshot)

    assert snapshot.symbols[0].inventory == side * quantity
    assert snapshot.marked_pnl_microusd == (
        snapshot.cash_microusd + side * quantity * mark_mid2 * 50
    )
    assert metrics.marked_pnl_microusd == (
        metrics.passive_spread_capture_microusd
        + metrics.inventory_mark_to_market_microusd
        + metrics.terminal_liquidation_slippage_microusd
        + metrics.fee_contribution_microusd
    )


def test_task_024_marks_and_inventory_are_isolated_by_symbol() -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=10_000,
        quantity=10,
        mark_mid2=20_100,
        message_index=2,
    )
    _record_direct_fill(
        ledger,
        order_id=2,
        symbol_id=2,
        side=-1,
        price4=20_100,
        quantity=20,
        mark_mid2=40_100,
        message_index=3,
    )
    before = ledger.snapshot()
    ledger.update_mark(1, 20_120)
    after = ledger.snapshot()

    assert [(row.symbol_id, row.inventory) for row in after.symbols] == [(1, 10), (2, -20)]
    assert (
        after.inventory_mark_to_market_microusd - before.inventory_mark_to_market_microusd == 10_000
    )
    assert MAX_MID2 == 2 * ((1 << 32) - 1)


@pytest.mark.parametrize(
    ("value", "validator", "code"),
    [
        (0, accounting_module._validate_symbol_id, ErrorCode.UNKNOWN_SYMBOL),
        (True, accounting_module._validate_symbol_id, ErrorCode.UNKNOWN_SYMBOL),
        (-1, accounting_module._validate_mark_mid2, ErrorCode.PRICE),
        (MAX_MID2 + 1, accounting_module._validate_mark_mid2, ErrorCode.PRICE),
    ],
)
def test_task_030_critical_accounting_primitive_boundaries(
    value: object, validator: Any, code: ErrorCode
) -> None:
    with pytest.raises(SimulationError) as captured:
        validator(value)
    assert captured.value.code is code


@pytest.mark.parametrize("inventory_limit", [0, True, MAX_INT64 + 1])
def test_task_030_critical_accounting_rejects_invalid_inventory_limits(
    inventory_limit: int,
) -> None:
    with pytest.raises(SimulationError) as captured:
        AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=inventory_limit)
    assert captured.value.code is ErrorCode.INVENTORY_LIMIT


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"simulated_order_id": 2}, ErrorCode.SIMULATION_ANOMALY),
        ({"market_message_index": -1}, ErrorCode.SIMULATION_ANOMALY),
        ({"timestamp_ns": -1}, ErrorCode.TIMESTAMP),
        ({"quantity": 0}, ErrorCode.QUANTITY),
        ({"queue_ahead_before": -1}, ErrorCode.QUEUE_STATE),
        ({"queue_ahead_before": 0, "queue_ahead_after": 1}, ErrorCode.QUEUE_STATE),
    ],
)
def test_task_030_critical_accounting_rejects_malformed_queue_fills(
    changes: dict[str, int], code: ErrorCode
) -> None:
    order = _order_after_fill()
    fill = replace(_queue_fill(order, quantity=100), **changes)
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    with pytest.raises(SimulationError) as captured:
        ledger.record_queue_fill(fill, order, mark_mid2=20_000)
    assert captured.value.code is code
    assert ledger.snapshot().passive_fill_count == 0


@pytest.mark.parametrize(
    ("trade", "code"),
    [
        (object(), ErrorCode.SIMULATION_ANOMALY),
        (accounting_module.TerminalTrade(1, 0, 10_000, 1, 20_000, 100), ErrorCode.INVENTORY_LIMIT),
        (accounting_module.TerminalTrade(1, 1, -1, 1, 20_000, 100), ErrorCode.PRICE),
        (accounting_module.TerminalTrade(1, 1, 10_000, 0, 20_000, 100), ErrorCode.QUANTITY),
        (accounting_module.TerminalTrade(1, 1, 10_000, 1, 20_000, -1), ErrorCode.TIMESTAMP),
    ],
)
def test_task_030_critical_accounting_rejects_malformed_terminal_trades(
    trade: object, code: ErrorCode
) -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    with pytest.raises(SimulationError) as captured:
        ledger.prepare_terminal_accounting((trade,), taker_fee_microusd_per_share=0)  # type: ignore[arg-type]
    assert captured.value.code is code


def test_task_030_critical_accounting_terminal_state_guards_are_atomic() -> None:
    order = _order_after_fill()
    fill = _queue_fill(order, quantity=100)
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    plan = ledger.prepare_terminal_accounting((), taker_fee_microusd_per_share=0)
    ledger.commit_terminal_accounting(plan)

    for operation in (
        lambda: ledger.update_mark(1, 20_000),
        lambda: ledger.record_queue_fill(fill, order, mark_mid2=20_000),
        lambda: ledger.prepare_terminal_accounting((), taker_fee_microusd_per_share=0),
        lambda: ledger.commit_terminal_accounting(plan),
    ):
        with pytest.raises(SimulationError) as captured:
            operation()
        assert captured.value.code is ErrorCode.SIMULATION_ANOMALY

    fresh = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    with pytest.raises(SimulationError) as captured:
        fresh.commit_terminal_accounting(plan)
    assert captured.value.code is ErrorCode.SIMULATION_ANOMALY


def test_task_030_critical_accounting_requires_exact_terminal_trades() -> None:
    ledger = AccountingLedger(maker_fee_microusd_per_share=0, inventory_limit=100)
    _record_direct_fill(
        ledger,
        order_id=1,
        symbol_id=1,
        side=1,
        price4=10_000,
        quantity=10,
        mark_mid2=20_100,
        message_index=2,
    )
    before = ledger.snapshot()
    with pytest.raises(SimulationError) as missing:
        ledger.prepare_terminal_accounting((), taker_fee_microusd_per_share=0)
    assert missing.value.code is ErrorCode.PRICE

    wrong = accounting_module.TerminalTrade(1, 1, 10_000, 10, 20_100, 200)
    with pytest.raises(SimulationError) as wrong_direction:
        ledger.prepare_terminal_accounting((wrong,), taker_fee_microusd_per_share=0)
    assert wrong_direction.value.code is ErrorCode.INVENTORY_LIMIT

    valid = accounting_module.TerminalTrade(1, -1, 10_000, 10, 20_100, 200)
    with pytest.raises(SimulationError) as duplicate:
        ledger.prepare_terminal_accounting((valid, valid), taker_fee_microusd_per_share=0)
    assert duplicate.value.code is ErrorCode.SIMULATION_ANOMALY
    assert ledger.snapshot() == before
