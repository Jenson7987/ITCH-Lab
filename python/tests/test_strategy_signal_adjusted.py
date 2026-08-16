"""TASK-026 bounded signal-adjusted strategy and validation selection tests."""

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
from itchlab_research.strategies import (
    BaselineDecision,
    CausalIntensityCalibrator,
    IntensityCalibration,
    InventoryAwareAvellanedaStoikov,
    ModelValidationMetric,
    PredictionDiagnosticCode,
    PredictionKey,
    SignalAdjustedAvellanedaStoikov,
    SignalAdjustedDecision,
    SignalPrediction,
    ValidationSignalPnl,
    select_signal_model,
    select_signal_weight,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRAINING_DAY = date(2019, 1, 30)
VALIDATION_DAY = date(2019, 3, 27)
SECOND_VALIDATION_DAY = date(2019, 3, 28)
EXPERIMENT_ID = "20190327T120000.000000000Z-0123456789ab"
SECOND = 1_000_000_000


def _config(**overrides: object) -> StrategyConfig:
    values: dict[str, object] = {
        "name": "signal_adjusted_avellaneda_stoikov",
        "decision_interval_ns": 100_000_000,
        "max_prediction_age_ns": 500_000_000,
        "order_quantity": 100,
        "inventory_limit": 1_000,
        "gamma": 0.1,
        "volatility_window_ns": 60 * SECOND,
        "risk_horizon_seconds": 10.0,
        "signal_weight_ticks": 1.0,
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


def _prediction(
    score: float,
    *,
    message_index: int = 2,
    timestamp_ns: int = 2 * SECOND,
) -> SignalPrediction:
    return SignalPrediction(
        key=PredictionKey(
            experiment_id=EXPERIMENT_ID,
            trading_date=VALIDATION_DAY,
            symbol_id=1,
            message_index=message_index,
            model_name="logistic_regression",
        ),
        timestamp_ns=timestamp_ns,
        score=score,
    )


def _signal_strategy(
    *,
    config: StrategyConfig | None = None,
    predictions: object = (),
) -> SignalAdjustedAvellanedaStoikov:
    return SignalAdjustedAvellanedaStoikov(
        _config() if config is None else config,
        symbol="AAPL",
        symbol_id=1,
        tick_size4=100,
        trading_date=VALIDATION_DAY,
        session_start_ns=0,
        session_end_ns=100 * SECOND,
        calibration=_calibration(),
        experiment_id=EXPERIMENT_ID,
        model_name="logistic_regression",
        predictions=predictions,  # type: ignore[arg-type]
    )


def _observe(strategy: object) -> None:
    strategy.observe_quote(  # type: ignore[attr-defined]
        message_index=1,
        timestamp_ns=SECOND,
        best_bid_price4=9_900,
        best_ask_price4=10_100,
    )
    strategy.observe_quote(  # type: ignore[attr-defined]
        message_index=2,
        timestamp_ns=2 * SECOND,
        best_bid_price4=10_000,
        best_ask_price4=10_200,
    )


def _order_requests(decision: SignalAdjustedDecision) -> tuple[OrderRequest, ...]:
    prediction_index = (
        None
        if decision.prediction is None or decision.prediction.key is None
        else decision.prediction.key.message_index
    )
    proposals = tuple(proposal for proposal in (decision.bid, decision.ask) if proposal is not None)
    return tuple(
        OrderRequest(
            simulated_order_id=offset,
            decision_message_index=decision.baseline.decision_message_index,
            prediction_message_index=prediction_index,
            requested_timestamp_ns=decision.baseline.timestamp_ns,
            symbol_id=decision.baseline.symbol_id,
            side=proposal.side,
            price4=proposal.price4,
            quantity=proposal.quantity,
        )
        for offset, proposal in enumerate(proposals)
    )


def _baseline_order_requests(decision: BaselineDecision) -> tuple[OrderRequest, ...]:
    proposals = tuple(proposal for proposal in (decision.bid, decision.ask) if proposal is not None)
    return tuple(
        OrderRequest(
            simulated_order_id=offset,
            decision_message_index=decision.decision_message_index,
            prediction_message_index=None,
            requested_timestamp_ns=decision.timestamp_ns,
            symbol_id=decision.symbol_id,
            side=proposal.side,
            price4=proposal.price4,
            quantity=proposal.quantity,
        )
        for offset, proposal in enumerate(proposals)
    )


def test_ut_strat_002_zero_weight_emits_exact_baseline_economic_decision() -> None:
    baseline = InventoryAwareAvellanedaStoikov(
        replace(
            _config(signal_weight_ticks=0.0),
            name="inventory_aware_avellaneda_stoikov",
        ),
        symbol="AAPL",
        symbol_id=1,
        tick_size4=100,
        session_start_ns=0,
        session_end_ns=100 * SECOND,
        calibration=_calibration(),
    )

    def forbidden_predictions() -> object:
        raise AssertionError("zero weight must not consume a prediction")
        yield _prediction(1.0)

    signal = _signal_strategy(
        config=_config(signal_weight_ticks=0.0),
        predictions=forbidden_predictions(),
    )
    _observe(baseline)
    _observe(signal)

    baseline_decision = baseline.decide(
        decision_message_index=2,
        timestamp_ns=2 * SECOND,
        inventory_shares=0,
    )
    signal_decision = signal.decide(
        decision_message_index=2,
        timestamp_ns=2 * SECOND,
        inventory_shares=0,
    )

    assert signal_decision.baseline == baseline_decision
    assert signal_decision.prediction is None
    assert signal_decision.adjustment_ticks == 0.0
    assert (
        signal_decision.signal_reservation_price_ticks == baseline_decision.reservation_price_ticks
    )
    assert signal_decision.bid == baseline_decision.bid
    assert signal_decision.ask == baseline_decision.ask
    assert signal.prediction_diagnostics == ()

    signal_requests = _order_requests(signal_decision)
    baseline_requests = _baseline_order_requests(baseline_decision)
    assert signal_requests == baseline_requests
    for request in signal_requests:
        validate_order_request(request)


def _decision_row(scenario: str, decision: SignalAdjustedDecision) -> dict[str, object]:
    return {
        "scenario": scenario,
        "raw_score": (None if decision.prediction is None else decision.prediction.raw_score),
        "effective_score": (
            0.0 if decision.prediction is None else decision.prediction.effective_score
        ),
        "prediction_message_index": (
            None
            if decision.prediction is None or decision.prediction.key is None
            else decision.prediction.key.message_index
        ),
        "diagnostic": (None if not decision.diagnostics else decision.diagnostics[0].code),
        "baseline_reservation_price_ticks": round(
            float(decision.baseline.reservation_price_ticks), 12
        ),
        "unclipped_adjustment_ticks": round(decision.unclipped_adjustment_ticks, 12),
        "adjustment_ticks": round(decision.adjustment_ticks, 12),
        "signal_reservation_price_ticks": round(float(decision.signal_reservation_price_ticks), 12),
        "bid_price4": None if decision.bid is None else decision.bid.price4,
        "ask_price4": None if decision.ask is None else decision.ask.price4,
    }


def test_task_026_controlled_signal_decision_table_matches_golden() -> None:
    scenarios = (
        (
            "positive_clipped",
            _config(signal_weight_ticks=2.0, max_signal_ticks=1.0),
            (_prediction(0.75),),
        ),
        (
            "negative_clipped",
            _config(signal_weight_ticks=2.0, max_signal_ticks=1.0),
            (_prediction(-0.75),),
        ),
        (
            "positive_half_tick",
            _config(signal_weight_ticks=1.0, max_signal_ticks=0.5),
            (_prediction(0.75),),
        ),
        ("missing", _config(), ()),
        ("stale", _config(), (_prediction(1.0, message_index=1, timestamp_ns=SECOND),)),
    )
    actual: list[dict[str, object]] = []
    for name, config, predictions in scenarios:
        strategy = _signal_strategy(config=config, predictions=predictions)
        _observe(strategy)
        actual.append(
            _decision_row(
                name,
                strategy.decide(
                    decision_message_index=2,
                    timestamp_ns=2 * SECOND,
                    inventory_shares=0,
                ),
            )
        )

    expected = json.loads(
        (
            REPOSITORY_ROOT
            / "tests"
            / "golden"
            / "simulation"
            / "task026-signal-decision-table.json"
        ).read_text(encoding="utf-8")
    )
    assert actual == expected


def test_task_026_selected_prediction_key_flows_to_existing_order_contract() -> None:
    strategy = _signal_strategy(predictions=(_prediction(0.25),))
    _observe(strategy)

    decision = strategy.decide(
        decision_message_index=2,
        timestamp_ns=2 * SECOND,
        inventory_shares=0,
    )

    assert decision.prediction is not None
    assert decision.prediction.key == _prediction(0.25).key
    requests = _order_requests(decision)
    assert requests
    assert {request.prediction_message_index for request in requests} == {2}
    for request in requests:
        validate_order_request(request)


def test_task_026_model_selection_uses_validation_loss_and_simplicity_ties() -> None:
    selected = select_signal_model(
        (
            ModelValidationMetric("validation", "prior", 1.0),
            ModelValidationMetric("validation", "logistic_regression", 0.9),
            ModelValidationMetric("validation", "hist_gradient_boosting", 0.8),
        )
    )
    tied = select_signal_model(
        (
            ModelValidationMetric("validation", "prior", 1.0),
            ModelValidationMetric("validation", "logistic_regression", 0.9999995),
            ModelValidationMetric("validation", "hist_gradient_boosting", 1.2),
        )
    )
    chained_tie = select_signal_model(
        (
            ModelValidationMetric("validation", "prior", 1.0000015),
            ModelValidationMetric("validation", "logistic_regression", 1.0000008),
            ModelValidationMetric("validation", "hist_gradient_boosting", 1.0),
        )
    )

    assert selected.model_name == "hist_gradient_boosting"
    assert tied.model_name == "prior"
    assert chained_tie.model_name == "logistic_regression"


@pytest.mark.parametrize(
    "evaluations",
    [
        (
            ModelValidationMetric("test", "prior", 1.0),
            ModelValidationMetric("validation", "logistic_regression", 0.9),
            ModelValidationMetric("validation", "hist_gradient_boosting", 0.8),
        ),
        (
            ModelValidationMetric("validation", "prior", 1.0),
            ModelValidationMetric("validation", "logistic_regression", 0.9),
        ),
        (
            ModelValidationMetric("validation", "prior", 1.0),
            ModelValidationMetric("validation", "prior", 0.9),
            ModelValidationMetric("validation", "hist_gradient_boosting", 0.8),
        ),
    ],
)
def test_task_026_model_selection_rejects_non_validation_or_incomplete_evidence(
    evaluations: tuple[ModelValidationMetric, ...],
) -> None:
    with pytest.raises(SimulationError) as captured:
        select_signal_model(evaluations)

    assert captured.value.code in {ErrorCode.LEAKAGE_GUARD, ErrorCode.MODEL_TRAINING}


def _weight_rows(values: dict[float, tuple[int, int]]) -> tuple[ValidationSignalPnl, ...]:
    rows: list[ValidationSignalPnl] = []
    for weight, daily_values in values.items():
        for trading_date, pnl in zip(
            (VALIDATION_DAY, SECOND_VALIDATION_DAY), daily_values, strict=True
        ):
            rows.append(
                ValidationSignalPnl(
                    partition="validation",
                    trading_date=trading_date,
                    signal_weight_ticks=weight,
                    submission_latency_ns=100_000,
                    cancellation_latency_ns=100_000,
                    maker_fee_microusd_per_share=-2_000,
                    net_pnl_microusd=pnl,
                )
            )
    return tuple(rows)


def test_task_026_signal_weight_uses_exact_validation_day_mean_and_tie_rule() -> None:
    selection = select_signal_weight(
        _weight_rows(
            {
                0.0: (100, 100),
                0.5: (101, 101),
                1.0: (103, 101),
                2.0: (103, 103),
            }
        )
    )

    assert selection.signal_weight_ticks == 1.0
    assert [item.mean_net_pnl_microusd for item in selection.evaluations] == [
        100.0,
        101.0,
        102.0,
        103.0,
    ]

    chained_tie = select_signal_weight(
        _weight_rows(
            {
                0.0: (0, 0),
                0.5: (1, 1),
                1.0: (1, 2),
                2.0: (-5, -5),
            }
        )
    )
    assert chained_tie.signal_weight_ticks == 0.5


def test_task_026_signal_weight_rejects_test_partition_and_wrong_scenario() -> None:
    rows = list(
        _weight_rows(
            {
                0.0: (100, 100),
                0.5: (101, 101),
                1.0: (102, 102),
                2.0: (103, 103),
            }
        )
    )
    for replacement, expected_code in (
        (replace(rows[0], partition="test"), ErrorCode.LEAKAGE_GUARD),
        (replace(rows[0], submission_latency_ns=0), ErrorCode.CONFIG_SCHEMA),
    ):
        changed = (replacement, *rows[1:])
        with pytest.raises(SimulationError) as captured:
            select_signal_weight(changed)
        assert captured.value.code is expected_code


def test_task_026_signal_weight_requires_complete_equal_day_coverage() -> None:
    rows = _weight_rows(
        {
            0.0: (100, 100),
            0.5: (101, 101),
            1.0: (102, 102),
            2.0: (103, 103),
        }
    )

    with pytest.raises(SimulationError) as captured:
        select_signal_weight(rows[:-1])

    assert captured.value.code is ErrorCode.PARTITION


@pytest.mark.parametrize(
    "config",
    [
        _config(signal_weight_ticks=0.25),
        _config(signal_weight_ticks=True),
        _config(max_signal_ticks=math.nan),
        replace(_config(), name="inventory_aware_avellaneda_stoikov"),
    ],
)
def test_task_026_signal_parameter_boundaries_fail_before_state_creation(
    config: StrategyConfig,
) -> None:
    with pytest.raises(SimulationError) as captured:
        _signal_strategy(config=config)

    assert captured.value.code is ErrorCode.CONFIG_SCHEMA


def test_task_026_missing_and_stale_predictions_are_distinct_nonfatal_diagnostics() -> None:
    missing_strategy = _signal_strategy(predictions=())
    stale_strategy = _signal_strategy(
        predictions=(_prediction(1.0, message_index=1, timestamp_ns=SECOND),)
    )
    _observe(missing_strategy)
    _observe(stale_strategy)

    missing = missing_strategy.decide(
        decision_message_index=2, timestamp_ns=2 * SECOND, inventory_shares=0
    )
    stale = stale_strategy.decide(
        decision_message_index=2, timestamp_ns=2 * SECOND, inventory_shares=0
    )

    assert missing.adjustment_ticks == stale.adjustment_ticks == 0.0
    assert missing.diagnostics[0].code is PredictionDiagnosticCode.MISSING
    assert stale.diagnostics[0].code is PredictionDiagnosticCode.STALE
    assert missing.prediction is not None and missing.prediction.key is None
    assert stale.prediction is not None and stale.prediction.key is not None
