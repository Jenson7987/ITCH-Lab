"""Deterministic market-first orchestration for conservative scenario simulation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, replace
from typing import Any, cast

from itchlab_research.config import SimulationConfig
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.accounting import AccountingLedger
from itchlab_research.simulation.liquidation import TerminalQuote, settle_session_end
from itchlab_research.simulation.metrics import accounting_metrics, temporal_metrics
from itchlab_research.simulation.models import (
    ExecutionScenario,
    ScenarioResult,
    SimulationDayInput,
    StrategyName,
)
from itchlab_research.simulation.order import OrderRequest, SimulatedOrder
from itchlab_research.simulation.queue_model import QueueFill, VisibleQueueModel
from itchlab_research.simulation.state_machine import OrderStateMachine
from itchlab_research.strategies import (
    IntensityCalibration,
    InventoryAwareAvellanedaStoikov,
    PredictionModelName,
    QuoteProposal,
    SignalAdjustedAvellanedaStoikov,
    SignalAdjustedDecision,
)

_MARKOUT_HORIZON_NS = 100_000_000


def _fail(code: ErrorCode, message: str) -> SimulationError:
    return SimulationError(code, message)


def _snapshot_by_key(day: SimulationDayInput) -> dict[tuple[int, int], dict[str, object]]:
    result: dict[tuple[int, int], dict[str, object]] = {}
    for raw in day.snapshots:
        row = dict(raw)
        key = (cast(int, row.get("symbol_id")), cast(int, row.get("message_index")))
        if key in result:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Snapshot keys are duplicated.")
        result[key] = row
    return result


def _prediction_index(decision: object) -> int | None:
    if not isinstance(decision, SignalAdjustedDecision) or decision.prediction is None:
        return None
    key = decision.prediction.key
    return None if key is None else key.message_index


def _quote_pair(decision: object) -> tuple[QuoteProposal | None, QuoteProposal | None]:
    if isinstance(decision, SignalAdjustedDecision):
        return decision.bid, decision.ask
    return cast(Any, decision).bid, cast(Any, decision).ask


def _refresh_quotes(
    state: OrderStateMachine,
    *,
    decision: object,
    timestamp_ns: int,
    message_index: int,
    next_order_id: int,
) -> int:
    bid, ask = _quote_pair(decision)
    symbol_id = (
        cast(Any, decision).baseline.symbol_id
        if isinstance(decision, SignalAdjustedDecision)
        else cast(Any, decision).symbol_id
    )
    prediction_message_index = _prediction_index(decision)
    for side, proposal in ((1, bid), (-1, ask)):
        current = state.occupied_order(symbol_id, side)
        desired_price = None if proposal is None else proposal.price4
        if current is not None and current.price4 != desired_price:
            if current.cancel_requested_ns is None:
                state.request_cancel(
                    current.simulated_order_id,
                    timestamp_ns=timestamp_ns,
                    message_index=message_index,
                )
            continue
        if current is not None or proposal is None:
            continue
        state.submit(
            OrderRequest(
                simulated_order_id=next_order_id,
                decision_message_index=message_index,
                prediction_message_index=prediction_message_index,
                requested_timestamp_ns=timestamp_ns,
                symbol_id=symbol_id,
                side=side,
                price4=proposal.price4,
                quantity=proposal.quantity,
            )
        )
        next_order_id += 1
    return next_order_id


def _account_fill_row(
    *,
    day: SimulationDayInput,
    scenario: ExecutionScenario,
    strategy_name: StrategyName,
    queue_fill: QueueFill,
    order: SimulatedOrder,
    ledger: AccountingLedger,
    mark_mid2: int,
    scenario_fill_id: int,
) -> dict[str, Any]:
    accounted = ledger.record_queue_fill(queue_fill, order, mark_mid2=mark_mid2)
    return {
        "scenario_id": scenario.scenario_id,
        "strategy_name": strategy_name,
        "trading_date": day.trading_date,
        "fill_id": scenario_fill_id,
        "simulated_order_id": accounted.simulated_order_id,
        "market_message_index": accounted.market_message_index,
        "timestamp_ns": accounted.timestamp_ns,
        "symbol_id": order.symbol_id,
        "side": order.side,
        "price4": accounted.price4,
        "quantity": accounted.quantity,
        "fee_microusd": accounted.fee_microusd,
        "cash_delta_microusd": accounted.cash_delta_microusd,
        "inventory_after": accounted.inventory_after,
        "fill_mid2": mark_mid2,
        "future_mid2": None,
        "adverse_selection_100ms_microusd": None,
    }


def _apply_markouts(
    fills: list[dict[str, Any]], quote_timeline: dict[int, list[tuple[int, int]]]
) -> None:
    for fill in fills:
        horizon = cast(int, fill["timestamp_ns"]) + _MARKOUT_HORIZON_NS
        future_mid2 = next(
            (
                mid2
                for timestamp_ns, mid2 in quote_timeline[cast(int, fill["symbol_id"])]
                if timestamp_ns >= horizon
            ),
            None,
        )
        if future_mid2 is None:
            continue
        fill["future_mid2"] = future_mid2
        fill["adverse_selection_100ms_microusd"] = (
            cast(int, fill["side"])
            * (cast(int, fill["fill_mid2"]) - future_mid2)
            * cast(int, fill["quantity"])
            * 50
        )


def _day_metrics(
    *,
    day: SimulationDayInput,
    ledger: AccountingLedger,
    fills: list[dict[str, Any]],
    equity: list[dict[str, Any]],
) -> dict[str, Any]:
    accounting = accounting_metrics(ledger)
    liquidation_notionals = [item.price4 * item.quantity * 100 for item in ledger.liquidations]
    temporal = temporal_metrics(
        (cast(int, row["marked_pnl_microusd"]) for row in equity),
        (
            *(cast(int, row["price4"]) * cast(int, row["quantity"]) * 100 for row in fills),
            *liquidation_notionals,
        ),
        (cast(int | None, row["adverse_selection_100ms_microusd"]) for row in fills),
    )
    result = asdict(accounting)
    result.update(asdict(temporal))
    result["trading_date"] = day.trading_date.isoformat()
    result["ending_inventory_by_symbol"] = dict(accounting.ending_inventory_by_symbol)
    result["max_abs_inventory_by_symbol"] = dict(accounting.max_abs_inventory_by_symbol)
    return result


def _aggregate_metrics(
    daily: Iterable[dict[str, Any]],
    fills: tuple[dict[str, Any], ...],
    equity: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    days = tuple(daily)
    additive = (
        "passive_fill_count",
        "passive_fill_quantity",
        "liquidation_count",
        "liquidation_quantity",
        "gross_cash_microusd",
        "maker_fee_microusd",
        "taker_fee_microusd",
        "signed_fee_microusd",
        "fee_contribution_microusd",
        "cash_microusd",
        "passive_spread_capture_microusd",
        "inventory_mark_to_market_microusd",
        "terminal_liquidation_slippage_microusd",
        "marked_pnl_microusd",
        "turnover_microusd",
    )
    result: dict[str, Any] = {name: sum(cast(int, day[name]) for day in days) for name in additive}
    offset = 0
    concatenated_equity: list[int] = []
    for day in days:
        day_name = cast(str, day["trading_date"])
        day_points = [
            cast(int, row["marked_pnl_microusd"])
            for row in equity
            if (
                row["trading_date"].isoformat()
                if hasattr(row["trading_date"], "isoformat")
                else cast(str, row["trading_date"])
            )
            == day_name
        ]
        concatenated_equity.extend(offset + value for value in day_points)
        offset += cast(int, day["marked_pnl_microusd"])
    adverse_values = [cast(int | None, row["adverse_selection_100ms_microusd"]) for row in fills]
    temporal = temporal_metrics(concatenated_equity, (), adverse_values)
    result.update(
        {
            "trading_days": len(days),
            "max_drawdown_microusd": temporal.max_drawdown_microusd,
            "adverse_selection_100ms_microusd": (temporal.adverse_selection_100ms_microusd),
            "adverse_selection_observation_count": (temporal.adverse_selection_observation_count),
            "adverse_selection_eligible_fill_count": (
                temporal.adverse_selection_eligible_fill_count
            ),
            "adverse_selection_coverage": temporal.adverse_selection_coverage,
            "reconciled": all(cast(bool, day["reconciled"]) for day in days),
            "settled": all(cast(bool, day["settled"]) for day in days),
        }
    )
    max_inventory: dict[int, int] = {}
    for day in days:
        for symbol_id, maximum in cast(dict[int, int], day["max_abs_inventory_by_symbol"]).items():
            max_inventory[int(symbol_id)] = max(max_inventory.get(int(symbol_id), 0), maximum)
    result["max_abs_inventory_by_symbol"] = max_inventory
    return result


def run_scenario(
    days: Iterable[SimulationDayInput],
    config: SimulationConfig,
    calibration: IntensityCalibration,
    scenario: ExecutionScenario,
    *,
    strategy_name: StrategyName,
    signal_weight_ticks: float = 0.0,
    experiment_id: str | None = None,
    model_name: PredictionModelName = "prior",
) -> ScenarioResult:
    """Run one strategy/scenario over ordered days without publication side effects."""
    day_values = tuple(days)
    if not day_values:
        raise _fail(ErrorCode.EMPTY_DATASET, "Simulation has no input days.")
    if strategy_name == "signal_adjusted_avellaneda_stoikov" and experiment_id is None:
        raise _fail(ErrorCode.PREDICTION_KEY, "Signal simulation requires an experiment identity.")

    all_orders: list[dict[str, Any]] = []
    all_fills: list[dict[str, Any]] = []
    all_liquidations: list[dict[str, Any]] = []
    all_equity: list[dict[str, Any]] = []
    all_diagnostics: list[dict[str, Any]] = []
    daily_metrics: list[dict[str, Any]] = []
    next_scenario_order_id = 0
    next_scenario_fill_id = 0
    next_scenario_liquidation_id = 0

    for day in day_values:
        state = OrderStateMachine(
            submission_latency_ns=scenario.submission_latency_ns,
            cancellation_latency_ns=scenario.cancellation_latency_ns,
        )
        queue = VisibleQueueModel(state, max_queue_anomalies=config.execution.max_queue_anomalies)
        ledger = AccountingLedger(
            maker_fee_microusd_per_share=scenario.maker_fee_microusd_per_share,
            inventory_limit=config.strategy.inventory_limit,
        )
        snapshots = _snapshot_by_key(day)
        symbols = {symbol.symbol_id: symbol for symbol in day.symbols}
        strategies: dict[int, Any] = {}
        next_decision = {
            symbol.symbol_id: day.session_start_ns + config.strategy.decision_interval_ns
            for symbol in day.symbols
        }
        for symbol in day.symbols:
            selected_config = replace(
                config.strategy,
                name=strategy_name,
                signal_weight_ticks=signal_weight_ticks,
            )
            if strategy_name == "inventory_aware_avellaneda_stoikov":
                strategies[symbol.symbol_id] = InventoryAwareAvellanedaStoikov(
                    selected_config,
                    symbol=symbol.symbol,
                    symbol_id=symbol.symbol_id,
                    tick_size4=symbol.tick_size4,
                    session_start_ns=day.session_start_ns,
                    session_end_ns=day.session_end_ns,
                    calibration=calibration,
                )
            else:
                selected_predictions = tuple(
                    item for item in day.predictions if item.key.symbol_id == symbol.symbol_id
                )
                strategies[symbol.symbol_id] = SignalAdjustedAvellanedaStoikov(
                    selected_config,
                    symbol=symbol.symbol,
                    symbol_id=symbol.symbol_id,
                    tick_size4=symbol.tick_size4,
                    trading_date=day.trading_date,
                    session_start_ns=day.session_start_ns,
                    session_end_ns=day.session_end_ns,
                    calibration=calibration,
                    experiment_id=cast(str, experiment_id),
                    model_name=model_name,
                    predictions=selected_predictions,
                )

        fills: list[dict[str, Any]] = []
        equity: list[dict[str, Any]] = []
        last_quotes: dict[int, TerminalQuote] = {}
        quote_timeline: dict[int, list[tuple[int, int]]] = {
            symbol.symbol_id: [] for symbol in day.symbols
        }
        next_order_id = next_scenario_order_id
        events = tuple(sorted(day.events, key=lambda row: cast(int, row["message_index"])))
        for offset, raw in enumerate(events):
            event_result = queue.process_market_event(raw)
            event = event_result.event
            event_symbol = symbols.get(event.symbol_id)
            if event_symbol is None:
                raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Event symbol is outside simulation scope.")
            snapshot = snapshots.get((event.symbol_id, event.message_index))
            mark_mid2: int | None = None
            if snapshot is not None:
                bid = cast(int | None, snapshot.get("bid_price4_1"))
                ask = cast(int | None, snapshot.get("ask_price4_1"))
                if bid is not None and ask is not None:
                    if bid > ask:
                        raise _fail(ErrorCode.BOOK_CROSSED, "Visible simulation quote is crossed.")
                    mark_mid2 = bid + ask
                    ledger.update_mark(event.symbol_id, mark_mid2)
                    if day.session_start_ns <= event.timestamp_ns < day.session_end_ns:
                        strategies[event.symbol_id].observe_quote(
                            message_index=event.message_index,
                            timestamp_ns=event.timestamp_ns,
                            best_bid_price4=bid,
                            best_ask_price4=ask,
                        )
                        last_quotes[event.symbol_id] = TerminalQuote(
                            symbol_id=event.symbol_id,
                            timestamp_ns=event.timestamp_ns,
                            best_bid_price4=bid,
                            best_ask_price4=ask,
                        )
                        quote_timeline[event.symbol_id].append((event.timestamp_ns, mark_mid2))
            if event_result.fills:
                if mark_mid2 is None:
                    last = last_quotes.get(event.symbol_id)
                    if last is None:
                        raise _fail(ErrorCode.PRICE, "A passive fill has no causal midpoint mark.")
                    mark_mid2 = last.mid2
                for queue_fill in event_result.fills:
                    order = state.order(queue_fill.simulated_order_id)
                    fills.append(
                        _account_fill_row(
                            day=day,
                            scenario=scenario,
                            strategy_name=strategy_name,
                            queue_fill=queue_fill,
                            order=order,
                            ledger=ledger,
                            mark_mid2=mark_mid2,
                            scenario_fill_id=next_scenario_fill_id,
                        )
                    )
                    next_scenario_fill_id += 1
            if (
                cast(bool, raw.get("in_session", False))
                and mark_mid2 is not None
                and event.timestamp_ns >= next_decision[event.symbol_id]
            ):
                decision = strategies[event.symbol_id].decide(
                    decision_message_index=event.message_index,
                    timestamp_ns=event.timestamp_ns,
                    inventory_shares=ledger.inventory(event.symbol_id),
                )
                next_order_id = _refresh_quotes(
                    state,
                    decision=decision,
                    timestamp_ns=event.timestamp_ns,
                    message_index=event.message_index,
                    next_order_id=next_order_id,
                )
                elapsed = event.timestamp_ns - day.session_start_ns
                next_decision[event.symbol_id] = (
                    day.session_start_ns
                    + (elapsed // config.strategy.decision_interval_ns + 1)
                    * config.strategy.decision_interval_ns
                )
            snapshot_value = ledger.snapshot()
            equity.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "strategy_name": strategy_name,
                    "trading_date": day.trading_date,
                    "message_index": event.message_index,
                    "timestamp_ns": event.timestamp_ns,
                    "marked_pnl_microusd": snapshot_value.marked_pnl_microusd,
                    "cash_microusd": snapshot_value.cash_microusd,
                    "marked_inventory_value_microusd": (
                        snapshot_value.marked_inventory_value_microusd
                    ),
                }
            )
            next_timestamp = (
                None if offset + 1 == len(events) else cast(int, events[offset + 1]["timestamp_ns"])
            )
            if next_timestamp != event.timestamp_ns:
                queue.complete_market_timestamp(event.timestamp_ns)

        terminal_timestamp = min(day.session_end_ns - 1, 86_399_999_999_999)
        settlement = settle_session_end(
            state,
            ledger,
            session_end_timestamp_ns=terminal_timestamp,
            last_quotes=last_quotes.values(),
            taker_fee_microusd_per_share=scenario.taker_fee_microusd_per_share,
        )
        for item in settlement.liquidations:
            all_liquidations.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "strategy_name": strategy_name,
                    "trading_date": day.trading_date,
                    **asdict(item),
                    "liquidation_id": next_scenario_liquidation_id,
                }
            )
            next_scenario_liquidation_id += 1
        equity.append(
            {
                "scenario_id": scenario.scenario_id,
                "strategy_name": strategy_name,
                "trading_date": day.trading_date,
                "message_index": None,
                "timestamp_ns": terminal_timestamp,
                "marked_pnl_microusd": settlement.accounting.marked_pnl_microusd,
                "cash_microusd": settlement.accounting.cash_microusd,
                "marked_inventory_value_microusd": (
                    settlement.accounting.marked_inventory_value_microusd
                ),
            }
        )
        _apply_markouts(fills, quote_timeline)
        daily_metrics.append(_day_metrics(day=day, ledger=ledger, fills=fills, equity=equity))
        for order in state.orders:
            all_orders.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "strategy_name": strategy_name,
                    "trading_date": day.trading_date,
                    **asdict(order),
                    "state": order.state.value,
                    "rejection_reason": (
                        None if order.rejection_reason is None else order.rejection_reason.value
                    ),
                }
            )
        all_fills.extend(fills)
        all_equity.extend(equity)
        all_diagnostics.extend(
            {
                "scenario_id": scenario.scenario_id,
                "strategy_name": strategy_name,
                "trading_date": day.trading_date.isoformat(),
                "code": item.code.value,
                "message_index": item.message_index,
                "symbol_id": item.symbol_id,
                "simulated_order_id": item.simulated_order_id,
                "reason": item.reason,
            }
            for item in queue.diagnostics
        )
        if strategy_name == "signal_adjusted_avellaneda_stoikov":
            for symbol_id, strategy in strategies.items():
                all_diagnostics.extend(
                    {
                        "scenario_id": scenario.scenario_id,
                        "strategy_name": strategy_name,
                        "trading_date": day.trading_date.isoformat(),
                        "code": item.code.value,
                        "message_index": item.decision_message_index,
                        "symbol_id": symbol_id,
                        "simulated_order_id": None,
                        "reason": item.reason,
                    }
                    for item in strategy.prediction_diagnostics
                )
        next_scenario_order_id = next_order_id

    fill_values = tuple(all_fills)
    equity_values = tuple(all_equity)
    return ScenarioResult(
        scenario=scenario,
        strategy_name=strategy_name,
        signal_weight_ticks=signal_weight_ticks,
        orders=tuple(all_orders),
        fills=fill_values,
        liquidations=tuple(all_liquidations),
        equity=equity_values,
        daily_metrics=tuple(daily_metrics),
        metrics=_aggregate_metrics(daily_metrics, fill_values, equity_values),
        diagnostics=tuple(all_diagnostics),
    )


__all__ = ["run_scenario"]
