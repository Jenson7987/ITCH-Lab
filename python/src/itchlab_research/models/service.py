"""Authenticated dataset loading, baseline training and immutable experiment publication."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, Final, TypeAlias, cast
from urllib.parse import quote, unquote

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import sklearn  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from sklearn.ensemble import (  # type: ignore[import-untyped]
    HistGradientBoostingClassifier,
)
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from itchlab_research import __version__
from itchlab_research.canonical_json import canonical_json_bytes, config_document, config_hashes
from itchlab_research.config import DatasetConfig, ExperimentConfig, parse_config
from itchlab_research.datasets.features import feature_catalogue_document
from itchlab_research.datasets.splits import dataset_schema
from itchlab_research.errors import ConfigValidationError, ErrorCode, ModelTrainingError
from itchlab_research.metrics import (
    CLASS_NAMES,
    CLASS_VALUES,
    calibration_bins,
    class_distribution,
    classification_metrics,
    day_block_confidence_intervals,
    multiclass_log_loss,
    validate_predictions,
)
from itchlab_research.models.gradient_boosting import (
    fit_gradient_boosting_candidates,
    gradient_boosting_probabilities,
)
from itchlab_research.models.logistic import fit_logistic_candidates, logistic_probabilities
from itchlab_research.models.models import (
    AuthenticatedExperiment,
    DatasetArtefact,
    ExperimentProgress,
    ExperimentResult,
    FileIdentity,
    PartitionData,
    PartitionedDataset,
    PartitionName,
    SelectedEstimator,
)
from itchlab_research.models.preprocessing import fit_preprocessor, transform_partition
from itchlab_research.models.prior import PriorClassifier, fit_prior

_SCHEMA_VERSION: Final = 1
_MANIFEST_NAME: Final = "experiment-manifest.json"
_IDENTITY_MARKER: Final = "identity.sha256"
_EXPERIMENT_RUN_ROOT: Final = Path("runs") / "experiment"
_MAX_JSON_BYTES: Final = 16 << 20
_HASH_CHUNK_BYTES: Final = 1 << 20
_INPUT_BATCH_ROWS: Final = 65_536
_ROW_GROUP_ROWS: Final = 65_536
_PARTITION_KEYS: Final = ("partition", "trading_date", "symbol")
_MODEL_ORDER: Final = ("prior", "logistic_regression", "hist_gradient_boosting")
_RUN_ID_PATTERN: Final = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-[0-9a-f]{12}$")

CancelCheck: TypeAlias = Callable[[], bool]
ProgressCallback: TypeAlias = Callable[[ExperimentProgress], None]


@dataclass(frozen=True, slots=True)
class _OutputArtefact:
    kind: str
    path: str
    sha256: str
    size_bytes: int
    row_count: int


@dataclass(frozen=True, slots=True)
class _RunPaths:
    root: Path
    lock_path: Path
    staging_directory: Path
    final_directory: Path


def _fail(code: ErrorCode, message: str, *, partial_exists: bool = False) -> ModelTrainingError:
    return ModelTrainingError(code, message, partial_exists=partial_exists)


def _identity(status_result: os.stat_result) -> FileIdentity:
    return (
        status_result.st_dev,
        status_result.st_ino,
        status_result.st_size,
        status_result.st_mtime_ns,
        status_result.st_ctime_ns,
    )


def _check_cancel(cancel_requested: CancelCheck) -> None:
    if cancel_requested():
        raise _fail(ErrorCode.CANCELLED, "Predictive training was cancelled at a safe boundary.")


def _path_has_symlink(path: Path) -> bool:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            status_result = current.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "A path component could not be inspected.") from error
        if stat.S_ISLNK(status_result.st_mode):
            return True
    return False


def _safe_relative_path(value: str) -> bool:
    normalised = value.replace("\\", "/")
    path = Path(normalised)
    segments = normalised.split("/")
    return bool(
        normalised
        and not normalised.startswith("/")
        and not (len(normalised) >= 2 and normalised[1] == ":")
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} and not part.endswith(".partial") for part in segments)
    )


def _read_regular_file(path: Path, maximum_bytes: int) -> tuple[bytes, FileIdentity]:
    if any(component.endswith(".partial") for component in path.parts) or _path_has_symlink(path):
        raise _fail(ErrorCode.PARTIAL_ARTEFACT, "A partial or symlinked input is not accepted.")
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A required input is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
                raise _fail(ErrorCode.INPUT_PATH, "A required input is not a bounded regular file.")
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(
                ErrorCode.INPUT_PATH, "A required input could not be read safely."
            ) from error
        if (
            len(content) != before.st_size
            or len(content) > maximum_bytes
            or _identity(before) != _identity(after)
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "An input changed or exceeded its size bound.")
        return content, _identity(after)


def _reject_duplicate_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _strict_json(content: bytes, *, description: str) -> dict[str, Any]:
    try:
        document = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_names,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{description} is not strict JSON/I-JSON."
        ) from error
    if not isinstance(document, dict):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{description} root is not an object.")
    return cast(dict[str, Any], document)


@lru_cache(maxsize=2)
def _manifest_validator(kind: str) -> Draft202012Validator:
    schema_names = [
        "dataset-config.schema.json",
        "dataset-manifest.schema.json",
        "experiment-config.schema.json",
        "experiment-manifest.schema.json",
    ]
    resources: list[tuple[str, Resource[Any]]] = []
    documents: dict[str, dict[str, Any]] = {}
    for name in schema_names:
        document = cast(
            dict[str, Any],
            json.loads(files("itchlab_research._schemas").joinpath(name).read_text("utf-8")),
        )
        documents[name] = document
        resources.append((cast(str, document["$id"]), Resource.from_contents(document)))
    return Draft202012Validator(
        documents[f"{kind}-manifest.schema.json"],
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _validate_manifest(document: Mapping[str, Any], kind: str) -> None:
    if list(_manifest_validator(kind).iter_errors(document)):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} manifest violates schema v1.")


def _sha256_file(path: Path, cancel_requested: CancelCheck) -> tuple[str, int, FileIdentity]:
    if _path_has_symlink(path):
        raise _fail(ErrorCode.INPUT_PATH, "A symlinked artefact is not accepted.")
    digest = hashlib.sha256()
    observed = 0
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A required artefact is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _fail(ErrorCode.INPUT_PATH, "A required artefact is not a regular file.")
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                _check_cancel(cancel_requested)
                digest.update(chunk)
                observed += len(chunk)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(ErrorCode.HASH_MISMATCH, "An artefact changed while hashing.") from error
        if observed != before.st_size or _identity(before) != _identity(after):
            raise _fail(ErrorCode.HASH_MISMATCH, "An artefact changed while hashing.")
    return digest.hexdigest(), observed, _identity(after)


def _stage_identity(
    domain: bytes,
    parent_hashes: Sequence[str],
    identity_config_sha256: str,
    tool_sha256: str,
) -> str:
    try:
        digest = hashlib.sha256(domain + b"\0")
        for value in parent_hashes:
            digest.update(bytes.fromhex(value))
        digest.update(bytes.fromhex(identity_config_sha256))
        digest.update(bytes.fromhex(tool_sha256))
    except ValueError as error:
        raise _fail(
            ErrorCode.HASH_MISMATCH, "A stage identity input is not valid SHA-256."
        ) from error
    digest.update(_SCHEMA_VERSION.to_bytes(2, "big"))
    return digest.hexdigest()


def _package_content_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    package_files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix in {".py", ".json"} or path.name == "py.typed")
    )
    digest = hashlib.sha256(b"itchlab-python-package-content-v1\0")
    for path in package_files:
        content = path.read_bytes()
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _schema_descriptor(schema: pa.Schema) -> dict[str, Any]:
    fields_value = [
        {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
    return {
        "fields": fields_value,
        "sha256": hashlib.sha256(canonical_json_bytes(fields_value)).hexdigest(),
    }


def _physical_dataset_schema(schema: pa.Schema) -> pa.Schema:
    return pa.schema([field for field in schema if field.name not in _PARTITION_KEYS])


def _parse_dataset_config(document: Mapping[str, Any]) -> DatasetConfig:
    try:
        parsed = parse_config(json.dumps(document["config"], allow_nan=False), "dataset")
    except (ConfigValidationError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset manifest config is invalid.") from error
    if not isinstance(parsed, DatasetConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset manifest config type is invalid.")
    return parsed


def _parse_dataset_path(relative: str) -> tuple[PartitionName, str, str]:
    if not _safe_relative_path(relative):
        raise _fail(ErrorCode.INPUT_PATH, "Dataset manifest names an unsafe child path.")
    parts = PurePosixPath(relative).parts
    if len(parts) != 5 or parts[0] != "dataset" or parts[4] != "part-0.parquet":
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet path shape is invalid.")
    partition = parts[1].removeprefix("partition=")
    date_text = parts[2].removeprefix("trading_date=")
    encoded_symbol = parts[3].removeprefix("symbol=")
    symbol = unquote(encoded_symbol)
    if (
        partition not in {"train", "validation", "test"}
        or parts[1] != f"partition={partition}"
        or parts[2] != f"trading_date={date_text}"
        or parts[3] != f"symbol={encoded_symbol}"
        or quote(symbol, safe="A-Za-z0-9._~-") != encoded_symbol
    ):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet partition metadata is invalid.")
    try:
        date.fromisoformat(date_text)
    except ValueError as error:
        raise _fail(ErrorCode.TRADING_DATE, "Dataset Parquet date is invalid.") from error
    return cast(PartitionName, partition), date_text, symbol


def _validate_dataset_artefact(
    directory: Path,
    entry: Mapping[str, Any],
    schema: pa.Schema,
    primary_label: str,
    feature_names: tuple[str, ...],
    cancel_requested: CancelCheck,
) -> tuple[DatasetArtefact, dict[str, int]]:
    relative = cast(str, entry["path"])
    partition, trading_date, symbol = _parse_dataset_path(relative)
    if (
        entry["partition"] != partition
        or entry["trading_date"] != trading_date
        or entry["symbol"] != symbol
    ):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset path and partition metadata disagree.")
    path = directory / relative
    digest, size, identity_value = _sha256_file(path, cancel_requested)
    if digest != entry["sha256"] or size != entry["size_bytes"]:
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset Parquet hash or size is invalid.")
    counts = {name: 0 for name in CLASS_NAMES}
    previous: int | None = None
    try:
        parquet_file = pq.ParquetFile(path)
        if parquet_file.schema_arrow != _physical_dataset_schema(schema):
            raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet schema is invalid.")
        if parquet_file.metadata.num_rows != entry["row_count"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset Parquet row count is invalid.")
        columns = ["message_index", primary_label, *feature_names]
        for batch in parquet_file.iter_batches(
            batch_size=_INPUT_BATCH_ROWS, columns=columns, use_threads=False
        ):
            _check_cancel(cancel_requested)
            values = batch.to_pydict()
            for message_index in cast(list[int], values["message_index"]):
                if previous is not None and message_index <= previous:
                    raise _fail(ErrorCode.INVARIANT, "Dataset message indices are not increasing.")
                previous = message_index
            for label in cast(list[int | None], values[primary_label]):
                if label not in CLASS_VALUES:
                    raise _fail(ErrorCode.INVARIANT, "Dataset primary label is invalid.")
                counts[CLASS_NAMES[CLASS_VALUES.index(cast(int, label))]] += 1
            for name in feature_names:
                for value in cast(list[int | float | None], values[name]):
                    if value is not None and not np.isfinite(float(value)):
                        raise _fail(ErrorCode.INVARIANT, "Dataset feature is non-finite.")
    except ModelTrainingError:
        raise
    except (OSError, pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet content is invalid.") from error
    return (
        DatasetArtefact(
            path=path,
            relative_path=relative,
            sha256=digest,
            size_bytes=size,
            row_count=cast(int, entry["row_count"]),
            partition=partition,
            trading_date=trading_date,
            symbol=symbol,
            identity=identity_value,
        ),
        counts,
    )


def _validate_dataset_supporting_artefacts(
    directory: Path,
    document: Mapping[str, Any],
) -> None:
    expected_paths = {
        "feature_catalogue": "feature-catalogue.json",
        "data_quality": "data-quality.json",
    }
    expected_documents = {
        "feature_catalogue": document["feature_catalogue"],
        "data_quality": {
            "schema_version": 1,
            "filter_order": [
                "history_complete",
                "primary_label_available",
                "qualifying_ordinal_mod_row_stride",
            ],
            "counts": document["counts"],
        },
    }
    seen: set[str] = set()
    for entry in cast(list[dict[str, Any]], document["supporting_artefacts"]):
        kind = cast(str, entry["kind"])
        relative = cast(str, entry["path"])
        if (
            kind in seen
            or expected_paths.get(kind) != relative
            or not _safe_relative_path(relative)
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset supporting artefact names are invalid.")
        seen.add(kind)
        content, _identity_value = _read_regular_file(directory / relative, _MAX_JSON_BYTES)
        if (
            hashlib.sha256(content).hexdigest() != entry["sha256"]
            or len(content) != entry["size_bytes"]
            or _strict_json(content, description="Dataset supporting artefact")
            != expected_documents[kind]
        ):
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "Dataset supporting artefact content is inconsistent.",
            )
    if seen != set(expected_paths):
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset supporting artefact set is incomplete.")


def load_partitioned_dataset(
    config: ExperimentConfig,
    *,
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> PartitionedDataset:
    """Authenticate a completed dataset and return metadata without loading test rows."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    _check_cancel(cancellation)
    if not isinstance(config, ExperimentConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Experiment config has the wrong domain type.")
    try:
        parsed_config = parse_config(json.dumps(config_document(config)), "experiment")
    except ConfigValidationError as error:
        raise _fail(
            ErrorCode.CONFIG_SCHEMA, "Experiment config is semantically invalid."
        ) from error
    if parsed_config != config or not _safe_relative_path(config.dataset_manifest):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Experiment config is not canonical version 1.")
    manifest_path = base / config.dataset_manifest
    content, manifest_identity = _read_regular_file(manifest_path, _MAX_JSON_BYTES)
    manifest_sha256 = hashlib.sha256(content).hexdigest()
    document = _strict_json(content, description="Dataset manifest")
    _validate_manifest(document, "dataset")
    dataset_config = _parse_dataset_config(document)
    dataset_hashes = config_hashes(dataset_config)
    expected_identity = _stage_identity(
        b"itchlab-dataset-v1",
        [
            cast(str, item["manifest_sha256"])
            for item in cast(list[dict[str, Any]], document["parents"])
        ],
        dataset_hashes.identity_config_sha256,
        cast(str, cast(dict[str, Any], document["tool"])["sha256"]),
    )
    schema = dataset_schema(dataset_config.features, dataset_config.labels)
    feature_names = tuple(
        cast(str, item["name"])
        for item in cast(list[dict[str, Any]], document["feature_catalogue"]["features"])
    )
    primary_label = f"label_horizon_{dataset_config.labels.primary_event_horizon}"
    if (
        document["status"] != "completed"
        or document["config"] != config_document(dataset_config)
        or document["config_sha256"] != dataset_hashes.config_sha256
        or document["identity_config_sha256"] != dataset_hashes.identity_config_sha256
        or document["identity_sha256"] != expected_identity
        or document["schema"] != _schema_descriptor(schema)
        or document["partition_keys"] != list(_PARTITION_KEYS)
        or document["sort_keys"] != ["message_index"]
        or document["dataset_id"] != manifest_path.parent.name
        or document["partitions"]
        != {
            "train_dates": list(dataset_config.partitions.train_dates),
            "validation_dates": list(dataset_config.partitions.validation_dates),
            "test_dates": list(dataset_config.partitions.test_dates),
        }
        or document["feature_catalogue"] != feature_catalogue_document(dataset_config.features)
        or not feature_names
        or any(schema.get_field_index(name) < 0 for name in feature_names)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest lineage or schema is inconsistent.")
    artefacts: list[DatasetArtefact] = []
    seen_paths: set[str] = set()
    partition_rows = {name: 0 for name in ("train", "validation", "test")}
    class_counts = {
        name: {class_name: 0 for class_name in CLASS_NAMES}
        for name in ("train", "validation", "test")
    }
    for entry in cast(list[dict[str, Any]], document["artefacts"]):
        if entry["path"] in seen_paths:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest repeats a child path.")
        seen_paths.add(cast(str, entry["path"]))
        artefact, counts = _validate_dataset_artefact(
            manifest_path.parent,
            entry,
            schema,
            primary_label,
            feature_names,
            cancellation,
        )
        expected_dates = cast(dict[str, list[str]], document["partitions"])[
            f"{artefact.partition}_dates"
        ]
        if (
            artefact.trading_date not in expected_dates
            or artefact.symbol not in dataset_config.symbols
        ):
            raise _fail(ErrorCode.PARTITION, "Dataset artefact is outside its frozen partition.")
        artefacts.append(artefact)
        partition_rows[artefact.partition] += artefact.row_count
        for name in CLASS_NAMES:
            class_counts[artefact.partition][name] += counts[name]
    manifest_counts = {
        cast(str, item["partition"]): cast(dict[str, int], item["classes"])
        for item in cast(list[dict[str, Any]], document["counts"]["by_partition"])
    }
    if class_counts != manifest_counts:
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest class counts disagree with rows.")
    manifest_rows = {
        cast(str, item["partition"]): cast(int, item["rows"]["retained_rows"])
        for item in cast(list[dict[str, Any]], document["counts"]["by_partition"])
    }
    if (
        partition_rows != manifest_rows
        or sum(partition_rows.values()) != document["counts"]["rows"]["retained_rows"]
        or {
            name: sum(class_counts[partition][name] for partition in partition_rows)
            for name in CLASS_NAMES
        }
        != document["counts"]["classes"]
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest row counts do not reconcile.")
    _validate_dataset_supporting_artefacts(manifest_path.parent, document)
    if any(class_counts["train"][name] == 0 for name in CLASS_NAMES):
        raise _fail(ErrorCode.EMPTY_DATASET, "Training data must contain all three classes.")
    return PartitionedDataset(
        dataset_id=cast(str, document["dataset_id"]),
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        manifest_identity=manifest_identity,
        config_sha256=cast(str, document["config_sha256"]),
        identity_sha256=cast(str, document["identity_sha256"]),
        manifest=document,
        logical_schema=schema,
        feature_names=feature_names,
        primary_label=primary_label,
        artefacts=tuple(
            sorted(artefacts, key=lambda item: (item.partition, item.trading_date, item.symbol))
        ),
    )


def load_completed_dataset(
    dataset_manifest: str,
    *,
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> PartitionedDataset:
    """Authenticate a completed dataset directly for non-training read-only consumers."""
    placeholder = {
        "schema_version": 1,
        "dataset_manifest": dataset_manifest,
        "models": {
            "prior": {"enabled": True},
            "logistic_regression": {
                "c_values": [0.01, 0.1, 1.0, 10.0],
                "penalty": "l2",
                "solver": "lbfgs",
                "max_iter": 2000,
            },
            "hist_gradient_boosting": {
                "learning_rates": [0.05, 0.1],
                "max_leaf_nodes": [15, 31],
                "l2_regularization": [0.0, 1.0],
                "max_iter": 100,
            },
        },
        "preprocessing": {
            "continuous_imputation": "median",
            "standardise_logistic": True,
            "standardise_hist_gradient_boosting": False,
            "unknown_symbol": "all_zero",
        },
        "selection_metric": "multiclass_log_loss",
        "seed": 0,
    }
    try:
        parsed = parse_config(json.dumps(placeholder), "experiment")
    except ConfigValidationError as error:  # pragma: no cover - static internal document
        raise _fail(
            ErrorCode.INTERNAL, "Internal dataset loader configuration is invalid."
        ) from error
    if not isinstance(parsed, ExperimentConfig):  # pragma: no cover - exhaustive builder guard
        raise _fail(ErrorCode.INTERNAL, "Internal dataset loader type is invalid.")
    return load_partitioned_dataset(
        parsed,
        base_directory=base_directory,
        cancel_requested=cancel_requested,
    )


def _recheck_dataset(dataset: PartitionedDataset, *, partial_exists: bool = False) -> None:
    try:
        content, manifest_identity = _read_regular_file(dataset.manifest_path, _MAX_JSON_BYTES)
    except ModelTrainingError as error:
        raise _fail(error.code, error.message, partial_exists=partial_exists) from error
    if (
        manifest_identity != dataset.manifest_identity
        or hashlib.sha256(content).hexdigest() != dataset.manifest_sha256
    ):
        raise _fail(
            ErrorCode.HASH_MISMATCH,
            "The dataset manifest changed during predictive training.",
            partial_exists=partial_exists,
        )
    document = _strict_json(content, description="Dataset manifest")
    expected_artefacts = tuple(
        (
            cast(str, entry["path"]),
            cast(str, entry["sha256"]),
            cast(int, entry["size_bytes"]),
            cast(int, entry["row_count"]),
            cast(str, entry["partition"]),
            cast(str, entry["trading_date"]),
            cast(str, entry["symbol"]),
        )
        for entry in cast(list[dict[str, Any]], document["artefacts"])
    )
    supplied_artefacts = tuple(
        (
            item.relative_path,
            item.sha256,
            item.size_bytes,
            item.row_count,
            item.partition,
            item.trading_date,
            item.symbol,
        )
        for item in dataset.artefacts
    )
    if document != dataset.manifest or sorted(expected_artefacts) != sorted(supplied_artefacts):
        raise _fail(
            ErrorCode.HASH_MISMATCH,
            "Authenticated dataset metadata changed before predictive training.",
            partial_exists=partial_exists,
        )
    for artefact in dataset.artefacts:
        try:
            status_result = artefact.path.stat()
        except OSError as error:
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "A dataset artefact disappeared during predictive training.",
                partial_exists=partial_exists,
            ) from error
        if _identity(status_result) != artefact.identity:
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "A dataset artefact changed during predictive training.",
                partial_exists=partial_exists,
            )


def _load_partition(
    dataset: PartitionedDataset,
    partition: PartitionName,
    cancel_requested: CancelCheck,
) -> PartitionData:
    feature_chunks: list[np.ndarray[Any, np.dtype[np.float64]]] = []
    label_chunks: list[np.ndarray[Any, np.dtype[np.int8]]] = []
    symbol_chunks: list[np.ndarray[Any, np.dtype[np.str_]]] = []
    date_chunks: list[np.ndarray[Any, np.dtype[np.str_]]] = []
    symbol_id_chunks: list[np.ndarray[Any, np.dtype[np.uint16]]] = []
    message_index_chunks: list[np.ndarray[Any, np.dtype[np.uint64]]] = []
    artefacts = sorted(
        (item for item in dataset.artefacts if item.partition == partition),
        key=lambda item: (item.trading_date, item.symbol, item.relative_path),
    )
    for artefact in artefacts:
        _check_cancel(cancel_requested)
        try:
            before = _identity(artefact.path.stat())
            if before != artefact.identity:
                raise _fail(ErrorCode.HASH_MISMATCH, "A dataset artefact changed before reading.")
            parquet_file = pq.ParquetFile(artefact.path)
            columns = [
                *dataset.feature_names,
                dataset.primary_label,
                "symbol_id",
                "message_index",
            ]
            for batch in parquet_file.iter_batches(
                batch_size=_INPUT_BATCH_ROWS, columns=columns, use_threads=False
            ):
                _check_cancel(cancel_requested)
                values = batch.to_pydict()
                feature_chunks.append(
                    np.column_stack(
                        [
                            np.asarray(
                                [
                                    np.nan if value is None else float(value)
                                    for value in values[name]
                                ],
                                dtype=np.float64,
                            )
                            for name in dataset.feature_names
                        ]
                    )
                )
                label_chunks.append(np.asarray(values[dataset.primary_label], dtype=np.int8))
                symbol_chunks.append(np.asarray([artefact.symbol] * batch.num_rows, dtype=np.str_))
                date_chunks.append(
                    np.asarray([artefact.trading_date] * batch.num_rows, dtype=np.str_)
                )
                symbol_id_chunks.append(np.asarray(values["symbol_id"], dtype=np.uint16))
                message_index_chunks.append(np.asarray(values["message_index"], dtype=np.uint64))
            after = _identity(artefact.path.stat())
            if before != after:
                raise _fail(ErrorCode.HASH_MISMATCH, "A dataset artefact changed while reading.")
        except ModelTrainingError:
            raise
        except (OSError, pa.ArrowException, MemoryError, TypeError, ValueError) as error:
            raise _fail(
                ErrorCode.SCHEMA_VERSION, "Dataset model rows could not be loaded."
            ) from error
    if not label_chunks:
        raise _fail(ErrorCode.EMPTY_DATASET, f"The frozen {partition} partition is empty.")
    try:
        result = PartitionData(
            partition=partition,
            features=np.concatenate(feature_chunks, axis=0),
            labels=np.concatenate(label_chunks),
            symbols=np.concatenate(symbol_chunks),
            trading_dates=np.concatenate(date_chunks),
            symbol_ids=np.concatenate(symbol_id_chunks),
            message_indices=np.concatenate(message_index_chunks),
        )
    except MemoryError as error:
        raise _fail(
            ErrorCode.MODEL_TRAINING, "Loading a model partition exhausted memory."
        ) from error
    if (
        result.features.shape != (result.rows, len(dataset.feature_names))
        or any(
            values.size != result.rows
            for values in (
                result.symbols,
                result.trading_dates,
                result.symbol_ids,
                result.message_indices,
            )
        )
        or not np.isin(result.labels, CLASS_VALUES).all()
    ):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Loaded model rows are misaligned.")
    return result


def _prediction_schema() -> pa.Schema:
    fields_value: list[pa.Field[Any]] = [
        pa.field("dataset_id", pa.string(), nullable=False),
        pa.field("experiment_id", pa.string(), nullable=False),
        pa.field("trading_date", pa.date32(), nullable=False),
        pa.field("symbol_id", pa.uint16(), nullable=False),
        pa.field("message_index", pa.uint64(), nullable=False),
        pa.field("probability_down", pa.float64(), nullable=False),
        pa.field("probability_flat", pa.float64(), nullable=False),
        pa.field("probability_up", pa.float64(), nullable=False),
        pa.field("score", pa.float64(), nullable=False),
        pa.field("model_name", pa.string(), nullable=False),
    ]
    return pa.schema(fields_value)


def _model_metrics(
    data: PartitionData,
    probabilities: np.ndarray[Any, np.dtype[np.float64]],
    *,
    bootstrap_seed: int | None,
) -> dict[str, Any]:
    validate_predictions(data.labels, probabilities)
    symbols: list[dict[str, Any]] = []
    for symbol in sorted(set(str(value) for value in data.symbols)):
        mask = data.symbols == symbol
        symbols.append(
            {
                "symbol": symbol,
                "class_distribution": class_distribution(data.labels[mask]),
                "metrics": classification_metrics(data.labels[mask], probabilities[mask]),
                "calibration": calibration_bins(data.labels[mask], probabilities[mask]),
            }
        )
    result = {
        "class_distribution": class_distribution(data.labels),
        "metrics": classification_metrics(data.labels, probabilities),
        "calibration": calibration_bins(data.labels, probabilities),
        "by_symbol": symbols,
    }
    if bootstrap_seed is not None:
        result["confidence_intervals"] = day_block_confidence_intervals(
            data.labels,
            probabilities,
            [str(value) for value in data.trading_dates],
            seed=bootstrap_seed,
        )
    return result


def _metrics_document(
    dataset: PartitionedDataset,
    experiment_id: str,
    data: PartitionData,
    probabilities: Mapping[str, np.ndarray[Any, np.dtype[np.float64]]],
    parameters: Mapping[str, Mapping[str, Any]],
    *,
    seed: int,
    candidates: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    for offset, model_name in enumerate(_MODEL_ORDER):
        entry = {
            "model_name": model_name,
            "status": "completed",
            "parameters": dict(parameters[model_name]),
            **_model_metrics(
                data,
                probabilities[model_name],
                bootstrap_seed=None if data.partition != "test" else seed + offset,
            ),
        }
        if candidates is not None and model_name in candidates:
            entry["candidate_evaluations"] = list(candidates[model_name])
        models.append(entry)
    return {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "experiment_id": experiment_id,
        "partition": data.partition,
        "trading_dates": sorted(set(str(value) for value in data.trading_dates)),
        "selection_metric": "multiclass_log_loss",
        "models": models,
    }


def _prediction_table(
    dataset: PartitionedDataset,
    experiment_id: str,
    partitions: Sequence[tuple[PartitionData, Mapping[str, np.ndarray[Any, np.dtype[np.float64]]]]],
) -> pa.Table:
    output: dict[str, list[Any]] = {name: [] for name in _prediction_schema().names}
    seen_keys: set[tuple[str, int, int, str]] = set()
    for data, predictions in partitions:
        for model_name in _MODEL_ORDER:
            probabilities = predictions[model_name]
            validate_predictions(data.labels, probabilities)
            for row in range(data.rows):
                key = (
                    str(data.trading_dates[row]),
                    int(data.symbol_ids[row]),
                    int(data.message_indices[row]),
                    model_name,
                )
                if key in seen_keys:
                    raise _fail(ErrorCode.PREDICTION_KEY, "Prediction keys are not unique.")
                seen_keys.add(key)
                output["dataset_id"].append(dataset.dataset_id)
                output["experiment_id"].append(experiment_id)
                output["trading_date"].append(date.fromisoformat(key[0]))
                output["symbol_id"].append(key[1])
                output["message_index"].append(key[2])
                output["probability_down"].append(float(probabilities[row, 0]))
                output["probability_flat"].append(float(probabilities[row, 1]))
                output["probability_up"].append(float(probabilities[row, 2]))
                output["score"].append(float(probabilities[row, 2] - probabilities[row, 0]))
                output["model_name"].append(model_name)
    try:
        return pa.Table.from_pydict(output, schema=_prediction_schema())
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.PREDICTION_KEY, "Prediction rows violate schema version 1."
        ) from error


def _timestamp(value_ns: int) -> str:
    seconds, nanoseconds = divmod(value_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{prefix}.{nanoseconds:09d}Z"


def _run_id(identity_sha256: str, now_ns: int) -> str:
    return f"{_timestamp(now_ns).replace('-', '').replace(':', '')}-{identity_sha256[:12]}"


def _parent_descriptor(dataset: PartitionedDataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "manifest_sha256": dataset.manifest_sha256,
        "config_sha256": dataset.config_sha256,
        "identity_sha256": dataset.identity_sha256,
        "schema_sha256": dataset.manifest["schema"]["sha256"],
        "partitions": dataset.manifest["partitions"],
    }


def _remove_lock(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Experiment identity lock could not be removed."
        ) from error


def _identity_marker_matches(path: Path, identity_sha256: str) -> bool:
    try:
        return path.read_text(encoding="ascii") == identity_sha256 + "\n"
    except (OSError, UnicodeError):
        return False


def _safe_experiment_root(base: Path, dataset: PartitionedDataset) -> Path:
    root = base / _EXPERIMENT_RUN_ROOT
    if root.exists() and _path_has_symlink(root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Experiment output root may not contain symlinks.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
        dataset_path = dataset.manifest_path.resolve(strict=True)
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Experiment output root could not be prepared."
        ) from error
    if dataset_path.is_relative_to(resolved_root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Experiment output may not contain its dataset input.")
    return resolved_root


def _write_json_artefact(
    directory: Path,
    kind: str,
    filename: str,
    document: Mapping[str, Any],
    row_count: int,
    cancel_requested: CancelCheck,
) -> _OutputArtefact:
    partial = directory / f"{filename}.partial"
    final = directory / filename
    try:
        encoded = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_JSON_BYTES:
            raise _fail(ErrorCode.DISK_WRITE, "An experiment JSON artefact exceeds its bound.")
        with partial.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(final)
    except ModelTrainingError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE, "An experiment JSON artefact could not be written."
        ) from error
    digest, size, _identity_value = _sha256_file(final, cancel_requested)
    return _OutputArtefact(kind, filename, digest, size, row_count)


def _write_predictions(
    directory: Path,
    table: pa.Table,
    cancel_requested: CancelCheck,
) -> _OutputArtefact:
    partial = directory / "predictions.parquet.partial"
    final = directory / "predictions.parquet"
    try:
        pq.write_table(
            table,
            partial,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            row_group_size=_ROW_GROUP_ROWS,
        )
        partial.rename(final)
    except (OSError, pa.ArrowException) as error:
        raise _fail(ErrorCode.DISK_WRITE, "Predictions Parquet could not be written.") from error
    digest, size, _identity_value = _sha256_file(final, cancel_requested)
    try:
        parquet_file = pq.ParquetFile(final)
        if (
            parquet_file.schema_arrow != _prediction_schema()
            or parquet_file.metadata.num_rows != table.num_rows
            or any(
                parquet_file.metadata.row_group(index).num_rows > _ROW_GROUP_ROWS
                for index in range(parquet_file.metadata.num_row_groups)
            )
        ):
            raise _fail(ErrorCode.INVARIANT, "Written predictions metadata is inconsistent.")
    except ModelTrainingError:
        raise
    except (OSError, pa.ArrowException) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Written predictions are invalid.") from error
    return _OutputArtefact("predictions", final.name, digest, size, table.num_rows)


def _artefact_descriptor(artefact: _OutputArtefact) -> dict[str, Any]:
    return {
        "kind": artefact.kind,
        "path": artefact.path,
        "sha256": artefact.sha256,
        "size_bytes": artefact.size_bytes,
        "row_count": artefact.row_count,
    }


def _validate_prediction_output(
    path: Path,
    document: Mapping[str, Any],
    expected_rows: int,
) -> None:
    seen: set[tuple[date, int, int, str]] = set()
    models_by_row: dict[tuple[date, int, int], set[str]] = {}
    rows = 0
    allowed_dates = {
        date.fromisoformat(value)
        for partition in ("validation_dates", "test_dates")
        for value in cast(dict[str, list[str]], document["parent"]["partitions"])[partition]
    }
    try:
        parquet_file = pq.ParquetFile(path)
        if parquet_file.schema_arrow != _prediction_schema():
            raise _fail(ErrorCode.SCHEMA_VERSION, "Prediction schema is invalid.")
        for batch in parquet_file.iter_batches(batch_size=_INPUT_BATCH_ROWS, use_threads=False):
            values = batch.to_pydict()
            for index in range(batch.num_rows):
                if (
                    values["dataset_id"][index] != document["parent"]["dataset_id"]
                    or values["experiment_id"][index] != document["experiment_id"]
                    or values["model_name"][index] not in _MODEL_ORDER
                ):
                    raise _fail(ErrorCode.PREDICTION_KEY, "Prediction lineage is invalid.")
                probabilities = np.asarray(
                    [
                        [
                            values[name][index]
                            for name in (
                                "probability_down",
                                "probability_flat",
                                "probability_up",
                            )
                        ]
                    ],
                    dtype=np.float64,
                )
                if (
                    not np.isfinite(probabilities).all()
                    or (probabilities < 0).any()
                    or (probabilities > 1).any()
                    or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9, rtol=0)
                    or not np.isclose(
                        values["score"][index],
                        probabilities[0, 2] - probabilities[0, 0],
                        atol=1e-12,
                        rtol=0,
                    )
                ):
                    raise _fail(ErrorCode.MODEL_TRAINING, "Prediction probabilities are invalid.")
                key = (
                    cast(date, values["trading_date"][index]),
                    cast(int, values["symbol_id"][index]),
                    cast(int, values["message_index"][index]),
                    cast(str, values["model_name"][index]),
                )
                if key in seen:
                    raise _fail(ErrorCode.PREDICTION_KEY, "Prediction keys are duplicated.")
                seen.add(key)
                models_by_row.setdefault(key[:3], set()).add(key[3])
                if key[0] not in allowed_dates:
                    raise _fail(
                        ErrorCode.PREDICTION_KEY,
                        "A prediction is outside validation and test dates.",
                    )
                rows += 1
    except ModelTrainingError:
        raise
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Prediction content is invalid.") from error
    if rows != expected_rows:
        raise _fail(ErrorCode.HASH_MISMATCH, "Prediction row count is invalid.")
    if any(models != set(_MODEL_ORDER) for models in models_by_row.values()):
        raise _fail(ErrorCode.PREDICTION_KEY, "A prediction row lacks a required model.")
    metric_rows = sum(
        cast(int, entry["row_count"])
        for entry in cast(list[dict[str, Any]], document["artefacts"])
        if entry["kind"] in {"metrics_validation", "metrics_test"}
    )
    if rows != len(_MODEL_ORDER) * metric_rows:
        raise _fail(ErrorCode.HASH_MISMATCH, "Prediction and metric row counts disagree.")


def _validate_metrics_output(
    metrics: Mapping[str, Any],
    manifest: Mapping[str, Any],
    partition: str,
    expected_rows: int,
) -> None:
    expected_dates = cast(dict[str, list[str]], manifest["parent"]["partitions"])[
        f"{partition}_dates"
    ]
    models = cast(list[dict[str, Any]], metrics.get("models"))
    selection = {
        cast(str, item["model_name"]): item
        for item in cast(list[dict[str, Any]], manifest["selection"]["models"])
    }
    if (
        set(metrics)
        != {
            "schema_version",
            "dataset_id",
            "experiment_id",
            "partition",
            "trading_dates",
            "selection_metric",
            "models",
        }
        or metrics["schema_version"] != 1
        or metrics["dataset_id"] != manifest["parent"]["dataset_id"]
        or metrics["experiment_id"] != manifest["experiment_id"]
        or metrics["partition"] != partition
        or metrics["trading_dates"] != expected_dates
        or metrics["selection_metric"] != "multiclass_log_loss"
        or [item.get("model_name") for item in models] != list(_MODEL_ORDER)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment metrics lineage is inconsistent.")
    for model in models:
        name = cast(str, model["model_name"])
        distribution = cast(dict[str, Any], model.get("class_distribution"))
        metric_values = cast(dict[str, Any], model.get("metrics"))
        calibration = cast(dict[str, Any], model.get("calibration"))
        by_symbol = cast(list[dict[str, Any]], model.get("by_symbol"))
        if (
            model.get("status") != "completed"
            or model.get("parameters") != selection[name]["selected_parameters"]
            or distribution.get("rows") != expected_rows
            or len(distribution.get("classes", [])) != 3
            or sum(cast(int, item["count"]) for item in distribution.get("classes", []))
            != expected_rows
            or set(metric_values)
            != {
                "multiclass_log_loss",
                "balanced_accuracy",
                "macro_f1",
                "confusion_matrix",
            }
            or not all(
                np.isfinite(float(metric_values[key]))
                for key in ("multiclass_log_loss", "balanced_accuracy", "macro_f1")
            )
            or sum(
                sum(cast(list[int], row))
                for row in metric_values["confusion_matrix"]["rows_true_columns_predicted"]
            )
            != expected_rows
            or calibration.get("bin_count") != 10
            or len(calibration.get("classes", [])) != 3
            or any(
                len(class_entry["bins"]) != 10
                or sum(cast(int, item["count"]) for item in class_entry["bins"]) != expected_rows
                for class_entry in calibration.get("classes", [])
            )
            or sum(cast(int, item["class_distribution"]["rows"]) for item in by_symbol)
            != expected_rows
            or len({item.get("symbol") for item in by_symbol}) != len(by_symbol)
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Experiment metric values are inconsistent.")
        if partition == "validation":
            expected_candidates = {
                "prior": 0,
                "logistic_regression": 4,
                "hist_gradient_boosting": 8,
            }
            candidates = cast(list[dict[str, Any]], model.get("candidate_evaluations", []))
            if (
                len(candidates) != expected_candidates[name]
                or any(item.get("status") not in {"completed", "failed"} for item in candidates)
                or (
                    candidates
                    and not any(
                        item.get("status") == "completed"
                        and item.get("parameters") == selection[name]["selected_parameters"]
                        for item in candidates
                    )
                )
            ):
                raise _fail(ErrorCode.HASH_MISMATCH, "Validation candidate evidence is incomplete.")
            if not np.isclose(
                float(metric_values["multiclass_log_loss"]),
                float(selection[name]["validation_log_loss"]),
                atol=0,
                rtol=0,
            ):
                raise _fail(ErrorCode.HASH_MISMATCH, "Validation selection metric disagrees.")
        elif (
            "candidate_evaluations" in model
            or "confidence_intervals" not in model
            or model["confidence_intervals"].get("status") not in {"completed", "omitted"}
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Test metric evidence is inconsistent.")


def _validate_diagnostics_output(
    diagnostics: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    models = cast(list[dict[str, Any]], diagnostics.get("models"))
    if (
        set(diagnostics)
        != {"schema_version", "dataset_id", "experiment_id", "serialization_policy", "models"}
        or diagnostics["schema_version"] != 1
        or diagnostics["dataset_id"] != manifest["parent"]["dataset_id"]
        or diagnostics["experiment_id"] != manifest["experiment_id"]
        or diagnostics["serialization_policy"] != "no_model_serialization_retrain_from_manifest"
        or [item.get("model_name") for item in models] != list(_MODEL_ORDER)
        or any(item.get("preprocessing", {}).get("fit_partition") != "train" for item in models[1:])
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment diagnostics are inconsistent.")


def _result_from_manifest(
    manifest_path: Path, document: Mapping[str, Any], *, reused: bool
) -> ExperimentResult:
    test_entry = next(
        item
        for item in cast(list[dict[str, Any]], document["artefacts"])
        if item["kind"] == "metrics_test"
    )
    content, _identity_value = _read_regular_file(
        manifest_path.parent / cast(str, test_entry["path"]), _MAX_JSON_BYTES
    )
    test_document = _strict_json(content, description="Test metrics")
    test_metrics = tuple(
        (cast(str, item["model_name"]), cast(dict[str, Any], item["metrics"]))
        for item in cast(list[dict[str, Any]], test_document["models"])
    )
    selections = tuple(
        (
            cast(str, item["model_name"]),
            cast(dict[str, Any], item["selected_parameters"]),
        )
        for item in cast(list[dict[str, Any]], document["selection"]["models"])
    )
    predictions = next(
        item
        for item in cast(list[dict[str, Any]], document["artefacts"])
        if item["kind"] == "predictions"
    )
    return ExperimentResult(
        experiment_id=cast(str, document["experiment_id"]),
        status="completed",
        manifest_path=manifest_path,
        dataset_id=cast(str, document["parent"]["dataset_id"]),
        prediction_rows=cast(int, predictions["row_count"]),
        selected_parameters=selections,
        test_metrics=test_metrics,
        warnings=tuple(cast(list[str], document["warnings"])),
        reused=reused,
    )


def _verify_existing(
    directory: Path,
    identity_sha256: str,
    config: ExperimentConfig,
    dataset: PartitionedDataset,
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> ExperimentResult | None:
    if not directory.name.endswith(identity_sha256[:12]):
        return None
    manifest_path = directory / _MANIFEST_NAME
    if not manifest_path.exists():
        return None
    content, _manifest_identity = _read_regular_file(manifest_path, _MAX_JSON_BYTES)
    document = _strict_json(content, description="Experiment manifest")
    _validate_manifest(document, "experiment")
    if document["identity_sha256"] != identity_sha256:
        return None
    hashes = config_hashes(config)
    selection_models = cast(list[dict[str, Any]], document["selection"]["models"])
    logistic_parameters = cast(dict[str, Any], selection_models[1]["selected_parameters"])
    boosting_parameters = cast(dict[str, Any], selection_models[2]["selected_parameters"])
    if (
        document["experiment_id"] != directory.name
        or document["config"] != config_document(config)
        or document["config_sha256"] != hashes.config_sha256
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["tool"]["sha256"] != tool_sha256
        or document["parent"] != _parent_descriptor(dataset)
        or document["prediction_schema"] != _schema_descriptor(_prediction_schema())
        or document["feature_names"] != list(dataset.feature_names)
        or [item["model_name"] for item in selection_models] != list(_MODEL_ORDER)
        or set(logistic_parameters) != {"C", "penalty", "solver", "max_iter"}
        or logistic_parameters["C"] not in config.models.logistic_regression.c_values
        or logistic_parameters["penalty"] != config.models.logistic_regression.penalty
        or logistic_parameters["solver"] != config.models.logistic_regression.solver
        or logistic_parameters["max_iter"] != config.models.logistic_regression.max_iter
        or set(boosting_parameters)
        != {
            "learning_rate",
            "max_leaf_nodes",
            "l2_regularization",
            "max_iter",
            "early_stopping",
        }
        or boosting_parameters["learning_rate"]
        not in config.models.hist_gradient_boosting.learning_rates
        or boosting_parameters["max_leaf_nodes"]
        not in config.models.hist_gradient_boosting.max_leaf_nodes
        or boosting_parameters["l2_regularization"]
        not in config.models.hist_gradient_boosting.l2_regularization
        or boosting_parameters["max_iter"] != config.models.hist_gradient_boosting.max_iter
        or boosting_parameters["early_stopping"] is not False
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Existing experiment lineage is inconsistent.")
    expected_paths = {
        "predictions": "predictions.parquet",
        "metrics_validation": "metrics-validation.json",
        "metrics_test": "metrics-test.json",
        "diagnostics": "model-diagnostics.json",
    }
    seen_kinds: set[str] = set()
    for entry in cast(list[dict[str, Any]], document["artefacts"]):
        kind = cast(str, entry["kind"])
        relative = cast(str, entry["path"])
        if kind in seen_kinds or expected_paths.get(kind) != relative:
            raise _fail(ErrorCode.HASH_MISMATCH, "Experiment artefact names are invalid.")
        seen_kinds.add(kind)
        digest, size, _identity_value = _sha256_file(directory / relative, cancel_requested)
        if digest != entry["sha256"] or size != entry["size_bytes"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Experiment artefact hash or size is invalid.")
        if kind == "predictions":
            _validate_prediction_output(
                directory / relative, document, cast(int, entry["row_count"])
            )
        else:
            data, _identity_value = _read_regular_file(directory / relative, _MAX_JSON_BYTES)
            supporting = _strict_json(data, description="Experiment supporting artefact")
            try:
                if kind == "metrics_validation":
                    _validate_metrics_output(
                        supporting,
                        document,
                        "validation",
                        cast(int, entry["row_count"]),
                    )
                elif kind == "metrics_test":
                    _validate_metrics_output(
                        supporting,
                        document,
                        "test",
                        cast(int, entry["row_count"]),
                    )
                else:
                    _validate_diagnostics_output(supporting, document)
            except ModelTrainingError:
                raise
            except (KeyError, TypeError, ValueError) as error:
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "Experiment supporting artefact structure is invalid.",
                ) from error
    if seen_kinds != set(expected_paths):
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment artefact set is incomplete.")
    return _result_from_manifest(manifest_path, document, reused=True)


def load_completed_experiment(
    run_id: str,
    *,
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> AuthenticatedExperiment:
    """Authenticate a completed predictive experiment for read-only consumers."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    _check_cancel(cancellation)
    if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise _fail(ErrorCode.INPUT_PATH, "The experiment run ID is invalid.")

    directory = base / _EXPERIMENT_RUN_ROOT / run_id
    manifest_path = directory / _MANIFEST_NAME
    manifest_content, manifest_identity = _read_regular_file(manifest_path, _MAX_JSON_BYTES)
    document = _strict_json(manifest_content, description="Experiment manifest")
    _validate_manifest(document, "experiment")
    if document.get("experiment_id") != run_id:
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment manifest identity is inconsistent.")
    try:
        parsed_config = parse_config(
            json.dumps(document["config"], ensure_ascii=False, allow_nan=False),
            "experiment",
        )
    except (ConfigValidationError, KeyError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Experiment manifest config is invalid.") from error
    if not isinstance(parsed_config, ExperimentConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Experiment manifest config type is invalid.")

    dataset = load_partitioned_dataset(
        parsed_config,
        base_directory=base,
        cancel_requested=cancellation,
    )
    hashes = config_hashes(parsed_config)
    expected_identity = _stage_identity(
        b"itchlab-experiment-v1",
        [dataset.manifest_sha256],
        hashes.identity_config_sha256,
        cast(str, cast(dict[str, Any], document["tool"])["sha256"]),
    )
    verified = _verify_existing(
        directory,
        expected_identity,
        parsed_config,
        dataset,
        cast(str, cast(dict[str, Any], document["tool"])["sha256"]),
        cancellation,
    )
    if verified is None:
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment content identity is inconsistent.")

    final_content, final_identity = _read_regular_file(manifest_path, _MAX_JSON_BYTES)
    if final_content != manifest_content or final_identity != manifest_identity:
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment manifest changed during validation.")

    evidence: dict[str, dict[str, Any]] = {}
    for entry in cast(list[dict[str, Any]], document["artefacts"]):
        kind = cast(str, entry["kind"])
        if kind not in {"metrics_validation", "metrics_test", "diagnostics"}:
            continue
        content, _identity_value = _read_regular_file(
            directory / cast(str, entry["path"]), _MAX_JSON_BYTES
        )
        if (
            len(content) != cast(int, entry["size_bytes"])
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Experiment evidence changed after validation.")
        evidence[kind] = _strict_json(content, description="Experiment reporting evidence")
    if set(evidence) != {"metrics_validation", "metrics_test", "diagnostics"}:
        raise _fail(ErrorCode.HASH_MISMATCH, "Experiment reporting evidence is incomplete.")

    return AuthenticatedExperiment(
        experiment_id=run_id,
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(manifest_content).hexdigest(),
        config=parsed_config,
        dataset=dataset,
        manifest=document,
        validation_metrics=evidence["metrics_validation"],
        test_metrics=evidence["metrics_test"],
        diagnostics=evidence["diagnostics"],
    )


def _prepare_run(
    root: Path,
    identity_sha256: str,
    force_new_run: bool,
    config: ExperimentConfig,
    dataset: PartitionedDataset,
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> ExperimentResult | _RunPaths:
    lock_path = root / f".{identity_sha256}.lock"
    staging_created = False
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise _fail(
            ErrorCode.RUN_EXISTS, "An experiment with this identity is already locked."
        ) from error
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Experiment identity lock could not be created."
        ) from error
    try:
        if not force_new_run:
            for directory in sorted(root.iterdir()):
                _check_cancel(cancel_requested)
                if not directory.is_dir() or directory == lock_path:
                    continue
                if directory.name.endswith(".partial"):
                    if _identity_marker_matches(directory / _IDENTITY_MARKER, identity_sha256):
                        raise _fail(ErrorCode.RUN_EXISTS, "A partial experiment already exists.")
                    continue
                existing = _verify_existing(
                    directory,
                    identity_sha256,
                    config,
                    dataset,
                    tool_sha256,
                    cancel_requested,
                )
                if existing is not None:
                    _remove_lock(lock_path)
                    return existing
        experiment_id = _run_id(identity_sha256, time.time_ns())
        final_directory = root / experiment_id
        staging_directory = root / f"{experiment_id}.partial"
        if final_directory.exists() or staging_directory.exists():
            raise _fail(ErrorCode.RUN_EXISTS, "Experiment run ID already exists.")
        staging_directory.mkdir()
        staging_created = True
        (staging_directory / _IDENTITY_MARKER).write_text(identity_sha256 + "\n", "ascii")
        return _RunPaths(root, lock_path, staging_directory, final_directory)
    except ModelTrainingError:
        if lock_path.exists() and not staging_created:
            _remove_lock(lock_path)
        raise
    except OSError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Experiment staging directory could not be prepared."
        ) from error


def _publish(paths: _RunPaths, document: Mapping[str, Any]) -> None:
    partial = paths.staging_directory / f"{_MANIFEST_NAME}.partial"
    final = paths.staging_directory / _MANIFEST_NAME
    try:
        encoded = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_JSON_BYTES:
            raise _fail(ErrorCode.DISK_WRITE, "Experiment manifest exceeds its size bound.")
        with partial.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(final)
        _remove_lock(paths.lock_path)
        paths.staging_directory.rename(paths.final_directory)
        try:
            (paths.final_directory / _IDENTITY_MARKER).unlink()
        except OSError:
            pass
    except ModelTrainingError:
        raise
    except OSError as error:
        raise _fail(
            ErrorCode.DISK_WRITE, "Experiment could not be atomically published."
        ) from error


def _selection_document(
    prior_loss: float,
    logistic: SelectedEstimator,
    boosting: SelectedEstimator,
    prior_parameters: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "metric": "multiclass_log_loss",
        "tie_tolerance": 1e-6,
        "models": [
            {
                "model_name": "prior",
                "status": "completed",
                "selected_parameters": dict(prior_parameters),
                "validation_log_loss": prior_loss,
            },
            {
                "model_name": logistic.model_name,
                "status": "completed",
                "selected_parameters": logistic.parameters,
                "validation_log_loss": logistic.validation_log_loss,
            },
            {
                "model_name": boosting.model_name,
                "status": "completed",
                "selected_parameters": boosting.parameters,
                "validation_log_loss": boosting.validation_log_loss,
            },
        ],
    }


def _diagnostics_document(
    dataset: PartitionedDataset,
    experiment_id: str,
    prior: PriorClassifier,
    logistic: SelectedEstimator,
    boosting: SelectedEstimator,
    logistic_preprocessor: Mapping[str, Any],
    boosting_preprocessor: Mapping[str, Any],
) -> dict[str, Any]:
    logistic_estimator = cast(LogisticRegression, logistic.estimator)
    boosting_estimator = cast(HistGradientBoostingClassifier, boosting.estimator)
    return {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "experiment_id": experiment_id,
        "serialization_policy": "no_model_serialization_retrain_from_manifest",
        "models": [
            {
                "model_name": "prior",
                "classes": list(CLASS_VALUES),
                "probabilities": [float(value) for value in prior.probabilities],
            },
            {
                "model_name": "logistic_regression",
                "classes": [int(value) for value in logistic_estimator.classes_],
                "selected_parameters": logistic.parameters,
                "preprocessing": dict(logistic_preprocessor),
                "coefficients": [
                    [float(value) for value in row] for row in logistic_estimator.coef_
                ],
                "intercepts": [float(value) for value in logistic_estimator.intercept_],
                "candidate_evaluations": list(logistic.candidate_evaluations),
            },
            {
                "model_name": "hist_gradient_boosting",
                "classes": [int(value) for value in boosting_estimator.classes_],
                "selected_parameters": boosting.parameters,
                "preprocessing": dict(boosting_preprocessor),
                "iterations_completed": int(boosting_estimator.n_iter_),
                "candidate_evaluations": list(boosting.candidate_evaluations),
            },
        ],
    }


def train_baselines(
    dataset: PartitionedDataset,
    config: ExperimentConfig,
    *,
    base_directory: Path | None = None,
    force_new_run: bool = False,
    cancel_requested: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> ExperimentResult:
    """Train, select, test once and publish all required version-1 baselines."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    _check_cancel(cancellation)
    if not isinstance(dataset, PartitionedDataset) or not isinstance(config, ExperimentConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Training inputs have the wrong domain type.")
    try:
        configured_manifest = (base / config.dataset_manifest).resolve(strict=True)
        supplied_manifest = dataset.manifest_path.resolve(strict=True)
    except OSError as error:
        raise _fail(
            ErrorCode.INPUT_PATH, "The configured dataset manifest is unavailable."
        ) from error
    if configured_manifest != supplied_manifest:
        raise _fail(ErrorCode.HASH_MISMATCH, "The supplied dataset does not match the config.")
    _recheck_dataset(dataset)
    if not pa.Codec.is_available("zstd"):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Zstandard Parquet compression is unavailable.")
    hashes = config_hashes(config)
    tool_sha256 = _package_content_sha256()
    identity_sha256 = _stage_identity(
        b"itchlab-experiment-v1",
        [dataset.manifest_sha256],
        hashes.identity_config_sha256,
        tool_sha256,
    )
    root = _safe_experiment_root(base, dataset)
    prepared = _prepare_run(
        root,
        identity_sha256,
        force_new_run,
        config,
        dataset,
        tool_sha256,
        cancellation,
    )
    if isinstance(prepared, ExperimentResult):
        return prepared
    paths = prepared
    started_at_ns = time.time_ns()
    try:
        train = _load_partition(dataset, "train", cancellation)
        validation = _load_partition(dataset, "validation", cancellation)
        _check_cancel(cancellation)

        prior = fit_prior(train.labels)
        prior_parameters: dict[str, Any] = {
            "source": "training_class_frequencies",
            "fit_rows": train.rows,
        }
        validation_probabilities: dict[str, np.ndarray[Any, np.dtype[np.float64]]] = {
            "prior": prior.predict_proba(validation.rows)
        }
        if progress is not None:
            progress(ExperimentProgress("selection", 1, 13, 1))

        logistic_preprocessor = fit_preprocessor(
            train, dataset.feature_names, family="logistic_regression"
        )
        logistic_train = transform_partition(logistic_preprocessor, train)
        logistic_validation = transform_partition(logistic_preprocessor, validation)
        logistic = fit_logistic_candidates(
            logistic_train,
            train.labels,
            logistic_validation,
            validation.labels,
            config.models.logistic_regression,
            seed=config.seed,
            cancel_requested=cancellation,
            candidate_completed=(
                None
                if progress is None
                else lambda completed: progress(
                    ExperimentProgress("selection", 1 + completed, 13, 1)
                )
            ),
        )
        validation_probabilities["logistic_regression"] = logistic.validation_probabilities
        if progress is not None:
            progress(ExperimentProgress("selection", 5, 13, 2))

        boosting_preprocessor = fit_preprocessor(
            train, dataset.feature_names, family="hist_gradient_boosting"
        )
        boosting_train = transform_partition(boosting_preprocessor, train)
        boosting_validation = transform_partition(boosting_preprocessor, validation)
        boosting = fit_gradient_boosting_candidates(
            boosting_train,
            train.labels,
            boosting_validation,
            validation.labels,
            config.models.hist_gradient_boosting,
            seed=config.seed,
            cancel_requested=cancellation,
            candidate_completed=(
                None
                if progress is None
                else lambda completed: progress(
                    ExperimentProgress("selection", 5 + completed, 13, 2)
                )
            ),
        )
        validation_probabilities["hist_gradient_boosting"] = boosting.validation_probabilities
        if progress is not None:
            progress(ExperimentProgress("selection", 13, 13, 3))

        parameters: dict[str, Mapping[str, Any]] = {
            "prior": prior_parameters,
            "logistic_regression": logistic.parameters,
            "hist_gradient_boosting": boosting.parameters,
        }
        candidate_evaluations: dict[str, Sequence[Mapping[str, Any]]] = {
            "logistic_regression": logistic.candidate_evaluations,
            "hist_gradient_boosting": boosting.candidate_evaluations,
        }
        experiment_id = paths.final_directory.name
        validation_metrics = _metrics_document(
            dataset,
            experiment_id,
            validation,
            validation_probabilities,
            parameters,
            seed=config.seed,
            candidates=candidate_evaluations,
        )

        _check_cancel(cancellation)
        test = _load_partition(dataset, "test", cancellation)
        logistic_test = transform_partition(logistic_preprocessor, test)
        boosting_test = transform_partition(boosting_preprocessor, test)
        test_probabilities: dict[str, np.ndarray[Any, np.dtype[np.float64]]] = {
            "prior": prior.predict_proba(test.rows),
            "logistic_regression": logistic_probabilities(logistic, logistic_test),
            "hist_gradient_boosting": gradient_boosting_probabilities(boosting, boosting_test),
        }
        for values in test_probabilities.values():
            validate_predictions(test.labels, values)
        test_metrics = _metrics_document(
            dataset,
            experiment_id,
            test,
            test_probabilities,
            parameters,
            seed=config.seed,
            candidates=None,
        )
        if progress is not None:
            progress(ExperimentProgress("test_evaluation", 13, 13, 3))

        selection = _selection_document(
            multiclass_log_loss(validation.labels, validation_probabilities["prior"]),
            logistic,
            boosting,
            prior_parameters,
        )
        diagnostics = _diagnostics_document(
            dataset,
            experiment_id,
            prior,
            logistic,
            boosting,
            logistic_preprocessor.diagnostics,
            boosting_preprocessor.diagnostics,
        )
        failures = [
            item
            for selected in (logistic, boosting)
            for item in selected.candidate_evaluations
            if item["status"] == "failed"
        ]
        warnings_value = (
            ["One or more declared candidates failed; details remain in validation metrics."]
            if failures
            else []
        )
        if force_new_run:
            warnings_value.append("A new immutable run was explicitly forced for this identity.")

        prediction_table = _prediction_table(
            dataset,
            experiment_id,
            ((validation, validation_probabilities), (test, test_probabilities)),
        )
        artefacts = [
            _write_predictions(paths.staging_directory, prediction_table, cancellation),
            _write_json_artefact(
                paths.staging_directory,
                "metrics_validation",
                "metrics-validation.json",
                validation_metrics,
                validation.rows,
                cancellation,
            ),
            _write_json_artefact(
                paths.staging_directory,
                "metrics_test",
                "metrics-test.json",
                test_metrics,
                test.rows,
                cancellation,
            ),
            _write_json_artefact(
                paths.staging_directory,
                "diagnostics",
                "model-diagnostics.json",
                diagnostics,
                0,
                cancellation,
            ),
        ]
        _recheck_dataset(dataset, partial_exists=True)
        completed_at_ns = time.time_ns()
        document = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "status": "completed",
            "started_at": _timestamp(started_at_ns),
            "completed_at": _timestamp(completed_at_ns),
            "config": config_document(config),
            "config_sha256": hashes.config_sha256,
            "identity_config_sha256": hashes.identity_config_sha256,
            "identity_sha256": identity_sha256,
            "tool": {
                "application_version": __version__,
                "content_digest_kind": "python-package-content-v1",
                "sha256": tool_sha256,
                "python_version": platform.python_version(),
                "pyarrow_version": pa.__version__,
                "numpy_version": np.__version__,
                "scikit_learn_version": sklearn.__version__,
            },
            "parent": _parent_descriptor(dataset),
            "classes": [
                {"name": name, "value": value}
                for name, value in zip(CLASS_NAMES, CLASS_VALUES, strict=True)
            ],
            "feature_names": list(dataset.feature_names),
            "selection": selection,
            "test_evaluation_count": 1,
            "prediction_schema": _schema_descriptor(_prediction_schema()),
            "artefacts": [_artefact_descriptor(item) for item in artefacts],
            "warnings": warnings_value,
        }
        _validate_manifest(document, "experiment")
        _validate_prediction_output(
            paths.staging_directory / "predictions.parquet",
            document,
            prediction_table.num_rows,
        )
        _publish(paths, document)
    except ModelTrainingError as error:
        if error.partial_exists:
            raise
        raise _fail(error.code, error.message, partial_exists=True) from error
    except (OSError, pa.ArrowException, MemoryError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Predictive training failed while creating staged output.",
            partial_exists=True,
        ) from error

    return ExperimentResult(
        experiment_id=paths.final_directory.name,
        status="completed",
        manifest_path=paths.final_directory / _MANIFEST_NAME,
        dataset_id=dataset.dataset_id,
        prediction_rows=prediction_table.num_rows,
        selected_parameters=tuple((name, dict(parameters[name])) for name in _MODEL_ORDER),
        test_metrics=tuple(
            (
                cast(str, item["model_name"]),
                cast(dict[str, Any], item["metrics"]),
            )
            for item in cast(list[dict[str, Any]], test_metrics["models"])
        ),
        warnings=tuple(warnings_value),
        reused=False,
    )


__all__ = [
    "load_completed_dataset",
    "load_completed_experiment",
    "load_partitioned_dataset",
    "train_baselines",
]
