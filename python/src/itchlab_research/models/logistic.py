"""Fixed-grid multinomial logistic-regression selection."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from itchlab_research.config import LogisticRegressionConfig
from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.metrics import CLASS_VALUES, multiclass_log_loss, validate_predictions
from itchlab_research.models.models import SelectedEstimator

_TIE_TOLERANCE = 1e-6


def _probabilities(
    estimator: LogisticRegression, values: NDArray[np.float64]
) -> NDArray[np.float64]:
    probabilities = cast(NDArray[np.float64], estimator.predict_proba(values))
    classes = tuple(int(value) for value in estimator.classes_)
    if classes != CLASS_VALUES:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING,
            "Logistic regression did not preserve the fixed three-class order.",
        )
    return probabilities


def fit_logistic_candidates(
    training_features: NDArray[np.float64],
    training_labels: NDArray[np.int8],
    validation_features: NDArray[np.float64],
    validation_labels: NDArray[np.int8],
    config: LogisticRegressionConfig,
    *,
    seed: int,
    cancel_requested: Callable[[], bool],
    candidate_completed: Callable[[int], None] | None = None,
) -> SelectedEstimator:
    """Fit every declared C candidate and select minimum validation log loss."""
    evaluations: list[dict[str, Any]] = []
    selected: tuple[float, float, LogisticRegression, NDArray[np.float64]] | None = None
    for value in config.c_values:
        if cancel_requested():
            raise ModelTrainingError(ErrorCode.CANCELLED, "Model selection was cancelled.")
        parameters: dict[str, int | float | str | bool] = {
            "C": value,
            "penalty": config.penalty,
            "solver": config.solver,
            "max_iter": config.max_iter,
        }
        estimator = LogisticRegression(
            C=value,
            l1_ratio=0.0,
            solver=config.solver,
            max_iter=config.max_iter,
            random_state=seed,
        )
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                estimator.fit(training_features, training_labels)
            if any(issubclass(item.category, ConvergenceWarning) for item in caught):
                evaluations.append(
                    {
                        "parameters": parameters,
                        "status": "failed",
                        "error_code": ErrorCode.MODEL_TRAINING.value,
                        "reason": "did_not_converge",
                    }
                )
                if candidate_completed is not None:
                    candidate_completed(len(evaluations))
                continue
            probabilities = _probabilities(estimator, validation_features)
            validate_predictions(validation_labels, probabilities)
            loss = multiclass_log_loss(validation_labels, probabilities)
        except MemoryError as error:
            raise ModelTrainingError(
                ErrorCode.MODEL_TRAINING, "Logistic-regression fitting exhausted memory."
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
            or (abs(loss - selected[0]) <= _TIE_TOLERANCE and value < selected[1])
        ):
            selected = (loss, value, estimator, probabilities)
    if selected is None:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING,
            "Every logistic-regression candidate failed.",
        )
    return SelectedEstimator(
        model_name="logistic_regression",
        estimator=selected[2],
        parameters=next(
            cast(dict[str, int | float | str | bool], item["parameters"])
            for item in evaluations
            if item.get("status") == "completed"
            and cast(dict[str, Any], item["parameters"])["C"] == selected[1]
        ),
        validation_log_loss=selected[0],
        validation_probabilities=selected[3],
        candidate_evaluations=tuple(evaluations),
    )


def logistic_probabilities(
    selected: SelectedEstimator, values: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Predict with one frozen selected logistic estimator."""
    return _probabilities(cast(LogisticRegression, selected.estimator), values)


__all__ = ["fit_logistic_candidates", "logistic_probabilities"]
