"""TASK-025 causal inventory-aware baseline strategy tests."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from itchlab_research.config import StrategyConfig
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation import OrderRequest, validate_order_request
from itchlab_research.simulation.order import MAX_UINT32
from itchlab_research.strategies import (
    BaselineDecision,
    CausalIntensityCalibrator,
    CausalVolatilityEstimator,
    IntensityCalibration,
    InventoryAwareAvellanedaStoikov,
    QuoteSuppressionReason,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRAINING_DAY = date(2019, 1, 30)
SECOND = 1_000_000_000


def _config(**overrides: object) -> StrategyConfig:
    values: dict[str, object] = {
        "name": "inventory_aware_avellaneda_stoikov",
        "decision_interval_ns": 100_000_000,
        "max_prediction_age_ns": 500_000_000,
        "order_quantity": 100,
        "inventory_limit": 1_000,
        "gamma": 0.1,
        "volatility_window_ns": 60 * SECOND,
        "risk_horizon_seconds": 10.0,
        "signal_weight_ticks": 0.0,
        "max_signal_ticks": 2.0,
    }
    values.update(overrides)
    return StrategyConfig(**values)  # type: ignore[arg-type]


def _calibration() -> IntensityCalibration:
    calibrator = CausalIntensityCalibrator(symbols=("AAPL",), training_dates=(TRAINING_DAY,))
    for distance, executions in enumerate((99, 49, 24)):
        calibrator.record_exposure(TRAINING_DAY, "AAPL", distance, 10 * SECOND)
        calibrator.record_execution(TRAINING_DAY, "AAPL", distance, executions)
    return calibrator.finalise()


def _strategy(
    *,
    config: StrategyConfig | None = None,
    tick_size4: int = 100,
    session_start_ns: int = 0,
    session_end_ns: int = 100 * SECOND,
) -> InventoryAwareAvellanedaStoikov:
    return InventoryAwareAvellanedaStoikov(
        _config() if config is None else config,
        symbol="AAPL",
        symbol_id=1,
        tick_size4=tick_size4,
        session_start_ns=session_start_ns,
        session_end_ns=session_end_ns,
        calibration=_calibration(),
    )


def _decision_row(name: str, decision: BaselineDecision) -> dict[str, object]:
    assert decision.volatility is not None
    return {
        "scenario": name,
        "inventory_shares": decision.inventory_shares,
        "inventory_units": decision.inventory_units,
        "sigma_squared": round(decision.volatility.sigma_squared, 12),
        "tau_seconds": round(decision.tau_seconds, 12),
        "kappa": round(decision.kappa, 12),
        "kappa_source": decision.kappa_source,
        "reservation_price_ticks": round(float(decision.reservation_price_ticks), 12),
        "half_spread_ticks": round(float(decision.half_spread_ticks), 12),
        "bid_price4": None if decision.bid is None else decision.bid.price4,
        "ask_price4": None if decision.ask is None else decision.ask.price4,
        "bid_suppression_reason": decision.bid_suppression_reason,
        "ask_suppression_reason": decision.ask_suppression_reason,
    }


def test_ut_strat_001_matches_inventory_skew_decision_table() -> None:
    strategy = _strategy()
    assert (
        strategy.observe_quote(
            message_index=1,
            timestamp_ns=SECOND,
            best_bid_price4=9_900,
            best_ask_price4=10_100,
        )
        is None
    )
    strategy.observe_quote(
        message_index=2,
        timestamp_ns=2 * SECOND,
        best_bid_price4=10_000,
        best_ask_price4=10_200,
    )

    decisions = [
        strategy.decide(
            decision_message_index=2,
            timestamp_ns=2 * SECOND,
            inventory_shares=inventory,
        )
        for inventory in (0, 200, -200)
    ]
    actual = [
        _decision_row(name, decision)
        for name, decision in zip(("flat", "long", "short"), decisions, strict=True)
    ]
    expected = json.loads(
        (
            REPOSITORY_ROOT
            / "tests"
            / "golden"
            / "simulation"
            / "task025-strategy-decision-table.json"
        ).read_text(encoding="utf-8")
    )

    assert actual == expected
    flat, long, short = decisions
    assert long.reservation_price_ticks < flat.reservation_price_ticks
    assert flat.reservation_price_ticks < short.reservation_price_ticks
    assert long.half_spread_ticks == flat.half_spread_ticks == short.half_spread_ticks


def test_task_025_first_observation_suppresses_both_sides_then_zero_variance_is_valid() -> None:
    strategy = _strategy()
    strategy.observe_quote(
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )

    first = strategy.decide(decision_message_index=1, timestamp_ns=SECOND, inventory_shares=0)
    assert first.volatility is None
    assert first.bid is first.ask is None
    assert first.bid_suppression_reason is QuoteSuppressionReason.INSUFFICIENT_VOLATILITY
    assert first.ask_suppression_reason is QuoteSuppressionReason.INSUFFICIENT_VOLATILITY

    later = strategy.decide(decision_message_index=2, timestamp_ns=2 * SECOND, inventory_shares=0)
    assert later.volatility is not None
    assert later.volatility.sigma_squared == 0.0
    assert later.bid is not None and later.ask is not None


def test_task_025_volatility_uses_half_open_window_and_actual_elapsed_seconds() -> None:
    estimator = CausalVolatilityEstimator(window_ns=2 * SECOND, tick_size4=100)
    estimator.observe_quote(
        message_index=1, timestamp_ns=0, best_bid_price4=9_900, best_ask_price4=10_100
    )
    estimator.observe_quote(
        message_index=2,
        timestamp_ns=SECOND,
        best_bid_price4=10_000,
        best_ask_price4=10_200,
    )
    estimator.observe_quote(
        message_index=3,
        timestamp_ns=2 * SECOND,
        best_bid_price4=10_200,
        best_ask_price4=10_400,
    )

    estimate = estimator.estimate(decision_message_index=4, timestamp_ns=3 * SECOND)

    assert estimate is not None
    assert estimate.window_start_ns == SECOND
    assert estimate.elapsed_ns == 2 * SECOND
    assert estimate.change_count == 1
    assert estimate.squared_change_sum_ticks2 == 4.0
    assert estimate.sigma_squared == 2.0


def test_task_025_future_quote_mutation_cannot_change_saved_prefix_estimate() -> None:
    estimators = [
        CausalVolatilityEstimator(window_ns=10 * SECOND, tick_size4=100) for _ in range(2)
    ]
    for estimator in estimators:
        estimator.observe_quote(
            message_index=1,
            timestamp_ns=SECOND,
            best_bid_price4=9_900,
            best_ask_price4=10_100,
        )
        estimator.observe_quote(
            message_index=2,
            timestamp_ns=2 * SECOND,
            best_bid_price4=10_000,
            best_ask_price4=10_200,
        )
    earlier = tuple(
        estimator.estimate(decision_message_index=2, timestamp_ns=2 * SECOND)
        for estimator in estimators
    )

    estimators[0].observe_quote(
        message_index=3,
        timestamp_ns=3 * SECOND,
        best_bid_price4=10_500,
        best_ask_price4=10_700,
    )
    estimators[1].observe_quote(
        message_index=3,
        timestamp_ns=3 * SECOND,
        best_bid_price4=9_500,
        best_ask_price4=9_700,
    )

    assert earlier[0] == earlier[1]
    assert earlier[0] is not None and earlier[0].sigma_squared == 1.0


def test_task_025_invalid_or_out_of_order_quote_is_atomic() -> None:
    estimator = CausalVolatilityEstimator(window_ns=SECOND, tick_size4=100)
    estimator.observe_quote(
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )

    for values, expected_code in (
        ((2, 2 * SECOND, 10_200, 10_100), ErrorCode.BOOK_CROSSED),
        ((1, 2 * SECOND, 10_000, 10_200), ErrorCode.LEAKAGE_GUARD),
        ((2, SECOND - 1, 10_000, 10_200), ErrorCode.LEAKAGE_GUARD),
    ):
        before = estimator.snapshot()
        with pytest.raises(SimulationError) as captured:
            estimator.observe_quote(
                message_index=values[0],
                timestamp_ns=values[1],
                best_bid_price4=values[2],
                best_ask_price4=values[3],
            )
        assert captured.value.code is expected_code
        assert estimator.snapshot() == before


def test_task_025_locked_and_off_grid_visible_books_produce_tick_valid_passive_quotes() -> None:
    for bid, ask in ((10_050, 10_050), (10_049, 10_151)):
        strategy = _strategy()
        strategy.observe_quote(
            message_index=1,
            timestamp_ns=SECOND,
            best_bid_price4=bid,
            best_ask_price4=ask,
        )
        decision = strategy.decide(
            decision_message_index=2,
            timestamp_ns=2 * SECOND,
            inventory_shares=0,
        )

        assert decision.bid is not None and decision.ask is not None
        assert decision.bid.price4 % 100 == 0
        assert decision.ask.price4 % 100 == 0
        assert decision.bid.price4 <= bid
        assert decision.bid.price4 < ask
        assert decision.ask.price4 >= ask
        assert decision.ask.price4 > bid


def test_task_025_price_domain_suppresses_only_the_invalid_side() -> None:
    low = _strategy()
    low.observe_quote(message_index=1, timestamp_ns=SECOND, best_bid_price4=0, best_ask_price4=1)
    low_decision = low.decide(decision_message_index=2, timestamp_ns=2 * SECOND, inventory_shares=0)
    assert low_decision.bid is None
    assert low_decision.bid_suppression_reason is QuoteSuppressionReason.PRICE_OUT_OF_RANGE
    assert low_decision.ask is not None

    high = _strategy()
    high.observe_quote(
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=MAX_UINT32 - 1,
        best_ask_price4=MAX_UINT32,
    )
    high_decision = high.decide(
        decision_message_index=2, timestamp_ns=2 * SECOND, inventory_shares=0
    )
    assert high_decision.bid is not None
    assert high_decision.ask is None
    assert high_decision.ask_suppression_reason is QuoteSuppressionReason.PRICE_OUT_OF_RANGE


@pytest.mark.parametrize(("inventory", "suppressed_side"), [(1_000, 1), (-1_000, -1)])
def test_task_025_inventory_gate_suppresses_only_the_risk_increasing_side(
    inventory: int,
    suppressed_side: int,
) -> None:
    strategy = _strategy()
    strategy.observe_quote(
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )

    decision = strategy.decide(
        decision_message_index=2,
        timestamp_ns=2 * SECOND,
        inventory_shares=inventory,
    )

    if suppressed_side == 1:
        assert decision.bid is None and decision.ask is not None
        assert decision.bid_suppression_reason is QuoteSuppressionReason.PROJECTED_INVENTORY_LIMIT
    else:
        assert decision.ask is None and decision.bid is not None
        assert decision.ask_suppression_reason is QuoteSuppressionReason.PROJECTED_INVENTORY_LIMIT


def test_task_025_tau_is_capped_by_exact_time_remaining() -> None:
    strategy = _strategy(session_end_ns=10 * SECOND)
    strategy.observe_quote(
        message_index=1,
        timestamp_ns=8 * SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )

    decision = strategy.decide(
        decision_message_index=2,
        timestamp_ns=9 * SECOND,
        inventory_shares=0,
    )

    assert decision.tau_seconds == 1.0


def test_task_025_proposals_satisfy_existing_order_request_contract() -> None:
    strategy = _strategy()
    strategy.observe_quote(
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )
    decision = strategy.decide(
        decision_message_index=2,
        timestamp_ns=2 * SECOND,
        inventory_shares=0,
    )
    assert decision.bid is not None and decision.ask is not None

    for order_id, proposal in enumerate((decision.bid, decision.ask)):
        validate_order_request(
            OrderRequest(
                simulated_order_id=order_id,
                decision_message_index=decision.decision_message_index,
                prediction_message_index=None,
                requested_timestamp_ns=decision.timestamp_ns,
                symbol_id=decision.symbol_id,
                side=proposal.side,
                price4=proposal.price4,
                quantity=proposal.quantity,
            )
        )


@pytest.mark.parametrize(
    "config",
    [
        _config(gamma=0.0),
        _config(gamma=math.inf),
        _config(risk_horizon_seconds=math.nan),
        _config(risk_horizon_seconds=86_401.0),
        _config(order_quantity=True),
        _config(order_quantity=0),
        _config(inventory_limit=99),
        _config(decision_interval_ns=0),
        _config(max_prediction_age_ns=-1),
        _config(volatility_window_ns=0),
        _config(signal_weight_ticks=False),
        _config(max_signal_ticks=math.nan),
        replace(_config(), name="signal_adjusted_avellaneda_stoikov"),
        replace(_config(), signal_weight_ticks=0.5),
    ],
)
def test_task_025_parameter_boundaries_fail_before_state_creation(config: StrategyConfig) -> None:
    with pytest.raises(SimulationError) as captured:
        _strategy(config=config)

    assert captured.value.code in {
        ErrorCode.CONFIG_SCHEMA,
        ErrorCode.INVENTORY_LIMIT,
        ErrorCode.QUANTITY,
    }


@pytest.mark.parametrize(
    ("window_ns", "tick_size4", "code"),
    [
        (0, 100, ErrorCode.CONFIG_SCHEMA),
        (SECOND, 0, ErrorCode.PRICE),
    ],
)
def test_task_030_high_coverage_volatility_constructor_boundaries(
    window_ns: int, tick_size4: int, code: ErrorCode
) -> None:
    with pytest.raises(SimulationError) as captured:
        CausalVolatilityEstimator(window_ns=window_ns, tick_size4=tick_size4)
    assert captured.value.code is code


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"message_index": -1}, ErrorCode.SIMULATION_ANOMALY),
        ({"timestamp_ns": -1}, ErrorCode.TIMESTAMP),
        ({"best_bid_price4": -1}, ErrorCode.PRICE),
        ({"best_bid_price4": 10_200}, ErrorCode.BOOK_CROSSED),
        ({"best_bid_price4": 0, "best_ask_price4": 0}, ErrorCode.PRICE),
    ],
)
def test_task_030_high_coverage_volatility_observation_boundaries(
    values: dict[str, int], code: ErrorCode
) -> None:
    arguments = {
        "message_index": 1,
        "timestamp_ns": SECOND,
        "best_bid_price4": 10_000,
        "best_ask_price4": 10_100,
    }
    arguments.update(values)
    estimator = CausalVolatilityEstimator(window_ns=SECOND, tick_size4=100)
    with pytest.raises(SimulationError) as captured:
        estimator.observe_quote(**arguments)
    assert captured.value.code is code


def test_task_030_high_coverage_volatility_decision_boundaries() -> None:
    empty = CausalVolatilityEstimator(window_ns=SECOND, tick_size4=100)
    with pytest.raises(SimulationError) as captured:
        empty.estimate(decision_message_index=1, timestamp_ns=SECOND)
    assert captured.value.code is ErrorCode.EMPTY_DATASET

    estimator = CausalVolatilityEstimator(window_ns=SECOND, tick_size4=100)
    estimator.observe_quote(
        message_index=2,
        timestamp_ns=SECOND,
        best_bid_price4=10_000,
        best_ask_price4=10_100,
    )
    for arguments, code in (
        ({"decision_message_index": -1, "timestamp_ns": SECOND}, ErrorCode.SIMULATION_ANOMALY),
        (
            {"decision_message_index": 2, "timestamp_ns": 86_400_000_000_000},
            ErrorCode.TIMESTAMP,
        ),
        ({"decision_message_index": 1, "timestamp_ns": SECOND}, ErrorCode.LEAKAGE_GUARD),
        ({"decision_message_index": 3, "timestamp_ns": SECOND - 1}, ErrorCode.LEAKAGE_GUARD),
    ):
        with pytest.raises(SimulationError) as captured:
            estimator.estimate(**arguments)
        assert captured.value.code is code


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"symbol_id": 0}, ErrorCode.UNKNOWN_SYMBOL),
        ({"tick_size4": 0}, ErrorCode.PRICE),
        ({"session_end_ns": 0}, ErrorCode.SESSION_WINDOW),
        ({"calibration": object()}, ErrorCode.MODEL_TRAINING),
    ],
)
def test_task_030_high_coverage_strategy_constructor_boundaries(
    overrides: dict[str, object], code: ErrorCode
) -> None:
    arguments: dict[str, object] = {
        "symbol": "AAPL",
        "symbol_id": 1,
        "tick_size4": 100,
        "session_start_ns": 0,
        "session_end_ns": 100 * SECOND,
        "calibration": _calibration(),
    }
    arguments.update(overrides)
    with pytest.raises(SimulationError) as captured:
        InventoryAwareAvellanedaStoikov(_config(), **arguments)  # type: ignore[arg-type]
    assert captured.value.code is code
