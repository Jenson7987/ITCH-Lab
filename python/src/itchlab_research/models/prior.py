"""Training-frequency prior baseline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.metrics import CLASS_VALUES, validate_predictions


@dataclass(frozen=True, slots=True)
class PriorClassifier:
    """A deterministic classifier that emits training-set class frequencies."""

    probabilities: NDArray[np.float64]

    def predict_proba(self, rows: int) -> NDArray[np.float64]:
        """Repeat the frozen prior for the requested number of rows."""
        if rows <= 0:
            raise ModelTrainingError(ErrorCode.EMPTY_DATASET, "Prediction rows must be positive.")
        return np.tile(self.probabilities, (rows, 1))


def fit_prior(labels: NDArray[np.int8]) -> PriorClassifier:
    """Fit exact down/flat/up frequencies from training labels only."""
    if labels.ndim != 1 or labels.size == 0 or not np.isin(labels, CLASS_VALUES).all():
        raise ModelTrainingError(ErrorCode.EMPTY_DATASET, "Prior training labels are invalid.")
    probabilities = np.asarray(
        [np.count_nonzero(labels == value) / labels.size for value in CLASS_VALUES],
        dtype=np.float64,
    )
    validate_predictions(labels[:1], probabilities.reshape(1, 3))
    return PriorClassifier(probabilities=probabilities)


__all__ = ["PriorClassifier", "fit_prior"]
