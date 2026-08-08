"""Stable public error identifiers and bounded configuration issues."""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable public error strings shared with the C++ core."""

    INPUT_PATH = "ERR_INPUT_PATH"
    UNSUPPORTED_COMPRESSION = "ERR_UNSUPPORTED_COMPRESSION"
    FRAMING = "ERR_FRAMING"
    TRUNCATED_MESSAGE = "ERR_TRUNCATED_MESSAGE"
    EMPTY_INPUT = "ERR_EMPTY_INPUT"
    MESSAGE_LENGTH = "ERR_MESSAGE_LENGTH"
    UNKNOWN_MESSAGE = "ERR_UNKNOWN_MESSAGE"
    TIMESTAMP = "ERR_TIMESTAMP"
    UNKNOWN_SYMBOL = "ERR_UNKNOWN_SYMBOL"
    TRADING_DATE = "ERR_TRADING_DATE"
    ORDER_REFERENCE = "ERR_ORDER_REFERENCE"
    QUANTITY = "ERR_QUANTITY"
    PRICE = "ERR_PRICE"
    BOOK_CROSSED = "ERR_BOOK_CROSSED"
    INVARIANT = "ERR_INVARIANT"
    OUTPUT_PATH = "ERR_OUTPUT_PATH"
    DISK_WRITE = "ERR_DISK_WRITE"
    HASH_MISMATCH = "ERR_HASH_MISMATCH"
    SCHEMA_VERSION = "ERR_SCHEMA_VERSION"
    PARTIAL_ARTEFACT = "ERR_PARTIAL_ARTEFACT"
    CONFIG_SCHEMA = "ERR_CONFIG_SCHEMA"
    SESSION_WINDOW = "ERR_SESSION_WINDOW"
    TIMEZONE = "ERR_TIMEZONE"
    DEPTH = "ERR_DEPTH"
    HORIZON = "ERR_HORIZON"
    PARTITION = "ERR_PARTITION"
    ROW_STRIDE = "ERR_ROW_STRIDE"
    SEED = "ERR_SEED"
    EMPTY_DATASET = "ERR_EMPTY_DATASET"
    LEAKAGE_GUARD = "ERR_LEAKAGE_GUARD"
    MODEL_TRAINING = "ERR_MODEL_TRAINING"
    PREDICTION_KEY = "ERR_PREDICTION_KEY"
    LATENCY = "ERR_LATENCY"
    COST = "ERR_COST"
    QUEUE_STATE = "ERR_QUEUE_STATE"
    INVENTORY_LIMIT = "ERR_INVENTORY_LIMIT"
    SIMULATION_ANOMALY = "ERR_SIMULATION_ANOMALY"
    BROKEN_SIM_FILL = "ERR_BROKEN_SIM_FILL"
    RUN_EXISTS = "ERR_RUN_EXISTS"
    CANCELLED = "ERR_CANCELLED"
    INTERNAL = "ERR_INTERNAL"


@dataclass(frozen=True, slots=True, order=True)
class ConfigIssue:
    """One safe configuration failure located by JSON pointer."""

    json_pointer: str
    code: ErrorCode
    message: str


class ConfigValidationError(ValueError):
    """Raised when a config cannot become an immutable domain model."""

    def __init__(self, issues: tuple[ConfigIssue, ...]) -> None:
        if not issues:
            raise ValueError("ConfigValidationError requires at least one issue")
        self.issues = tuple(sorted(issues))
        super().__init__(f"{len(self.issues)} configuration error(s)")


class InterchangeReadError(ValueError):
    """One stable, payload-free failure while reading an interchange artefact."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        record_index: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.record_index = record_index
        super().__init__(f"{code.value}: {message}")


class ConversionError(RuntimeError):
    """One stable, path-safe failure from conversion or publication."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        partial_exists: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.partial_exists = partial_exists
        super().__init__(f"{code.value}: {message}")


class FeatureComputationError(ValueError):
    """One stable, row-safe failure from causal feature calculation."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        message_index: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.message_index = message_index
        super().__init__(f"{code.value}: {message}")


class LabelComputationError(ValueError):
    """One stable, row-safe failure from future-label calculation."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        message_index: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.message_index = message_index
        super().__init__(f"{code.value}: {message}")


class DatasetBuildError(RuntimeError):
    """One stable, path-safe failure from dataset construction or publication."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        partial_exists: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.partial_exists = partial_exists
        super().__init__(f"{code.value}: {message}")


class ModelTrainingError(RuntimeError):
    """One stable, path-safe failure from predictive model training/publication."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        partial_exists: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.partial_exists = partial_exists
        super().__init__(f"{code.value}: {message}")


__all__ = [
    "ConfigIssue",
    "ConfigValidationError",
    "ConversionError",
    "DatasetBuildError",
    "ErrorCode",
    "FeatureComputationError",
    "InterchangeReadError",
    "LabelComputationError",
    "ModelTrainingError",
]
