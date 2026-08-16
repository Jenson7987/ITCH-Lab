"""TASK-025 training-only execution-intensity calibration tests."""

from __future__ import annotations

import math
from datetime import date

import pytest

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import MAX_UINT64
from itchlab_research.strategies import (
    CalibrationSource,
    CausalIntensityCalibrator,
)

TRAINING_DAY = date(2019, 1, 30)
VALIDATION_DAY = date(2019, 3, 27)


def _calibrator() -> CausalIntensityCalibrator:
    calibrator = CausalIntensityCalibrator(
        symbols=("MSFT", "AAPL"),
        training_dates=(TRAINING_DAY,),
    )
    for distance, executions in enumerate((99, 49, 24)):
        calibrator.record_exposure(TRAINING_DAY, "AAPL", distance, 10_000_000_000)
        calibrator.record_execution(TRAINING_DAY, "AAPL", distance, executions)
    return calibrator


def test_task_025_calibration_matches_hand_calculated_weighted_fit_and_pooled_fallback() -> None:
    calibration = _calibrator().finalise()

    expected_kappa = math.log(2.0)
    expected_intercept = math.log(100.0 / 11.0)
    assert calibration.training_dates == (TRAINING_DAY,)
    assert calibration.pooled_kappa == pytest.approx(expected_kappa, rel=0.0, abs=1e-15)
    assert calibration.pooled_intercept == pytest.approx(expected_intercept, rel=0.0, abs=1e-15)
    assert len(calibration.pooled_buckets) == 11
    assert calibration.pooled_buckets[0].exposure_seconds == 10.0
    assert calibration.pooled_buckets[0].execution_count == 99

    aapl = calibration.for_symbol("AAPL")
    msft = calibration.for_symbol("MSFT")
    assert aapl.source is CalibrationSource.SYMBOL
    assert aapl.kappa == pytest.approx(expected_kappa, rel=0.0, abs=1e-15)
    assert msft.source is CalibrationSource.POOLED
    assert msft.kappa == calibration.pooled_kappa
    assert all(bucket.exposure_ns == 0 for bucket in msft.buckets)


def test_task_025_non_training_observation_is_rejected_atomically() -> None:
    calibrator = _calibrator()
    before = calibrator.snapshot()

    with pytest.raises(SimulationError) as captured:
        calibrator.record_execution(VALIDATION_DAY, "AAPL", 0)

    assert captured.value.code is ErrorCode.LEAKAGE_GUARD
    assert calibrator.snapshot() == before


@pytest.mark.parametrize(
    ("operation", "value", "expected_code"),
    [
        ("exposure", -1, ErrorCode.TIMESTAMP),
        ("exposure", True, ErrorCode.TIMESTAMP),
        ("execution", 0, ErrorCode.QUANTITY),
        ("execution", True, ErrorCode.QUANTITY),
    ],
)
def test_task_025_invalid_bucket_updates_leave_calibration_unchanged(
    operation: str,
    value: int,
    expected_code: ErrorCode,
) -> None:
    calibrator = _calibrator()
    before = calibrator.snapshot()

    with pytest.raises(SimulationError) as captured:
        if operation == "exposure":
            calibrator.record_exposure(TRAINING_DAY, "AAPL", 0, value)
        else:
            calibrator.record_execution(TRAINING_DAY, "AAPL", 0, value)

    assert captured.value.code is expected_code
    assert calibrator.snapshot() == before


def test_task_025_bucket_totals_are_checked_before_mutation() -> None:
    calibrator = CausalIntensityCalibrator(symbols=("AAPL",), training_dates=(TRAINING_DAY,))
    calibrator.record_exposure(TRAINING_DAY, "AAPL", 0, MAX_UINT64)
    before = calibrator.snapshot()

    with pytest.raises(SimulationError) as captured:
        calibrator.record_exposure(TRAINING_DAY, "AAPL", 0, 1)

    assert captured.value.code is ErrorCode.TIMESTAMP
    assert calibrator.snapshot() == before


@pytest.mark.parametrize("counts", [(1,), (1, 3)])
def test_task_025_absent_or_non_positive_pooled_fit_prevents_strategy_run(
    counts: tuple[int, ...],
) -> None:
    calibrator = CausalIntensityCalibrator(symbols=("AAPL",), training_dates=(TRAINING_DAY,))
    for distance, count in enumerate(counts):
        calibrator.record_exposure(TRAINING_DAY, "AAPL", distance, 1_000_000_000)
        calibrator.record_execution(TRAINING_DAY, "AAPL", distance, count)

    with pytest.raises(SimulationError) as captured:
        calibrator.finalise()

    assert captured.value.code is ErrorCode.MODEL_TRAINING


@pytest.mark.parametrize(
    ("symbols", "dates", "expected_code"),
    [
        ((), (TRAINING_DAY,), ErrorCode.UNKNOWN_SYMBOL),
        (("AAPL", "AAPL"), (TRAINING_DAY,), ErrorCode.UNKNOWN_SYMBOL),
        (("AAPL",), (), ErrorCode.PARTITION),
        (("AAPL",), (VALIDATION_DAY, TRAINING_DAY), ErrorCode.PARTITION),
        (("AAPL",), (TRAINING_DAY, TRAINING_DAY), ErrorCode.PARTITION),
    ],
)
def test_task_025_calibration_scope_boundaries(
    symbols: tuple[str, ...],
    dates: tuple[date, ...],
    expected_code: ErrorCode,
) -> None:
    with pytest.raises(SimulationError) as captured:
        CausalIntensityCalibrator(symbols=symbols, training_dates=dates)

    assert captured.value.code is expected_code


def test_task_025_unknown_symbol_lookup_has_stable_error() -> None:
    calibration = _calibrator().finalise()

    with pytest.raises(SimulationError) as captured:
        calibration.for_symbol("NVDA")

    assert captured.value.code is ErrorCode.UNKNOWN_SYMBOL
