"""TASK-027 deterministic scenario orchestration and temporal metric tests."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from itchlab_research.config import SimulationConfig, parse_config
from itchlab_research.simulation import (
    SimulationDayInput,
    SimulationSymbol,
    required_scenarios,
    temporal_metrics,
)
from itchlab_research.simulation.runner import run_scenario
from itchlab_research.strategies import CausalIntensityCalibrator, IntensityCalibration


def _config() -> SimulationConfig:
    parsed = parse_config(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_manifest": "runs/dataset/example/dataset-manifest.json",
                "prediction_manifest": None,
                "strategy": {
                    "name": "inventory_aware_avellaneda_stoikov",
                    "decision_interval_ns": 100,
                    "max_prediction_age_ns": 500,
                    "order_quantity": 100,
                    "inventory_limit": 1000,
                    "gamma": 0.1,
                    "volatility_window_ns": 1000,
                    "risk_horizon_seconds": 10,
                    "signal_weight_ticks": 0.0,
                    "max_signal_ticks": 2.0,
                },
                "execution": {
                    "passive_only": True,
                    "submission_latency_ns": 0,
                    "cancellation_latency_ns": 0,
                    "maker_fee_microusd_per_share": -2000,
                    "taker_fee_microusd_per_share": 3000,
                    "queue_policy": "known_orders_conservative",
                    "max_queue_anomalies": 0,
                    "terminal_liquidation": "cross_visible_spread",
                },
                "seed": 1,
            }
        ),
        "simulation",
    )
    assert isinstance(parsed, SimulationConfig)
    return parsed


def _calibration() -> IntensityCalibration:
    calibrator = CausalIntensityCalibrator(symbols=["AAPL"], training_dates=[date(2019, 1, 1)])
    calibrator.record_exposure(date(2019, 1, 1), "AAPL", 0, 1_000_000_000)
    calibrator.record_exposure(date(2019, 1, 1), "AAPL", 1, 1_000_000_000)
    calibrator.record_execution(date(2019, 1, 1), "AAPL", 0, 10)
    return calibrator.finalise()


def _event(
    message_index: int,
    timestamp_ns: int,
    kind: str,
    *,
    reference: int | None = None,
    side: int | None = None,
    price4: int | None = None,
) -> dict[str, object]:
    quantity = 100 if reference is not None else None
    return {
        "symbol": "AAPL",
        "message_index": message_index,
        "timestamp_ns": timestamp_ns,
        "symbol_id": 1,
        "event_kind": kind,
        "primary_reference": reference,
        "secondary_reference": None,
        "side": side,
        "price4": price4,
        "quantity": quantity,
        "remaining_quantity": quantity,
        "execution_price4": None,
        "in_session": True,
    }


def _snapshot(message_index: int, timestamp_ns: int, bid: int, ask: int) -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "symbol_id": 1,
        "message_index": message_index,
        "timestamp_ns": timestamp_ns,
        "bid_price4_1": bid,
        "ask_price4_1": ask,
    }


def test_required_grid_is_exact_three_by_two() -> None:
    grid = required_scenarios()

    assert len(grid) == 6
    assert {(item.submission_latency_ns, item.maker_fee_microusd_per_share) for item in grid} == {
        (0, -2000),
        (0, 3000),
        (100_000, -2000),
        (100_000, 3000),
        (1_000_000, -2000),
        (1_000_000, 3000),
    }
    assert all(item.submission_latency_ns == item.cancellation_latency_ns for item in grid)
    assert {item.taker_fee_microusd_per_share for item in grid} == {3000}


def test_temporal_metrics_cover_drawdown_turnover_and_partial_markouts() -> None:
    metrics = temporal_metrics(
        [5, 8, 3, 10, 4],
        [100, 250, 50],
        [20, None, -5],
    )

    assert metrics.max_drawdown_microusd == 6
    assert metrics.turnover_microusd == 400
    assert metrics.adverse_selection_100ms_microusd == 15
    assert metrics.adverse_selection_observation_count == 2
    assert metrics.adverse_selection_eligible_fill_count == 3
    assert metrics.adverse_selection_coverage == 2 / 3


def test_runner_publishes_valid_zero_fill_metrics_and_expires_orders() -> None:
    day = SimulationDayInput(
        trading_date=date(2019, 1, 2),
        session_start_ns=0,
        session_end_ns=300,
        symbols=(SimulationSymbol("AAPL", 1, 100),),
        events=(
            _event(1, 0, "add", reference=1, side=1, price4=10_000),
            _event(2, 1, "add", reference=2, side=-1, price4=10_200),
            _event(3, 100, "trading_state"),
            _event(4, 200, "trading_state"),
        ),
        snapshots=(
            _snapshot(1, 0, 10_000, 10_200),
            _snapshot(2, 1, 10_000, 10_200),
            _snapshot(3, 100, 10_100, 10_300),
            _snapshot(4, 200, 10_100, 10_300),
        ),
    )

    result = run_scenario(
        (day,),
        _config(),
        _calibration(),
        required_scenarios()[0],
        strategy_name="inventory_aware_avellaneda_stoikov",
    )

    assert len(result.orders) == 2
    assert {item["state"] for item in result.orders} == {"expired"}
    assert result.fills == ()
    assert result.metrics["passive_fill_count"] == 0
    assert result.metrics["marked_pnl_microusd"] == 0
    assert result.metrics["max_drawdown_microusd"] == 0
    assert result.metrics["turnover_microusd"] == 0
    assert result.metrics["adverse_selection_100ms_microusd"] is None
    assert result.metrics["settled"] is True


def test_runner_identifiers_are_unique_across_scenario_days() -> None:
    first_day = SimulationDayInput(
        trading_date=date(2019, 1, 2),
        session_start_ns=0,
        session_end_ns=300,
        symbols=(SimulationSymbol("AAPL", 1, 100),),
        events=(
            _event(1, 0, "add", reference=1, side=1, price4=10_000),
            _event(2, 1, "add", reference=2, side=-1, price4=10_200),
            _event(3, 100, "trading_state"),
        ),
        snapshots=(
            _snapshot(1, 0, 10_000, 10_200),
            _snapshot(2, 1, 10_000, 10_200),
            _snapshot(3, 100, 10_000, 10_200),
        ),
    )
    second_day = replace(first_day, trading_date=date(2019, 1, 3))

    result = run_scenario(
        (first_day, second_day),
        _config(),
        _calibration(),
        required_scenarios()[0],
        strategy_name="inventory_aware_avellaneda_stoikov",
    )

    identifiers = [item["simulated_order_id"] for item in result.orders]
    assert identifiers == list(range(len(identifiers)))


def test_it_010_runner_reconciles_fill_liquidation_and_markout() -> None:
    first_execution = _event(4, 150, "execute", reference=1, side=1, price4=10_000)
    first_execution.update(secondary_reference=10, remaining_quantity=0)
    simulated_fill_execution = _event(
        6,
        200,
        "execute",
        reference=3,
        side=1,
        price4=10_000,
    )
    simulated_fill_execution.update(secondary_reference=11, remaining_quantity=0)
    day = SimulationDayInput(
        trading_date=date(2019, 1, 2),
        session_start_ns=0,
        session_end_ns=100_000_300,
        symbols=(SimulationSymbol("AAPL", 1, 100),),
        events=(
            _event(1, 0, "add", reference=1, side=1, price4=10_000),
            _event(2, 1, "add", reference=2, side=-1, price4=10_200),
            _event(3, 100, "trading_state"),
            first_execution,
            _event(5, 151, "add", reference=3, side=1, price4=10_000),
            simulated_fill_execution,
            _event(7, 100_000_200, "trading_state"),
        ),
        snapshots=(
            _snapshot(1, 0, 10_000, 10_200),
            _snapshot(2, 1, 10_000, 10_200),
            _snapshot(3, 100, 10_000, 10_200),
            _snapshot(7, 100_000_200, 9_900, 10_100),
        ),
    )

    result = run_scenario(
        (day,),
        _config(),
        _calibration(),
        required_scenarios()[0],
        strategy_name="inventory_aware_avellaneda_stoikov",
    )

    assert len(result.fills) == 1
    assert result.fills[0]["fill_id"] == 0
    assert result.fills[0]["future_mid2"] == 20_000
    assert result.fills[0]["adverse_selection_100ms_microusd"] == 1_000_000
    assert len(result.liquidations) == 1
    assert result.liquidations[0]["inventory_after"] == 0
    assert result.metrics["passive_fill_count"] == 1
    assert result.metrics["passive_fill_quantity"] == 100
    assert result.metrics["turnover_microusd"] == 199_000_000
    assert result.metrics["marked_pnl_microusd"] == -1_100_000
    assert result.metrics["adverse_selection_100ms_microusd"] == 1_000_000
    assert result.metrics["adverse_selection_coverage"] == 1.0
    assert result.metrics["reconciled"] is True
    assert result.metrics["settled"] is True


def test_full_runner_signal_weight_zero_matches_inventory_control() -> None:
    day = SimulationDayInput(
        trading_date=date(2019, 1, 2),
        session_start_ns=0,
        session_end_ns=300,
        symbols=(SimulationSymbol("AAPL", 1, 100),),
        events=(
            _event(1, 0, "add", reference=1, side=1, price4=10_000),
            _event(2, 1, "add", reference=2, side=-1, price4=10_200),
            _event(3, 100, "trading_state"),
            _event(4, 200, "trading_state"),
        ),
        snapshots=(
            _snapshot(1, 0, 10_000, 10_200),
            _snapshot(2, 1, 10_000, 10_200),
            _snapshot(3, 100, 10_100, 10_300),
            _snapshot(4, 200, 10_100, 10_300),
        ),
    )
    scenario = required_scenarios()[0]

    baseline = run_scenario(
        (day,),
        _config(),
        _calibration(),
        scenario,
        strategy_name="inventory_aware_avellaneda_stoikov",
    )
    signal = run_scenario(
        (day,),
        _config(),
        _calibration(),
        scenario,
        strategy_name="signal_adjusted_avellaneda_stoikov",
        signal_weight_ticks=0.0,
        experiment_id="experiment",
    )

    def economic_orders(rows: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
        return tuple(
            {key: value for key, value in row.items() if key != "strategy_name"} for row in rows
        )

    assert economic_orders(baseline.orders) == economic_orders(signal.orders)
    assert baseline.metrics == signal.metrics
    assert baseline.fills == signal.fills == ()
