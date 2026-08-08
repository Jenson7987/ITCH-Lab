"""Deterministic three-class predictive metrics for frozen experiment partitions."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from itchlab_research.errors import ErrorCode, ModelTrainingError

CLASS_VALUES: Final[tuple[int, int, int]] = (-1, 0, 1)
CLASS_NAMES: Final[tuple[str, str, str]] = ("down", "flat", "up")
CALIBRATION_BINS: Final = 10
BOOTSTRAP_REPETITIONS: Final = 1_000
BOOTSTRAP_CONFIDENCE_LEVEL: Final = 0.95


def _fail(message: str) -> ModelTrainingError:
    return ModelTrainingError(ErrorCode.MODEL_TRAINING, message)


def validate_predictions(labels: NDArray[np.int8], probabilities: NDArray[np.float64]) -> None:
    """Validate exact labels and finite three-class probability rows."""
    if labels.ndim != 1 or probabilities.ndim != 2 or probabilities.shape != (labels.size, 3):
        raise _fail("Prediction arrays do not match the three-class metric contract.")
    if labels.size == 0 or not np.isin(labels, CLASS_VALUES).all():
        raise _fail("Metric labels are empty or outside the three-class domain.")
    if (
        not np.isfinite(probabilities).all()
        or (probabilities < 0.0).any()
        or (probabilities > 1.0).any()
        or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise _fail("Predicted probabilities are non-finite, out of bounds or unnormalised.")


def predicted_labels(probabilities: NDArray[np.float64]) -> NDArray[np.int8]:
    """Return fixed-order argmax labels; an exact tie resolves down, then flat, then up."""
    if probabilities.ndim != 2 or probabilities.shape[1] != 3:
        raise _fail("Prediction probabilities do not have three columns.")
    values = np.asarray(CLASS_VALUES, dtype=np.int8)
    return values[np.argmax(probabilities, axis=1)]


def class_distribution(labels: NDArray[np.int8]) -> dict[str, Any]:
    """Return fixed-order class counts and fractions."""
    if labels.ndim != 1 or labels.size == 0 or not np.isin(labels, CLASS_VALUES).all():
        raise _fail("Class-distribution labels are invalid.")
    rows = int(labels.size)
    counts = [int(np.count_nonzero(labels == value)) for value in CLASS_VALUES]
    return {
        "rows": rows,
        "classes": [
            {"name": name, "value": value, "count": count, "fraction": count / rows}
            for name, value, count in zip(CLASS_NAMES, CLASS_VALUES, counts, strict=True)
        ],
    }


def confusion_matrix(
    labels: NDArray[np.int8], probabilities: NDArray[np.float64]
) -> list[list[int]]:
    """Return a 3x3 matrix with true classes as rows and predicted classes as columns."""
    validate_predictions(labels, probabilities)
    predicted = predicted_labels(probabilities)
    return [
        [
            int(np.count_nonzero((labels == actual) & (predicted == estimate)))
            for estimate in CLASS_VALUES
        ]
        for actual in CLASS_VALUES
    ]


def multiclass_log_loss(labels: NDArray[np.int8], probabilities: NDArray[np.float64]) -> float:
    """Return mean natural-log loss in the fixed down/flat/up class order."""
    validate_predictions(labels, probabilities)
    class_index = np.searchsorted(np.asarray(CLASS_VALUES), labels)
    selected = probabilities[np.arange(labels.size), class_index]
    value = float(-np.mean(np.log(np.maximum(selected, np.finfo(np.float64).eps))))
    if not math.isfinite(value):
        raise _fail("Multiclass log loss is not finite.")
    return value


def balanced_accuracy(labels: NDArray[np.int8], probabilities: NDArray[np.float64]) -> float:
    """Average recall over true classes present in the evaluated slice."""
    validate_predictions(labels, probabilities)
    predicted = predicted_labels(probabilities)
    recalls = [
        float(np.count_nonzero((labels == value) & (predicted == value)))
        / int(np.count_nonzero(labels == value))
        for value in CLASS_VALUES
        if np.count_nonzero(labels == value)
    ]
    value = float(np.mean(recalls))
    if not math.isfinite(value):
        raise _fail("Balanced accuracy is not finite.")
    return value


def macro_f1(labels: NDArray[np.int8], probabilities: NDArray[np.float64]) -> float:
    """Average per-class F1 across the fixed catalogue, using zero for undefined classes."""
    validate_predictions(labels, probabilities)
    predicted = predicted_labels(probabilities)
    scores: list[float] = []
    for value in CLASS_VALUES:
        true_positive = int(np.count_nonzero((labels == value) & (predicted == value)))
        false_positive = int(np.count_nonzero((labels != value) & (predicted == value)))
        false_negative = int(np.count_nonzero((labels == value) & (predicted != value)))
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(0.0 if denominator == 0 else 2 * true_positive / denominator)
    return float(np.mean(scores))


def calibration_bins(
    labels: NDArray[np.int8], probabilities: NDArray[np.float64]
) -> dict[str, Any]:
    """Return ten equal-width one-vs-rest reliability bins for every class."""
    validate_predictions(labels, probabilities)
    classes: list[dict[str, Any]] = []
    for class_index, (name, value) in enumerate(zip(CLASS_NAMES, CLASS_VALUES, strict=True)):
        predicted = probabilities[:, class_index]
        indices = np.minimum((predicted * CALIBRATION_BINS).astype(np.int64), 9)
        bins: list[dict[str, Any]] = []
        for bin_index in range(CALIBRATION_BINS):
            mask = indices == bin_index
            count = int(np.count_nonzero(mask))
            bins.append(
                {
                    "bin": bin_index,
                    "lower": bin_index / CALIBRATION_BINS,
                    "upper": (bin_index + 1) / CALIBRATION_BINS,
                    "upper_inclusive": bin_index == CALIBRATION_BINS - 1,
                    "count": count,
                    "mean_probability": None if count == 0 else float(np.mean(predicted[mask])),
                    "observed_frequency": (
                        None if count == 0 else float(np.mean(labels[mask] == value))
                    ),
                }
            )
        classes.append({"name": name, "value": value, "bins": bins})
    return {
        "method": "one_vs_rest_equal_width",
        "bin_count": CALIBRATION_BINS,
        "classes": classes,
    }


def classification_metrics(
    labels: NDArray[np.int8], probabilities: NDArray[np.float64]
) -> dict[str, Any]:
    """Return all required aggregate classification metrics."""
    matrix = confusion_matrix(labels, probabilities)
    return {
        "multiclass_log_loss": multiclass_log_loss(labels, probabilities),
        "balanced_accuracy": balanced_accuracy(labels, probabilities),
        "macro_f1": macro_f1(labels, probabilities),
        "confusion_matrix": {
            "class_order": list(CLASS_NAMES),
            "rows_true_columns_predicted": matrix,
        },
    }


def day_block_confidence_intervals(
    labels: NDArray[np.int8],
    probabilities: NDArray[np.float64],
    trading_dates: Sequence[str],
    *,
    seed: int,
    repetitions: int = BOOTSTRAP_REPETITIONS,
) -> dict[str, Any]:
    """Bootstrap whole trading-day blocks and return deterministic percentile intervals."""
    validate_predictions(labels, probabilities)
    if len(trading_dates) != labels.size or repetitions <= 0:
        raise _fail("Day-block bootstrap inputs are invalid.")
    dates = np.asarray(trading_dates, dtype=np.str_)
    unique_dates = np.unique(dates)
    if unique_dates.size < 5:
        return {
            "status": "omitted",
            "reason": "fewer_than_five_trading_days",
            "trading_days": int(unique_dates.size),
        }
    rng = np.random.default_rng(seed)
    values = {
        "multiclass_log_loss": np.empty(repetitions, dtype=np.float64),
        "balanced_accuracy": np.empty(repetitions, dtype=np.float64),
        "macro_f1": np.empty(repetitions, dtype=np.float64),
    }
    predictions = predicted_labels(probabilities)
    class_indices = np.searchsorted(np.asarray(CLASS_VALUES), labels)
    selected_probabilities = probabilities[np.arange(labels.size), class_indices]
    day_rows = np.empty(unique_dates.size, dtype=np.int64)
    day_loss_sums = np.empty(unique_dates.size, dtype=np.float64)
    day_confusions = np.empty((unique_dates.size, 3, 3), dtype=np.int64)
    for day_index, trading_date in enumerate(unique_dates):
        mask = dates == trading_date
        day_rows[day_index] = np.count_nonzero(mask)
        day_loss_sums[day_index] = -np.log(
            np.maximum(selected_probabilities[mask], np.finfo(np.float64).eps)
        ).sum()
        day_confusions[day_index] = np.asarray(
            [
                [
                    np.count_nonzero(mask & (labels == actual) & (predictions == estimate))
                    for estimate in CLASS_VALUES
                ]
                for actual in CLASS_VALUES
            ],
            dtype=np.int64,
        )
    for repetition in range(repetitions):
        sampled = rng.integers(0, unique_dates.size, size=unique_dates.size)
        rows = int(day_rows[sampled].sum())
        matrix = day_confusions[sampled].sum(axis=0)
        true_counts = matrix.sum(axis=1)
        predicted_counts = matrix.sum(axis=0)
        present = true_counts > 0
        values["multiclass_log_loss"][repetition] = day_loss_sums[sampled].sum() / rows
        values["balanced_accuracy"][repetition] = np.mean(
            np.diag(matrix)[present] / true_counts[present]
        )
        denominators = true_counts + predicted_counts
        values["macro_f1"][repetition] = np.mean(
            np.divide(
                2 * np.diag(matrix),
                denominators,
                out=np.zeros(3, dtype=np.float64),
                where=denominators != 0,
            )
        )
    alpha = (1.0 - BOOTSTRAP_CONFIDENCE_LEVEL) / 2.0
    return {
        "status": "completed",
        "method": "whole_trading_day_percentile_bootstrap",
        "trading_days": int(unique_dates.size),
        "repetitions": repetitions,
        "seed": seed,
        "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
        "intervals": {
            name: {
                "lower": float(np.quantile(samples, alpha)),
                "upper": float(np.quantile(samples, 1.0 - alpha)),
            }
            for name, samples in values.items()
        },
    }


__all__ = [
    "BOOTSTRAP_CONFIDENCE_LEVEL",
    "BOOTSTRAP_REPETITIONS",
    "CALIBRATION_BINS",
    "CLASS_NAMES",
    "CLASS_VALUES",
    "balanced_accuracy",
    "calibration_bins",
    "class_distribution",
    "classification_metrics",
    "confusion_matrix",
    "day_block_confidence_intervals",
    "macro_f1",
    "multiclass_log_loss",
    "predicted_labels",
    "validate_predictions",
]
