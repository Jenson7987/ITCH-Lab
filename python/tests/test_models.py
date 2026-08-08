"""TASK-020 baseline-selection and authenticated experiment integration tests."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import itchlab_research.models.service as model_service
from itchlab_research.config import (
    ExperimentConfig,
    HistGradientBoostingConfig,
    LogisticRegressionConfig,
    ModelConfig,
    PreprocessingConfig,
    PriorConfig,
)
from itchlab_research.datasets import build_dataset
from itchlab_research.errors import ErrorCode, ModelTrainingError
from itchlab_research.metrics import multiclass_log_loss
from itchlab_research.models import load_partitioned_dataset, train_baselines
from itchlab_research.models.gradient_boosting import fit_gradient_boosting_candidates
from itchlab_research.models.logistic import fit_logistic_candidates
from itchlab_research.models.models import PartitionData
from itchlab_research.models.preprocessing import fit_preprocessor, transform_partition
from itchlab_research.models.prior import fit_prior
from test_dataset import _config as dataset_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _experiment_config(tmp_path: Path, manifest: Path) -> ExperimentConfig:
    return ExperimentConfig(
        schema_version=1,
        dataset_manifest=manifest.relative_to(tmp_path).as_posix(),
        models=ModelConfig(
            prior=PriorConfig(enabled=True),
            logistic_regression=LogisticRegressionConfig(
                c_values=(0.01, 0.1, 1.0, 10.0),
                penalty="l2",
                solver="lbfgs",
                max_iter=2000,
            ),
            hist_gradient_boosting=HistGradientBoostingConfig(
                learning_rates=(0.05, 0.1),
                max_leaf_nodes=(15, 31),
                l2_regularization=(0.0, 1.0),
                max_iter=100,
            ),
        ),
        preprocessing=PreprocessingConfig(
            continuous_imputation="median",
            standardise_logistic=True,
            standardise_hist_gradient_boosting=False,
            unknown_symbol="all_zero",
        ),
        selection_metric="multiclass_log_loss",
        seed=7987,
    )


def _partition(
    name: str,
    features: np.ndarray[Any, np.dtype[np.float64]],
    labels: np.ndarray[Any, np.dtype[np.int8]],
    symbols: np.ndarray[Any, np.dtype[np.str_]],
) -> PartitionData:
    rows = labels.size
    return PartitionData(
        partition=name,  # type: ignore[arg-type]
        features=features,
        labels=labels,
        symbols=symbols,
        trading_dates=np.asarray(["2019-01-30"] * rows, dtype=np.str_),
        symbol_ids=np.ones(rows, dtype=np.uint16),
        message_indices=np.arange(rows, dtype=np.uint64),
    )


def test_ut_model_001_training_only_preprocessing_and_required_baselines() -> None:
    rng = np.random.default_rng(7987)
    training_signal = np.repeat(np.asarray([-2.0, 0.0, 2.0]), 40)
    validation_signal = np.repeat(np.asarray([-1.8, 0.1, 1.8]), 15)
    training_labels = np.repeat(np.asarray([-1, 0, 1], dtype=np.int8), 40)
    validation_labels = np.repeat(np.asarray([-1, 0, 1], dtype=np.int8), 15)
    training = _partition(
        "train",
        np.column_stack((training_signal, rng.normal(size=120))),
        training_labels,
        np.asarray(["AAPL", "MSFT"] * 60, dtype=np.str_),
    )
    validation = _partition(
        "validation",
        np.column_stack((validation_signal, rng.normal(size=45))),
        validation_labels,
        np.full(45, "UNSEEN", dtype=np.str_),
    )

    logistic_preprocessor = fit_preprocessor(
        training, ("signal", "noise"), family="logistic_regression"
    )
    boosting_preprocessor = fit_preprocessor(
        training, ("signal", "noise"), family="hist_gradient_boosting"
    )
    logistic_train = transform_partition(logistic_preprocessor, training)
    logistic_validation = transform_partition(logistic_preprocessor, validation)
    boosting_train = transform_partition(boosting_preprocessor, training)
    boosting_validation = transform_partition(boosting_preprocessor, validation)
    prior = fit_prior(training.labels)
    logistic = fit_logistic_candidates(
        logistic_train,
        training.labels,
        logistic_validation,
        validation.labels,
        _experiment_config(Path("."), Path("dataset-manifest.json")).models.logistic_regression,
        seed=7987,
        cancel_requested=lambda: False,
    )
    boosting = fit_gradient_boosting_candidates(
        boosting_train,
        training.labels,
        boosting_validation,
        validation.labels,
        _experiment_config(Path("."), Path("dataset-manifest.json")).models.hist_gradient_boosting,
        seed=7987,
        cancel_requested=lambda: False,
    )

    assert logistic_preprocessor.diagnostics["fit_partition"] == "train"
    assert logistic_preprocessor.diagnostics["fit_rows"] == 120
    assert logistic_preprocessor.diagnostics["fit_dates"] == ["2019-01-30"]
    assert np.count_nonzero(logistic_validation[:, -2:]) == 0
    assert np.count_nonzero(boosting_validation[:, -2:]) == 0
    assert logistic.validation_log_loss < multiclass_log_loss(
        validation.labels, prior.predict_proba(validation.rows)
    )
    assert boosting.validation_log_loss < multiclass_log_loss(
        validation.labels, prior.predict_proba(validation.rows)
    )
    assert len(logistic.candidate_evaluations) == 4
    assert len(boosting.candidate_evaluations) == 8

    no_signal = _partition(
        "train",
        np.zeros((120, 2), dtype=np.float64),
        training_labels,
        np.full(120, "AAPL", dtype=np.str_),
    )
    no_signal_values = transform_partition(
        fit_preprocessor(no_signal, ("first", "second"), family="logistic_regression"),
        no_signal,
    )
    no_signal_model = fit_logistic_candidates(
        no_signal_values,
        no_signal.labels,
        no_signal_values,
        no_signal.labels,
        _experiment_config(Path("."), Path("dataset-manifest.json")).models.logistic_regression,
        seed=7987,
        cancel_requested=lambda: False,
    )
    no_signal_boosting_values = transform_partition(
        fit_preprocessor(
            no_signal,
            ("first", "second"),
            family="hist_gradient_boosting",
        ),
        no_signal,
    )
    no_signal_boosting = fit_gradient_boosting_candidates(
        no_signal_boosting_values,
        no_signal.labels,
        no_signal_boosting_values,
        no_signal.labels,
        _experiment_config(Path("."), Path("dataset-manifest.json")).models.hist_gradient_boosting,
        seed=7987,
        cancel_requested=lambda: False,
    )
    assert np.isfinite(no_signal_model.validation_probabilities).all()
    assert np.allclose(no_signal_model.validation_probabilities.sum(axis=1), 1.0)
    assert np.isfinite(no_signal_boosting.validation_probabilities).all()
    assert np.allclose(no_signal_boosting.validation_probabilities.sum(axis=1), 1.0)


def test_task_020_candidate_ties_use_documented_conservative_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClassifier:
        def __init__(self, **parameters: Any) -> None:
            self.parameters = parameters
            self.classes_ = np.asarray([-1, 0, 1], dtype=np.int8)

        def fit(self, values: np.ndarray[Any, Any], labels: np.ndarray[Any, Any]) -> None:
            del values, labels

        def predict_proba(self, values: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
            return np.full((values.shape[0], 3), 1 / 3, dtype=np.float64)

    monkeypatch.setattr("itchlab_research.models.logistic.LogisticRegression", FakeClassifier)
    monkeypatch.setattr(
        "itchlab_research.models.gradient_boosting.HistGradientBoostingClassifier",
        FakeClassifier,
    )
    values = np.zeros((6, 1), dtype=np.float64)
    labels = np.asarray([-1, 0, 1, -1, 0, 1], dtype=np.int8)
    config = _experiment_config(Path("."), Path("dataset-manifest.json"))

    logistic = fit_logistic_candidates(
        values,
        labels,
        values,
        labels,
        config.models.logistic_regression,
        seed=7987,
        cancel_requested=lambda: False,
    )
    boosting = fit_gradient_boosting_candidates(
        values,
        labels,
        values,
        labels,
        config.models.hist_gradient_boosting,
        seed=7987,
        cancel_requested=lambda: False,
    )

    assert logistic.parameters["C"] == 0.01
    assert boosting.parameters == {
        "learning_rate": 0.05,
        "max_leaf_nodes": 15,
        "l2_regularization": 1.0,
        "max_iter": 100,
        "early_stopping": False,
    }

    class OneFailureClassifier(FakeClassifier):
        def fit(self, values: np.ndarray[Any, Any], labels: np.ndarray[Any, Any]) -> None:
            super().fit(values, labels)
            if self.parameters["C"] == 0.01:
                raise ValueError("synthetic candidate failure")

    monkeypatch.setattr("itchlab_research.models.logistic.LogisticRegression", OneFailureClassifier)
    visible_failure = fit_logistic_candidates(
        values,
        labels,
        values,
        labels,
        config.models.logistic_regression,
        seed=7987,
        cancel_requested=lambda: False,
    )
    assert visible_failure.candidate_evaluations[0] == {
        "parameters": {
            "C": 0.01,
            "penalty": "l2",
            "solver": "lbfgs",
            "max_iter": 2000,
        },
        "status": "failed",
        "error_code": "ERR_MODEL_TRAINING",
        "reason": "fit_or_prediction_failed",
    }


def test_it_009_publishes_reproducible_experiment_and_never_serialises_models(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversion_manifest = dataset_conversion_factory()
    dataset_result = build_dataset(
        dataset_config(tmp_path, conversion_manifest), base_directory=tmp_path
    )
    config = _experiment_config(tmp_path, dataset_result.manifest_path)
    dataset = load_partitioned_dataset(config, base_directory=tmp_path)
    loaded_partitions: list[str] = []
    original_load = model_service._load_partition

    def track_partition(*args: Any, **kwargs: Any) -> PartitionData:
        partition = args[1]
        loaded_partitions.append(partition)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(model_service, "_load_partition", track_partition)

    first = train_baselines(dataset, config, base_directory=tmp_path)
    reused = train_baselines(dataset, config, base_directory=tmp_path)

    assert first.status == "completed"
    assert first.reused is False
    assert reused.reused is True
    assert reused.experiment_id == first.experiment_id
    assert first.prediction_rows == 18
    assert loaded_partitions == ["train", "validation", "test"]

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["test_evaluation_count"] == 1
    assert [item["model_name"] for item in manifest["selection"]["models"]] == [
        "prior",
        "logistic_regression",
        "hist_gradient_boosting",
    ]
    assert manifest["config"]["seed"] == 7987
    assert (
        manifest["parent"]["manifest_sha256"]
        == hashlib.sha256(dataset_result.manifest_path.read_bytes()).hexdigest()
    )

    artefacts = {item["kind"]: item for item in manifest["artefacts"]}
    assert set(artefacts) == {
        "predictions",
        "metrics_validation",
        "metrics_test",
        "diagnostics",
    }
    predictions = pq.read_table(first.manifest_path.parent / artefacts["predictions"]["path"])
    assert predictions.num_rows == 18
    assert set(predictions.column("model_name").to_pylist()) == {
        "prior",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert (
        len(
            set(
                zip(
                    predictions.column("trading_date").to_pylist(),
                    predictions.column("symbol_id").to_pylist(),
                    predictions.column("message_index").to_pylist(),
                    predictions.column("model_name").to_pylist(),
                    strict=True,
                )
            )
        )
        == 18
    )

    validation_metrics = json.loads(
        (first.manifest_path.parent / artefacts["metrics_validation"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    test_metrics = json.loads(
        (first.manifest_path.parent / artefacts["metrics_test"]["path"]).read_text(encoding="utf-8")
    )
    assert validation_metrics["partition"] == "validation"
    assert test_metrics["partition"] == "test"
    for model in test_metrics["models"]:
        assert set(model["metrics"]) == {
            "multiclass_log_loss",
            "balanced_accuracy",
            "macro_f1",
            "confusion_matrix",
        }
        assert model["calibration"]["bin_count"] == 10
        assert model["confidence_intervals"]["status"] == "omitted"
        assert [item["symbol"] for item in model["by_symbol"]] == ["AAPL"]

    diagnostics_path = first.manifest_path.parent / artefacts["diagnostics"]["path"]
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["serialization_policy"] == "no_model_serialization_retrain_from_manifest"
    for model in diagnostics["models"][1:]:
        assert model["preprocessing"]["fit_partition"] == "train"
        assert model["preprocessing"]["fit_dates"] == ["2019-01-30"]
    assert str(tmp_path) not in diagnostics_path.read_text(encoding="utf-8")
    assert not list(first.manifest_path.parent.glob("*.pkl"))
    assert not list(first.manifest_path.parent.glob("*.joblib"))

    cancelled = threading.Event()
    with pytest.raises(ModelTrainingError) as cancellation:
        train_baselines(
            dataset,
            config,
            base_directory=tmp_path,
            force_new_run=True,
            cancel_requested=cancelled.is_set,
            progress=lambda progress: cancelled.set() if progress.models_completed == 1 else None,
        )
    assert cancellation.value.code is ErrorCode.CANCELLED
    assert cancellation.value.partial_exists is True
    assert list((tmp_path / "runs" / "experiment").glob("*.partial"))


def test_task_020_reuse_rejects_output_tampering(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    conversion_manifest = dataset_conversion_factory()
    dataset_result = build_dataset(
        dataset_config(tmp_path, conversion_manifest), base_directory=tmp_path
    )
    config = _experiment_config(tmp_path, dataset_result.manifest_path)
    dataset = load_partitioned_dataset(config, base_directory=tmp_path)
    result = train_baselines(dataset, config, base_directory=tmp_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    metrics = next(item for item in manifest["artefacts"] if item["kind"] == "metrics_test")
    path = result.manifest_path.parent / metrics["path"]
    path.write_bytes(path.read_bytes() + b"tampered")

    with pytest.raises(ModelTrainingError) as captured:
        train_baselines(dataset, config, base_directory=tmp_path)

    assert captured.value.code is ErrorCode.HASH_MISMATCH


def test_task_020_loader_rejects_dataset_supporting_artefact_tampering(
    tmp_path: Path,
    dataset_conversion_factory: Any,
) -> None:
    conversion_manifest = dataset_conversion_factory()
    dataset_result = build_dataset(
        dataset_config(tmp_path, conversion_manifest), base_directory=tmp_path
    )
    manifest = json.loads(dataset_result.manifest_path.read_text(encoding="utf-8"))
    supporting = dataset_result.manifest_path.parent / manifest["supporting_artefacts"][0]["path"]
    supporting.write_bytes(supporting.read_bytes() + b"tampered")

    with pytest.raises(ModelTrainingError) as captured:
        load_partitioned_dataset(
            _experiment_config(tmp_path, dataset_result.manifest_path),
            base_directory=tmp_path,
        )

    assert captured.value.code is ErrorCode.HASH_MISMATCH


def test_task_020_manifest_schema_is_packaged_and_strict() -> None:
    public_path = REPOSITORY_ROOT / "schemas" / "experiment-manifest.schema.json"
    packaged_path = (
        REPOSITORY_ROOT
        / "python"
        / "src"
        / "itchlab_research"
        / "_schemas"
        / "experiment-manifest.schema.json"
    )
    assert public_path.read_bytes() == packaged_path.read_bytes()
    schema = json.loads(public_path.read_text(encoding="utf-8"))
    config_schema = json.loads(
        (REPOSITORY_ROOT / "schemas" / "experiment-config.schema.json").read_text("utf-8")
    )
    validator = Draft202012Validator(
        schema,
        registry=Registry().with_resources(
            [
                (config_schema["$id"], Resource.from_contents(config_schema)),
                (schema["$id"], Resource.from_contents(schema)),
            ]
        ),
        format_checker=FormatChecker(),
    )
    Draft202012Validator.check_schema(schema)
    assert list(validator.iter_errors({}))
    assert schema["additionalProperties"] is False
    unknown = copy.deepcopy(schema)
    assert unknown["properties"]["config"]["$ref"].endswith("experiment-config.schema.json")
