"""Immutable public models for authenticated predictive experiments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray

from itchlab_research.config import ExperimentConfig

PartitionName = Literal["train", "validation", "test"]
ModelName = Literal["prior", "logistic_regression", "hist_gradient_boosting"]
FileIdentity = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class DatasetArtefact:
    """One authenticated physical partition of a completed frozen dataset."""

    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    row_count: int
    partition: PartitionName
    trading_date: str
    symbol: str
    identity: FileIdentity


@dataclass(frozen=True, slots=True)
class PartitionedDataset:
    """Authenticated dataset metadata whose test rows are loaded only after selection."""

    dataset_id: str
    manifest_path: Path
    manifest_sha256: str
    manifest_identity: FileIdentity
    config_sha256: str
    identity_sha256: str
    manifest: dict[str, Any]
    logical_schema: pa.Schema
    feature_names: tuple[str, ...]
    primary_label: str
    artefacts: tuple[DatasetArtefact, ...]


@dataclass(frozen=True, slots=True)
class AuthenticatedExperiment:
    """A completed predictive experiment and its authenticated reporting evidence."""

    experiment_id: str
    manifest_path: Path
    manifest_sha256: str
    config: ExperimentConfig
    dataset: PartitionedDataset
    manifest: dict[str, Any]
    validation_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    diagnostics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PartitionData:
    """Dense model inputs and immutable keys for one frozen split."""

    partition: PartitionName
    features: NDArray[np.float64]
    labels: NDArray[np.int8]
    symbols: NDArray[np.str_]
    trading_dates: NDArray[np.str_]
    symbol_ids: NDArray[np.uint16]
    message_indices: NDArray[np.uint64]

    @property
    def rows(self) -> int:
        """Return the number of aligned rows."""
        return int(self.labels.size)


@dataclass(frozen=True, slots=True)
class FittedPreprocessor:
    """A train-fitted transformer plus safe deterministic diagnostics."""

    family: Literal["logistic_regression", "hist_gradient_boosting"]
    transformer: Any
    feature_names: tuple[str, ...]
    diagnostics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SelectedEstimator:
    """One selected train-fitted candidate and its validation evidence."""

    model_name: Literal["logistic_regression", "hist_gradient_boosting"]
    estimator: Any
    parameters: dict[str, int | float | str | bool]
    validation_log_loss: float
    validation_probabilities: NDArray[np.float64]
    candidate_evaluations: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ExperimentProgress:
    """One bounded progress observation between complete model operations."""

    stage: str
    candidates_completed: int
    candidates_total: int
    models_completed: int


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """A completed or safely reused immutable predictive experiment."""

    experiment_id: str
    status: str
    manifest_path: Path
    dataset_id: str
    prediction_rows: int
    selected_parameters: tuple[tuple[str, dict[str, Any]], ...]
    test_metrics: tuple[tuple[str, dict[str, Any]], ...]
    warnings: tuple[str, ...]
    reused: bool


__all__ = [
    "AuthenticatedExperiment",
    "DatasetArtefact",
    "ExperimentProgress",
    "ExperimentResult",
    "FileIdentity",
    "FittedPreprocessor",
    "ModelName",
    "PartitionData",
    "PartitionName",
    "PartitionedDataset",
    "SelectedEstimator",
]
