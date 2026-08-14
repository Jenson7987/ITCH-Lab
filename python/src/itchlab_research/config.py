"""Strict version-1 configuration parsing and immutable domain models."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, Literal, TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from itchlab_research.errors import ConfigIssue, ConfigValidationError, ErrorCode

ConfigKind = Literal["replay", "conversion", "dataset", "experiment", "simulation"]
JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

MAX_IJSON_INTEGER: Final = 9_007_199_254_740_991
_SCHEMA_BY_KIND: Final[dict[ConfigKind, str]] = {
    "replay": "replay-config.schema.json",
    "conversion": "conversion-config.schema.json",
    "dataset": "dataset-config.schema.json",
    "experiment": "experiment-config.schema.json",
    "simulation": "simulation-config.schema.json",
}


@dataclass(frozen=True, slots=True)
class ConversionParquetConfig:
    compression: Literal["zstd"]
    row_group_size: int
    partition_keys: tuple[Literal["trading_date", "symbol"], ...]


@dataclass(frozen=True, slots=True)
class ConversionConfig:
    schema_version: int
    replay_manifests: tuple[str, ...]
    output_root: str
    parquet: ConversionParquetConfig
    allow_degraded: bool


@dataclass(frozen=True, slots=True)
class ReplayInputConfig:
    path: str
    sha256: str | None
    trading_date: str
    exchange_timezone: str


@dataclass(frozen=True, slots=True)
class ReplaySelectionConfig:
    symbols: tuple[str, ...]
    session_start_ns: int
    session_end_ns: int
    require_trading_state: bool


@dataclass(frozen=True, slots=True)
class ReplayOutputConfig:
    depth: int
    emit_unchanged_trade_snapshots: bool


@dataclass(frozen=True, slots=True)
class ReplayValidationConfig:
    mode: Literal["strict", "permissive"]
    max_skipped_messages: int
    invariant_interval: int


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    schema_version: int
    input: ReplayInputConfig
    selection: ReplaySelectionConfig
    output: ReplayOutputConfig
    validation: ReplayValidationConfig


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    depth_levels: tuple[int, ...]
    event_windows: tuple[int, ...]
    clock_windows_ns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LabelConfig:
    primary_event_horizon: int
    secondary_event_horizons: tuple[int, ...]
    flat_threshold_ticks: int


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    row_stride: int


@dataclass(frozen=True, slots=True)
class PartitionConfig:
    train_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]
    test_dates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    schema_version: int
    conversion_manifests: tuple[str, ...]
    symbols: tuple[str, ...]
    tick_size4_by_symbol: tuple[tuple[str, int], ...]
    features: FeatureConfig
    labels: LabelConfig
    sampling: SamplingConfig
    partitions: PartitionConfig


@dataclass(frozen=True, slots=True)
class PriorConfig:
    enabled: bool


@dataclass(frozen=True, slots=True)
class LogisticRegressionConfig:
    c_values: tuple[float, ...]
    penalty: Literal["l2"]
    solver: Literal["lbfgs"]
    max_iter: int


@dataclass(frozen=True, slots=True)
class HistGradientBoostingConfig:
    learning_rates: tuple[float, ...]
    max_leaf_nodes: tuple[int, ...]
    l2_regularization: tuple[float, ...]
    max_iter: int


@dataclass(frozen=True, slots=True)
class ModelConfig:
    prior: PriorConfig
    logistic_regression: LogisticRegressionConfig
    hist_gradient_boosting: HistGradientBoostingConfig


@dataclass(frozen=True, slots=True)
class PreprocessingConfig:
    continuous_imputation: Literal["median"]
    standardise_logistic: bool
    standardise_hist_gradient_boosting: bool
    unknown_symbol: Literal["all_zero"]


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    schema_version: int
    dataset_manifest: str
    models: ModelConfig
    preprocessing: PreprocessingConfig
    selection_metric: Literal["multiclass_log_loss"]
    seed: int


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    name: Literal["inventory_aware_avellaneda_stoikov", "signal_adjusted_avellaneda_stoikov"]
    decision_interval_ns: int
    max_prediction_age_ns: int
    order_quantity: int
    inventory_limit: int
    gamma: float
    volatility_window_ns: int
    risk_horizon_seconds: float
    signal_weight_ticks: float
    max_signal_ticks: float


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    passive_only: bool
    submission_latency_ns: int
    cancellation_latency_ns: int
    maker_fee_microusd_per_share: int
    taker_fee_microusd_per_share: int
    queue_policy: Literal["known_orders_conservative"]
    max_queue_anomalies: int
    terminal_liquidation: Literal["cross_visible_spread"]


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    schema_version: int
    dataset_manifest: str
    prediction_manifest: str | None
    strategy: StrategyConfig
    execution: ExecutionConfig
    seed: int


Config: TypeAlias = (
    ReplayConfig | ConversionConfig | DatasetConfig | ExperimentConfig | SimulationConfig
)


class _DuplicateNameError(ValueError):
    pass


def _reject_duplicate_names(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateNameError
        result[key] = value
    return result


def _reject_non_json_constant(_: str) -> None:
    raise ValueError


def _validate_ijson(value: JSONValue, pointer: str = "") -> tuple[ConfigIssue, ...]:
    issues: list[ConfigIssue] = []
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            issues.append(
                ConfigIssue(pointer, ErrorCode.CONFIG_SCHEMA, "String is not valid Unicode.")
            )
    elif isinstance(value, bool) or value is None:
        pass
    elif isinstance(value, int):
        if not -MAX_IJSON_INTEGER <= value <= MAX_IJSON_INTEGER:
            issues.append(
                ConfigIssue(
                    pointer,
                    (
                        ErrorCode.SEED
                        if pointer.endswith("/seed")
                        else (
                            ErrorCode.QUEUE_STATE
                            if pointer.endswith("/max_queue_anomalies")
                            else ErrorCode.CONFIG_SCHEMA
                        )
                    ),
                    "Integer exceeds the RFC 8785/I-JSON exact range.",
                )
            )
    elif isinstance(value, float):
        if not math.isfinite(value):
            issues.append(ConfigIssue(pointer, ErrorCode.CONFIG_SCHEMA, "Number must be finite."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_validate_ijson(item, f"{pointer}/{index}"))
    else:
        for key, item in value.items():
            child_pointer = f"{pointer}/{_escape_pointer(key)}"
            issues.extend(_validate_ijson(key, child_pointer))
            issues.extend(_validate_ijson(item, child_pointer))
    return tuple(issues)


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_pointer(path: Sequence[str | int]) -> str:
    return "".join(f"/{_escape_pointer(str(component))}" for component in path)


@lru_cache(maxsize=5)
def _schema(kind: ConfigKind) -> Mapping[str, Any]:
    resource = files("itchlab_research._schemas").joinpath(_SCHEMA_BY_KIND[kind])
    return cast(Mapping[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _schema_error_code(error: ValidationError) -> ErrorCode:
    pointer = _json_pointer(tuple(error.absolute_path))
    if pointer.endswith("/exchange_timezone"):
        return ErrorCode.TIMEZONE
    if pointer.endswith("/trading_date"):
        return ErrorCode.TRADING_DATE
    if "session_" in pointer:
        return ErrorCode.SESSION_WINDOW
    if pointer.endswith("/depth"):
        return ErrorCode.DEPTH
    if "horizon" in pointer:
        return ErrorCode.HORIZON
    if pointer.startswith("/partitions"):
        return ErrorCode.PARTITION
    if pointer.endswith("/row_stride"):
        return ErrorCode.ROW_STRIDE
    if pointer.endswith("/seed"):
        return ErrorCode.SEED
    if pointer.endswith("_latency_ns"):
        return ErrorCode.LATENCY
    if "fee_microusd_per_share" in pointer:
        return ErrorCode.COST
    if pointer.endswith("/inventory_limit"):
        return ErrorCode.INVENTORY_LIMIT
    if pointer.endswith("/max_queue_anomalies"):
        return ErrorCode.QUEUE_STATE
    if pointer == "/schema_version":
        return ErrorCode.SCHEMA_VERSION
    return ErrorCode.CONFIG_SCHEMA


def _schema_error_message(error: ValidationError) -> str:
    messages: dict[str, str] = {
        "additionalProperties": "Unknown configuration property.",
        "required": "Required configuration property is missing.",
        "type": "Configuration value has the wrong JSON type.",
        "const": "Configuration value does not match the version-1 contract.",
        "enum": "Configuration value is not supported by version 1.",
        "format": "Configuration value has an invalid format.",
        "minimum": "Configuration value is below its allowed minimum.",
        "maximum": "Configuration value exceeds its allowed maximum.",
        "exclusiveMinimum": "Configuration value must be positive.",
        "minItems": "Configuration array is too short.",
        "uniqueItems": "Configuration array contains duplicates.",
        "pattern": "Configuration string has an invalid format.",
    }
    return messages.get(str(error.validator), "Configuration value violates the version-1 schema.")


def _schema_issues(document: JSONValue, kind: ConfigKind) -> tuple[ConfigIssue, ...]:
    validator = Draft202012Validator(_schema(kind), format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda item: _json_pointer(tuple(item.absolute_path)),
    )
    return tuple(
        ConfigIssue(
            _json_pointer(tuple(error.absolute_path)),
            _schema_error_code(error),
            _schema_error_message(error),
        )
        for error in errors
    )


def _semantic_issues(document: dict[str, JSONValue], kind: ConfigKind) -> tuple[ConfigIssue, ...]:
    issues: list[ConfigIssue] = []
    if kind == "replay":
        selection = cast(dict[str, JSONValue], document["selection"])
        start = cast(int, selection["session_start_ns"])
        end = cast(int, selection["session_end_ns"])
        if start >= end:
            issues.append(
                ConfigIssue(
                    "/selection", ErrorCode.SESSION_WINDOW, "Session start must precede end."
                )
            )
    elif kind == "conversion":
        locators = [
            *cast(list[str], document["replay_manifests"]),
            cast(str, document["output_root"]),
        ]
        for index, locator in enumerate(locators):
            normalised = locator.replace("\\", "/")
            path = Path(normalised)
            segments = normalised.split("/")
            if (
                normalised.startswith("/")
                or (len(normalised) >= 2 and normalised[1] == ":")
                or path.is_absolute()
                or path.drive
                or any(part in {"", ".", ".."} or part.endswith(".partial") for part in segments)
            ):
                pointer = (
                    f"/replay_manifests/{index}" if index < len(locators) - 1 else "/output_root"
                )
                issues.append(
                    ConfigIssue(
                        pointer,
                        ErrorCode.OUTPUT_PATH
                        if pointer == "/output_root"
                        else ErrorCode.INPUT_PATH,
                        "Conversion locators must be safe relative paths without "
                        "partial components.",
                    )
                )
    elif kind == "dataset":
        for index, locator in enumerate(cast(list[str], document["conversion_manifests"])):
            normalised = locator.replace("\\", "/")
            path = Path(normalised)
            segments = normalised.split("/")
            if (
                normalised.startswith("/")
                or (len(normalised) >= 2 and normalised[1] == ":")
                or path.is_absolute()
                or path.drive
                or any(part in {"", ".", ".."} or part.endswith(".partial") for part in segments)
            ):
                issues.append(
                    ConfigIssue(
                        f"/conversion_manifests/{index}",
                        ErrorCode.INPUT_PATH,
                        "Dataset manifest locators must be safe relative paths without "
                        "partial components.",
                    )
                )
        symbols = cast(list[str], document["symbols"])
        tick_sizes = cast(dict[str, JSONValue], document["tick_size4_by_symbol"])
        if set(symbols) != set(tick_sizes):
            issues.append(
                ConfigIssue(
                    "/tick_size4_by_symbol",
                    ErrorCode.CONFIG_SCHEMA,
                    "Tick-size keys must exactly match configured symbols.",
                )
            )
        partitions = cast(dict[str, list[str]], document["partitions"])
        train = partitions["train_dates"]
        validation = partitions["validation_dates"]
        test = partitions["test_dates"]
        if train != sorted(train) or validation != sorted(validation) or test != sorted(test):
            issues.append(
                ConfigIssue("/partitions", ErrorCode.PARTITION, "Partition dates must be sorted.")
            )
        train_set, validation_set, test_set = set(train), set(validation), set(test)
        if train_set & validation_set or train_set & test_set or validation_set & test_set:
            issues.append(
                ConfigIssue("/partitions", ErrorCode.PARTITION, "Partition dates must not overlap.")
            )
        parsed_train = tuple(date.fromisoformat(value) for value in train)
        parsed_validation = tuple(date.fromisoformat(value) for value in validation)
        parsed_test = tuple(date.fromisoformat(value) for value in test)
        if not (max(parsed_train) < min(parsed_validation) < min(parsed_test)) or not (
            max(parsed_validation) < min(parsed_test)
        ):
            issues.append(
                ConfigIssue(
                    "/partitions",
                    ErrorCode.PARTITION,
                    "Train, validation and test dates must be chronological.",
                )
            )
    elif kind == "experiment":
        locator = cast(str, document["dataset_manifest"])
        normalised = locator.replace("\\", "/")
        path = Path(normalised)
        segments = normalised.split("/")
        if (
            normalised.startswith("/")
            or (len(normalised) >= 2 and normalised[1] == ":")
            or path.is_absolute()
            or path.drive
            or any(part in {"", ".", ".."} or part.endswith(".partial") for part in segments)
        ):
            issues.append(
                ConfigIssue(
                    "/dataset_manifest",
                    ErrorCode.INPUT_PATH,
                    "Experiment dataset locator must be a safe relative path without "
                    "partial components.",
                )
            )
    elif kind == "simulation":
        strategy = cast(dict[str, JSONValue], document["strategy"])
        if cast(int, strategy["inventory_limit"]) < cast(int, strategy["order_quantity"]):
            issues.append(
                ConfigIssue(
                    "/strategy/inventory_limit",
                    ErrorCode.INVENTORY_LIMIT,
                    "Inventory limit must cover at least one configured order.",
                )
            )
    return tuple(sorted(issues))


def _parse_document(text: str) -> dict[str, JSONValue]:
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_names,
            parse_constant=_reject_non_json_constant,
        )
    except (_DuplicateNameError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ConfigValidationError(
            (ConfigIssue("", ErrorCode.CONFIG_SCHEMA, "Configuration is not valid JSON/I-JSON."),)
        ) from error
    if not isinstance(document, dict):
        raise ConfigValidationError(
            (ConfigIssue("", ErrorCode.CONFIG_SCHEMA, "Configuration root must be an object."),)
        )
    return cast(dict[str, JSONValue], document)


def _tuple_of_ints(value: JSONValue) -> tuple[int, ...]:
    return tuple(cast(list[int], value))


def _tuple_of_floats(value: JSONValue) -> tuple[float, ...]:
    return tuple(float(item) for item in cast(list[int | float], value))


def _build_replay(document: dict[str, JSONValue]) -> ReplayConfig:
    input_config = cast(dict[str, JSONValue], document["input"])
    selection = cast(dict[str, JSONValue], document["selection"])
    output = cast(dict[str, JSONValue], document["output"])
    validation = cast(dict[str, JSONValue], document["validation"])
    return ReplayConfig(
        schema_version=cast(int, document["schema_version"]),
        input=ReplayInputConfig(
            path=cast(str, input_config["path"]),
            sha256=cast(str | None, input_config["sha256"]),
            trading_date=cast(str, input_config["trading_date"]),
            exchange_timezone=cast(str, input_config["exchange_timezone"]),
        ),
        selection=ReplaySelectionConfig(
            symbols=tuple(cast(list[str], selection["symbols"])),
            session_start_ns=cast(int, selection["session_start_ns"]),
            session_end_ns=cast(int, selection["session_end_ns"]),
            require_trading_state=cast(bool, selection["require_trading_state"]),
        ),
        output=ReplayOutputConfig(
            depth=cast(int, output["depth"]),
            emit_unchanged_trade_snapshots=cast(bool, output["emit_unchanged_trade_snapshots"]),
        ),
        validation=ReplayValidationConfig(
            mode=cast(Literal["strict", "permissive"], validation["mode"]),
            max_skipped_messages=cast(int, validation["max_skipped_messages"]),
            invariant_interval=cast(int, validation["invariant_interval"]),
        ),
    )


def _build_conversion(document: dict[str, JSONValue]) -> ConversionConfig:
    parquet = cast(dict[str, JSONValue], document["parquet"])
    return ConversionConfig(
        schema_version=cast(int, document["schema_version"]),
        replay_manifests=tuple(cast(list[str], document["replay_manifests"])),
        output_root=cast(str, document["output_root"]),
        parquet=ConversionParquetConfig(
            compression="zstd",
            row_group_size=cast(int, parquet["row_group_size"]),
            partition_keys=cast(
                tuple[Literal["trading_date", "symbol"], ...],
                tuple(cast(list[str], parquet["partition_keys"])),
            ),
        ),
        allow_degraded=cast(bool, document.get("allow_degraded", False)),
    )


def _build_dataset(document: dict[str, JSONValue]) -> DatasetConfig:
    features = cast(dict[str, JSONValue], document["features"])
    labels = cast(dict[str, JSONValue], document["labels"])
    sampling = cast(dict[str, JSONValue], document["sampling"])
    partitions = cast(dict[str, JSONValue], document["partitions"])
    tick_sizes = cast(dict[str, int], document["tick_size4_by_symbol"])
    return DatasetConfig(
        schema_version=cast(int, document["schema_version"]),
        conversion_manifests=tuple(cast(list[str], document["conversion_manifests"])),
        symbols=tuple(cast(list[str], document["symbols"])),
        tick_size4_by_symbol=tuple(sorted(tick_sizes.items())),
        features=FeatureConfig(
            depth_levels=_tuple_of_ints(features["depth_levels"]),
            event_windows=_tuple_of_ints(features["event_windows"]),
            clock_windows_ns=_tuple_of_ints(features["clock_windows_ns"]),
        ),
        labels=LabelConfig(
            primary_event_horizon=cast(int, labels["primary_event_horizon"]),
            secondary_event_horizons=_tuple_of_ints(labels["secondary_event_horizons"]),
            flat_threshold_ticks=cast(int, labels["flat_threshold_ticks"]),
        ),
        sampling=SamplingConfig(row_stride=cast(int, sampling["row_stride"])),
        partitions=PartitionConfig(
            train_dates=tuple(cast(list[str], partitions["train_dates"])),
            validation_dates=tuple(cast(list[str], partitions["validation_dates"])),
            test_dates=tuple(cast(list[str], partitions["test_dates"])),
        ),
    )


def _build_experiment(document: dict[str, JSONValue]) -> ExperimentConfig:
    models = cast(dict[str, dict[str, JSONValue]], document["models"])
    logistic = models["logistic_regression"]
    boosting = models["hist_gradient_boosting"]
    preprocessing = cast(dict[str, JSONValue], document["preprocessing"])
    return ExperimentConfig(
        schema_version=cast(int, document["schema_version"]),
        dataset_manifest=cast(str, document["dataset_manifest"]),
        models=ModelConfig(
            prior=PriorConfig(enabled=cast(bool, models["prior"]["enabled"])),
            logistic_regression=LogisticRegressionConfig(
                c_values=_tuple_of_floats(logistic["c_values"]),
                penalty="l2",
                solver="lbfgs",
                max_iter=cast(int, logistic["max_iter"]),
            ),
            hist_gradient_boosting=HistGradientBoostingConfig(
                learning_rates=_tuple_of_floats(boosting["learning_rates"]),
                max_leaf_nodes=_tuple_of_ints(boosting["max_leaf_nodes"]),
                l2_regularization=_tuple_of_floats(boosting["l2_regularization"]),
                max_iter=cast(int, boosting["max_iter"]),
            ),
        ),
        preprocessing=PreprocessingConfig(
            continuous_imputation="median",
            standardise_logistic=cast(bool, preprocessing["standardise_logistic"]),
            standardise_hist_gradient_boosting=cast(
                bool, preprocessing["standardise_hist_gradient_boosting"]
            ),
            unknown_symbol="all_zero",
        ),
        selection_metric="multiclass_log_loss",
        seed=cast(int, document["seed"]),
    )


def _build_simulation(document: dict[str, JSONValue]) -> SimulationConfig:
    strategy = cast(dict[str, JSONValue], document["strategy"])
    execution = cast(dict[str, JSONValue], document["execution"])
    return SimulationConfig(
        schema_version=cast(int, document["schema_version"]),
        dataset_manifest=cast(str, document["dataset_manifest"]),
        prediction_manifest=cast(str | None, document["prediction_manifest"]),
        strategy=StrategyConfig(
            name=cast(
                Literal[
                    "inventory_aware_avellaneda_stoikov",
                    "signal_adjusted_avellaneda_stoikov",
                ],
                strategy["name"],
            ),
            decision_interval_ns=cast(int, strategy["decision_interval_ns"]),
            max_prediction_age_ns=cast(int, strategy["max_prediction_age_ns"]),
            order_quantity=cast(int, strategy["order_quantity"]),
            inventory_limit=cast(int, strategy["inventory_limit"]),
            gamma=float(cast(int | float, strategy["gamma"])),
            volatility_window_ns=cast(int, strategy["volatility_window_ns"]),
            risk_horizon_seconds=float(cast(int | float, strategy["risk_horizon_seconds"])),
            signal_weight_ticks=float(cast(int | float, strategy["signal_weight_ticks"])),
            max_signal_ticks=float(cast(int | float, strategy["max_signal_ticks"])),
        ),
        execution=ExecutionConfig(
            passive_only=cast(bool, execution["passive_only"]),
            submission_latency_ns=cast(int, execution["submission_latency_ns"]),
            cancellation_latency_ns=cast(int, execution["cancellation_latency_ns"]),
            maker_fee_microusd_per_share=cast(int, execution["maker_fee_microusd_per_share"]),
            taker_fee_microusd_per_share=cast(int, execution["taker_fee_microusd_per_share"]),
            queue_policy="known_orders_conservative",
            max_queue_anomalies=cast(int, execution["max_queue_anomalies"]),
            terminal_liquidation="cross_visible_spread",
        ),
        seed=cast(int, document["seed"]),
    )


_BUILDERS: Final[dict[ConfigKind, Callable[[dict[str, JSONValue]], Config]]] = {
    "replay": _build_replay,
    "conversion": _build_conversion,
    "dataset": _build_dataset,
    "experiment": _build_experiment,
    "simulation": _build_simulation,
}


def parse_config(text: str, kind: ConfigKind) -> Config:
    """Validate untrusted JSON text and return its immutable version-1 model."""
    document = _parse_document(text)
    issues = (*_validate_ijson(document), *_schema_issues(document, kind))
    if not issues:
        issues = _semantic_issues(document, kind)
    if issues:
        raise ConfigValidationError(tuple(issues))
    return _BUILDERS[kind](document)


def load_config(path: Path, kind: ConfigKind) -> Config:
    """Load and validate one UTF-8 config file without resolving referenced paths."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigValidationError(
            (ConfigIssue("", ErrorCode.INPUT_PATH, "Configuration path is not readable UTF-8."),)
        ) from error
    return parse_config(text, kind)


__all__ = [
    "Config",
    "ConfigKind",
    "ConversionConfig",
    "ConversionParquetConfig",
    "DatasetConfig",
    "ExperimentConfig",
    "FeatureConfig",
    "MAX_IJSON_INTEGER",
    "ReplayConfig",
    "SimulationConfig",
    "load_config",
    "parse_config",
]
