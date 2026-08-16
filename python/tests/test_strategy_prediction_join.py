"""TASK-026 causal prediction-key and fallback tests."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.strategies import (
    CausalPredictionJoin,
    PredictionDiagnosticCode,
    PredictionKey,
    SignalPrediction,
)

DAY = date(2019, 3, 27)
EXPERIMENT_ID = "20190327T120000.000000000Z-0123456789ab"


def _prediction(
    message_index: int,
    timestamp_ns: int,
    score: float,
    **key_overrides: object,
) -> SignalPrediction:
    values: dict[str, object] = {
        "experiment_id": EXPERIMENT_ID,
        "trading_date": DAY,
        "symbol_id": 1,
        "message_index": message_index,
        "model_name": "logistic_regression",
    }
    values.update(key_overrides)
    return SignalPrediction(
        key=PredictionKey(**values),  # type: ignore[arg-type]
        timestamp_ns=timestamp_ns,
        score=score,
    )


def _join(
    predictions: object,
    *,
    max_prediction_age_ns: int = 100,
) -> CausalPredictionJoin:
    return CausalPredictionJoin(
        predictions,  # type: ignore[arg-type]
        experiment_id=EXPERIMENT_ID,
        trading_date=DAY,
        symbol_id=1,
        model_name="logistic_regression",
        max_prediction_age_ns=max_prediction_age_ns,
    )


def test_task_026_exact_asof_join_retains_key_and_never_selects_future_row() -> None:
    first = _prediction(10, 100, 0.25)
    second = _prediction(20, 200, -0.5)
    join = _join((first, second))

    missing = join.select(decision_message_index=9, timestamp_ns=90)
    exact = join.select(decision_message_index=10, timestamp_ns=100)
    between = join.select(decision_message_index=15, timestamp_ns=150)
    later = join.select(decision_message_index=20, timestamp_ns=200)

    assert missing.key is None
    assert missing.effective_score == 0.0
    assert missing.diagnostic is not None
    assert missing.diagnostic.code is PredictionDiagnosticCode.MISSING
    assert exact.key == first.key
    assert exact.age_ns == 0
    assert exact.effective_score == 0.25
    assert between.key == first.key
    assert between.age_ns == 50
    assert between.effective_score == 0.25
    assert later.key == second.key
    assert later.effective_score == -0.5


def test_task_026_prediction_age_boundary_is_inclusive_then_stale() -> None:
    join = _join((_prediction(10, 100, 0.75),), max_prediction_age_ns=100)

    fresh = join.select(decision_message_index=20, timestamp_ns=200)
    stale = join.select(decision_message_index=21, timestamp_ns=201)

    assert fresh.age_ns == 100
    assert fresh.effective_score == 0.75
    assert fresh.diagnostic is None
    assert stale.key is not None and stale.key.message_index == 10
    assert stale.raw_score == 0.75
    assert stale.effective_score == 0.0
    assert stale.diagnostic is not None
    assert stale.diagnostic.code is PredictionDiagnosticCode.STALE


def test_task_026_mutating_future_score_cannot_change_saved_selection_prefix() -> None:
    first = _prediction(10, 100, 0.25)
    joins = (
        _join((first, _prediction(20, 200, 1.0))),
        _join((first, _prediction(20, 200, -1.0))),
    )

    prefixes = tuple(join.select(decision_message_index=10, timestamp_ns=100) for join in joins)

    assert prefixes[0] == prefixes[1]


def test_task_026_join_keeps_only_one_future_lookahead_in_normal_operation() -> None:
    yielded: list[int] = []

    def rows() -> object:
        for message_index in (10, 20, 30):
            yielded.append(message_index)
            yield _prediction(message_index, message_index * 10, 0.1)

    join = _join(rows())

    selected = join.select(decision_message_index=10, timestamp_ns=100)

    assert selected.key is not None and selected.key.message_index == 10
    assert yielded == [10, 20]


@pytest.mark.parametrize(
    "prediction",
    [
        _prediction(10, 100, math.nan),
        _prediction(10, 100, math.inf),
        _prediction(10, 100, 1.01),
        _prediction(10, 100, 0.1, experiment_id="other"),
        _prediction(10, 100, 0.1, trading_date=date(2019, 3, 28)),
        _prediction(10, 100, 0.1, symbol_id=2),
        _prediction(10, 100, 0.1, model_name="hist_gradient_boosting"),
    ],
)
def test_task_026_invalid_prediction_is_rejected_without_semantic_state_change(
    prediction: SignalPrediction,
) -> None:
    join = _join((prediction,))
    before = join.snapshot()

    with pytest.raises(SimulationError) as captured:
        join.select(decision_message_index=10, timestamp_ns=100)

    assert captured.value.code is ErrorCode.PREDICTION_KEY
    assert join.snapshot() == before


def test_task_026_duplicate_or_decreasing_prediction_keys_fail_atomically() -> None:
    for second in (_prediction(10, 100, -0.1), _prediction(9, 110, -0.1)):
        join = _join((_prediction(10, 100, 0.1), second))
        before = join.snapshot()

        with pytest.raises(SimulationError) as captured:
            join.select(decision_message_index=20, timestamp_ns=200)

        assert captured.value.code is ErrorCode.PREDICTION_KEY
        assert join.snapshot() == before


def test_task_026_prediction_timestamp_cannot_follow_decision() -> None:
    join = _join((_prediction(10, 101, 0.1),))
    before = join.snapshot()

    with pytest.raises(SimulationError) as captured:
        join.select(decision_message_index=10, timestamp_ns=100)

    assert captured.value.code is ErrorCode.LEAKAGE_GUARD
    assert join.snapshot() == before


def test_task_026_decisions_cannot_move_backwards() -> None:
    join = _join((_prediction(10, 100, 0.1),))
    join.select(decision_message_index=20, timestamp_ns=200)
    before = join.snapshot()

    with pytest.raises(SimulationError) as captured:
        join.select(decision_message_index=19, timestamp_ns=201)

    assert captured.value.code is ErrorCode.LEAKAGE_GUARD
    assert join.snapshot() == before


@pytest.mark.parametrize("value", [-1, True, 9_007_199_254_740_992])
def test_task_026_prediction_age_config_boundaries(value: int) -> None:
    with pytest.raises(SimulationError) as captured:
        _join((), max_prediction_age_ns=value)

    assert captured.value.code is ErrorCode.CONFIG_SCHEMA


def test_task_026_wrong_prediction_domain_type_fails_with_stable_error() -> None:
    join = _join((replace(_prediction(10, 100, 0.1), key="invalid"),))  # type: ignore[arg-type]

    with pytest.raises(SimulationError) as captured:
        join.select(decision_message_index=10, timestamp_ns=100)

    assert captured.value.code is ErrorCode.PREDICTION_KEY
