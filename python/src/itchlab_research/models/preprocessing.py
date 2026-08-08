"""Training-only preprocessing for the two required pooled model families."""

from __future__ import annotations

import math
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.preprocessing import (  # type: ignore[import-untyped]
    OneHotEncoder,
    StandardScaler,
)

from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.models.models import FittedPreprocessor, PartitionData


class _Transformer:
    def __init__(
        self,
        imputer: SimpleImputer,
        scaler: StandardScaler | None,
        encoder: OneHotEncoder,
    ) -> None:
        self.imputer = imputer
        self.scaler = scaler
        self.encoder = encoder

    def transform(self, data: PartitionData) -> NDArray[np.float64]:
        numeric = cast(NDArray[np.float64], self.imputer.transform(data.features))
        if self.scaler is not None:
            numeric = cast(NDArray[np.float64], self.scaler.transform(numeric))
        categorical = cast(NDArray[np.float64], self.encoder.transform(data.symbols.reshape(-1, 1)))
        output = np.concatenate((numeric, categorical), axis=1)
        if output.shape[0] != data.rows or not np.isfinite(output).all():
            raise ModelTrainingError(
                ErrorCode.MODEL_TRAINING,
                "Preprocessing produced non-finite or misaligned model input.",
            )
        return output


def fit_preprocessor(
    training: PartitionData,
    numeric_feature_names: tuple[str, ...],
    *,
    family: Literal["logistic_regression", "hist_gradient_boosting"],
) -> FittedPreprocessor:
    """Fit imputation, optional scaling and symbol encoding on training rows only."""
    if training.partition != "train" or training.rows == 0:
        raise ModelTrainingError(
            ErrorCode.LEAKAGE_GUARD,
            "Predictive preprocessing may fit only non-empty training rows.",
        )
    if training.features.shape != (training.rows, len(numeric_feature_names)):
        raise ModelTrainingError(ErrorCode.SCHEMA_VERSION, "Training feature matrix is invalid.")
    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    try:
        imputed = cast(NDArray[np.float64], imputer.fit_transform(training.features))
    except (MemoryError, ValueError) as error:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING, "Training-only median imputation failed."
        ) from error
    statistics = cast(NDArray[np.float64], imputer.statistics_)
    if statistics.shape != (len(numeric_feature_names),) or not np.isfinite(statistics).all():
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING,
            "A training feature has no finite median for imputation.",
        )
    scaler: StandardScaler | None = None
    if family == "logistic_regression":
        scaler = StandardScaler()
        try:
            scaler.fit(imputed)
        except (MemoryError, ValueError) as error:
            raise ModelTrainingError(
                ErrorCode.MODEL_TRAINING, "Training-only standardisation failed."
            ) from error
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float64)
    try:
        encoder.fit(training.symbols.reshape(-1, 1))
    except (MemoryError, ValueError) as error:
        raise ModelTrainingError(
            ErrorCode.MODEL_TRAINING, "Training-only symbol encoding failed."
        ) from error
    categories = tuple(str(value) for value in encoder.categories_[0])
    if not categories:
        raise ModelTrainingError(ErrorCode.EMPTY_DATASET, "Training symbols are empty.")
    encoded_names = tuple(f"symbol={value}" for value in categories)
    all_missing_features = [
        name
        for index, name in enumerate(numeric_feature_names)
        if not np.isfinite(training.features[:, index]).any()
    ]
    transformer = _Transformer(imputer, scaler, encoder)
    diagnostics: dict[str, Any] = {
        "fit_partition": "train",
        "fit_rows": training.rows,
        "fit_dates": sorted(set(str(value) for value in training.trading_dates)),
        "continuous_imputation": "median",
        "numeric_feature_names": list(numeric_feature_names),
        "numeric_medians": [float(value) for value in statistics],
        "all_missing_numeric_features": all_missing_features,
        "all_missing_fallback": 0.0,
        "symbol_categories": list(categories),
        "unknown_symbol": "all_zero",
        "standardised": scaler is not None,
    }
    if scaler is not None:
        means = cast(NDArray[np.float64], scaler.mean_)
        scales = cast(NDArray[np.float64], scaler.scale_)
        if not all(math.isfinite(float(value)) for value in (*means, *scales)):
            raise ModelTrainingError(
                ErrorCode.MODEL_TRAINING, "Training standardisation statistics are not finite."
            )
        diagnostics["numeric_means"] = [float(value) for value in means]
        diagnostics["numeric_scales"] = [float(value) for value in scales]
    return FittedPreprocessor(
        family=family,
        transformer=transformer,
        feature_names=(*numeric_feature_names, *encoded_names),
        diagnostics=diagnostics,
    )


def transform_partition(fitted: FittedPreprocessor, data: PartitionData) -> NDArray[np.float64]:
    """Transform a frozen split without changing fitted training statistics."""
    transformer = cast(_Transformer, fitted.transformer)
    return transformer.transform(data)


__all__ = ["fit_preprocessor", "transform_partition"]
