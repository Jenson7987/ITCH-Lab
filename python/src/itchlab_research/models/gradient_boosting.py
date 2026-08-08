"""Fixed-grid histogram-gradient-boosting selection."""

from __future__ import annotations

from collections.abc import Callable
from itertools import product
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]

from itchlab_research.config import HistGradientBoostingConfig
from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.metrics import CLASS_VALUES, multiclass_log_loss, validate_predictions
from itchlab_research.models.models import SelectedEstimator

_TIE_TOLERANCE = 1e-6


def _probabilities(
    estimator: HistGradientBoostingClassifier, values: NDArray[np.float64]
) -> NDArray[np.float64]:
    probabilities = cast(NDArray[np.float64], estimator.predict_proba(values))
    classes = tuple(int(value) for value in estimator.classes_)
    if classes != CLASS_VALUES:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING,
            "Histogram gradient boosting did not preserve the fixed three-class order.",
        )
    return probabilities


def _tie_key(parameters: dict[str, int | float | str | bool]) -> tuple[int, float, float]:
    return (
        cast(int, parameters["max_leaf_nodes"]),
        -cast(float, parameters["l2_regularization"]),
        cast(float, parameters["learning_rate"]),
    )


def fit_gradient_boosting_candidates(
    training_features: NDArray[np.float64],
    training_labels: NDArray[np.int8],
    validation_features: NDArray[np.float64],
    validation_labels: NDArray[np.int8],
    config: HistGradientBoostingConfig,
    *,
    seed: int,
    cancel_requested: Callable[[], bool],
    candidate_completed: Callable[[int], None] | None = None,
) -> SelectedEstimator:
    """Fit the complete declared grid and apply the conservative tie-break order."""
    evaluations: list[dict[str, Any]] = []
    selected: (
        tuple[
            float,
            dict[str, int | float | str | bool],
            HistGradientBoostingClassifier,
            NDArray[np.float64],
        ]
        | None
    ) = None
    for learning_rate, max_leaf_nodes, l2_regularization in product(
        config.learning_rates, config.max_leaf_nodes, config.l2_regularization
    ):
        if cancel_requested():
            raise ModelTrainingError(ErrorCode.CANCELLED, "Model selection was cancelled.")
        parameters: dict[str, int | float | str | bool] = {
            "learning_rate": learning_rate,
            "max_leaf_nodes": max_leaf_nodes,
            "l2_regularization": l2_regularization,
            "max_iter": config.max_iter,
            "early_stopping": False,
        }
        estimator = HistGradientBoostingClassifier(
            loss="log_loss",
            learning_rate=learning_rate,
            max_leaf_nodes=max_leaf_nodes,
            l2_regularization=l2_regularization,
            max_iter=config.max_iter,
            early_stopping=False,
            random_state=seed,
        )
        try:
            estimator.fit(training_features, training_labels)
            probabilities = _probabilities(estimator, validation_features)
            validate_predictions(validation_labels, probabilities)
            loss = multiclass_log_loss(validation_labels, probabilities)
        except MemoryError as error:
            raise ModelTrainingError(
                ErrorCode.MODEL_TRAINING, "Gradient-boosting fitting exhausted memory."
            ) from error
        except (FloatingPointError, ValueError):
            evaluations.append(
                {
                    "parameters": parameters,
                    "status": "failed",
                    "error_code": ErrorCode.MODEL_TRAINING.value,
                    "reason": "fit_or_prediction_failed",
                }
            )
            if candidate_completed is not None:
                candidate_completed(len(evaluations))
            continue
        evaluations.append(
            {"parameters": parameters, "status": "completed", "validation_log_loss": loss}
        )
        if candidate_completed is not None:
            candidate_completed(len(evaluations))
        if (
            selected is None
            or loss < selected[0] - _TIE_TOLERANCE
            or (
                abs(loss - selected[0]) <= _TIE_TOLERANCE
                and _tie_key(parameters) < _tie_key(selected[1])
            )
        ):
            selected = (loss, parameters, estimator, probabilities)
    if selected is None:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING,
            "Every histogram-gradient-boosting candidate failed.",
        )
    return SelectedEstimator(
        model_name="hist_gradient_boosting",
        estimator=selected[2],
        parameters=selected[1],
        validation_log_loss=selected[0],
        validation_probabilities=selected[3],
        candidate_evaluations=tuple(evaluations),
    )


def gradient_boosting_probabilities(
    selected: SelectedEstimator, values: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Predict with one frozen selected histogram-gradient-boosting estimator."""
    return _probabilities(cast(HistGradientBoostingClassifier, selected.estimator), values)


__all__ = ["fit_gradient_boosting_candidates", "gradient_boosting_probabilities"]
