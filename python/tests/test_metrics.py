"""TASK-020 deterministic metric and calibration hand cases."""

from __future__ import annotations

import math

import numpy as np
import pytest

from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.metrics import (
    calibration_bins,
    classification_metrics,
    day_block_confidence_intervals,
    multiclass_log_loss,
)


def test_task_020_metric_hand_case_uses_fixed_class_order() -> None:
    labels = np.asarray([-1, 0, 1, -1], dtype=np.int8)
    probabilities = np.asarray(
        [
            [0.8, 0.1, 0.1],
            [0.2, 0.7, 0.1],
            [0.1, 0.2, 0.7],
            [0.2, 0.6, 0.2],
        ],
        dtype=np.float64,
    )

    metrics = classification_metrics(labels, probabilities)

    assert metrics["multiclass_log_loss"] == pytest.approx(-math.log(0.8 * 0.7 * 0.7 * 0.2) / 4)
    assert metrics["balanced_accuracy"] == pytest.approx((0.5 + 1.0 + 1.0) / 3)
    assert metrics["macro_f1"] == pytest.approx((2 / 3 + 2 / 3 + 1.0) / 3)
    assert metrics["confusion_matrix"] == {
        "class_order": ["down", "flat", "up"],
        "rows_true_columns_predicted": [[1, 1, 0], [0, 1, 0], [0, 0, 1]],
    }

    calibration = calibration_bins(labels, probabilities)
    down_bins = calibration["classes"][0]["bins"]
    assert sum(item["count"] for item in down_bins) == 4
    assert any(item["count"] == 0 and item["mean_probability"] is None for item in down_bins)


def test_task_020_day_block_bootstrap_is_seeded_and_omits_short_series() -> None:
    labels = np.tile(np.asarray([-1, 0, 1], dtype=np.int8), 5)
    probabilities = np.tile(
        np.asarray(
            [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.2, 0.7]],
            dtype=np.float64,
        ),
        (5, 1),
    )
    dates = [f"2019-01-{day:02d}" for day in range(1, 6) for _ in range(3)]

    first = day_block_confidence_intervals(labels, probabilities, dates, seed=7987, repetitions=40)
    second = day_block_confidence_intervals(labels, probabilities, dates, seed=7987, repetitions=40)

    assert first == second
    assert first["status"] == "completed"
    assert first["method"] == "whole_trading_day_percentile_bootstrap"
    point_metrics = classification_metrics(labels, probabilities)
    for name in ("multiclass_log_loss", "balanced_accuracy", "macro_f1"):
        assert first["intervals"][name] == pytest.approx(
            {"lower": point_metrics[name], "upper": point_metrics[name]}
        )
    assert day_block_confidence_intervals(
        labels[:12], probabilities[:12], dates[:12], seed=7987
    ) == {
        "status": "omitted",
        "reason": "fewer_than_five_trading_days",
        "trading_days": 4,
    }


def test_task_020_metrics_reject_invalid_probabilities() -> None:
    labels = np.asarray([-1, 0, 1], dtype=np.int8)
    probabilities = np.asarray(
        [[0.8, 0.3, -0.1], [0.2, 0.7, 0.1], [0.1, 0.2, np.nan]],
        dtype=np.float64,
    )

    with pytest.raises(ModelTrainingError) as captured:
        multiclass_log_loss(labels, probabilities)

    assert captured.value.code is ErrorCode.MODEL_TRAINING
