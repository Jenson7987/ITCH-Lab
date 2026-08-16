"""Bounded causal as-of selection over one authenticated prediction stream."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Literal, TypeAlias, cast

from itchlab_research.config import MAX_IJSON_INTEGER
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import MAX_TIMESTAMP_NS, MAX_UINT16, MAX_UINT64

PredictionModelName: TypeAlias = Literal["prior", "logistic_regression", "hist_gradient_boosting"]
PREDICTION_MODEL_ORDER: Final[tuple[PredictionModelName, ...]] = (
    "prior",
    "logistic_regression",
    "hist_gradient_boosting",
)


class PredictionDiagnosticCode(StrEnum):
    """Stable non-fatal signal fallbacks emitted at a decision."""

    MISSING = "DIAG_MISSING_PREDICTION"
    STALE = "DIAG_STALE_PREDICTION"


@dataclass(frozen=True, slots=True)
class PredictionKey:
    """The exact persisted prediction-row identity within one experiment."""

    experiment_id: str
    trading_date: date
    symbol_id: int
    message_index: int
    model_name: PredictionModelName


@dataclass(frozen=True, slots=True)
class SignalPrediction:
    """One prediction enriched with its exact frozen dataset-row timestamp."""

    key: PredictionKey
    timestamp_ns: int
    score: float


@dataclass(frozen=True, slots=True)
class PredictionDiagnostic:
    """One payload-free missing or stale prediction observation."""

    code: PredictionDiagnosticCode
    decision_message_index: int
    symbol_id: int
    prediction_message_index: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class PredictionSelection:
    """The exact prediction and effective causal score for one decision."""

    key: PredictionKey | None
    prediction_timestamp_ns: int | None
    age_ns: int | None
    raw_score: float | None
    effective_score: float
    diagnostic: PredictionDiagnostic | None


@dataclass(frozen=True, slots=True)
class PredictionJoinState:
    """Immutable semantic cursor state for diagnostics and atomicity tests."""

    latest: SignalPrediction | None
    last_decision_message_index: int | None
    last_decision_timestamp_ns: int | None
    diagnostics: tuple[PredictionDiagnostic, ...]


def _fail(code: ErrorCode, message: str, *, message_index: int | None = None) -> SimulationError:
    return SimulationError(code, message, message_index=message_index)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _validate_model_name(value: object) -> PredictionModelName:
    if value not in PREDICTION_MODEL_ORDER or not isinstance(value, str):
        raise _fail(ErrorCode.PREDICTION_KEY, "Prediction model name is invalid.")
    return cast(PredictionModelName, value)


class CausalPredictionJoin:
    """Select latest same-scope predictions without consuming future scores."""

    def __init__(
        self,
        predictions: Iterable[SignalPrediction],
        *,
        experiment_id: str,
        trading_date: date,
        symbol_id: int,
        model_name: PredictionModelName,
        max_prediction_age_ns: int,
    ) -> None:
        if not isinstance(experiment_id, str) or not experiment_id:
            raise _fail(ErrorCode.PREDICTION_KEY, "Prediction experiment identity is invalid.")
        if type(trading_date) is not date:
            raise _fail(ErrorCode.TRADING_DATE, "Prediction trading date is invalid.")
        if not _valid_int(symbol_id, minimum=1, maximum=MAX_UINT16):
            raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Prediction symbol ID is invalid.")
        selected_model = _validate_model_name(model_name)
        if not _valid_int(
            max_prediction_age_ns,
            minimum=0,
            maximum=MAX_IJSON_INTEGER,
        ):
            raise _fail(ErrorCode.CONFIG_SCHEMA, "Prediction-age bound is invalid.")
        try:
            source = iter(predictions)
        except TypeError as error:
            raise _fail(ErrorCode.PREDICTION_KEY, "Prediction stream is not iterable.") from error

        self._source: Iterator[SignalPrediction] = source
        self._buffer: deque[SignalPrediction] = deque()
        self._experiment_id = experiment_id
        self._trading_date = trading_date
        self._symbol_id = symbol_id
        self._model_name = selected_model
        self._max_prediction_age_ns = max_prediction_age_ns
        self._latest: SignalPrediction | None = None
        self._last_decision_message_index: int | None = None
        self._last_decision_timestamp_ns: int | None = None
        self._diagnostics: list[PredictionDiagnostic] = []

    @property
    def diagnostics(self) -> tuple[PredictionDiagnostic, ...]:
        """Return non-fatal fallbacks in causal decision order."""
        return tuple(self._diagnostics)

    def snapshot(self) -> PredictionJoinState:
        """Return immutable semantic state without exposing iterator internals."""
        return PredictionJoinState(
            latest=self._latest,
            last_decision_message_index=self._last_decision_message_index,
            last_decision_timestamp_ns=self._last_decision_timestamp_ns,
            diagnostics=tuple(self._diagnostics),
        )

    def _take(self) -> SignalPrediction | None:
        if self._buffer:
            return self._buffer.popleft()
        try:
            return next(self._source)
        except StopIteration:
            return None

    def _restore(self, predictions: list[SignalPrediction]) -> None:
        self._buffer.extendleft(reversed(predictions))

    def _validate_prediction_key(
        self,
        prediction: object,
        previous: SignalPrediction | None,
    ) -> SignalPrediction:
        if not isinstance(prediction, SignalPrediction) or not isinstance(
            prediction.key, PredictionKey
        ):
            raise _fail(ErrorCode.PREDICTION_KEY, "Prediction row has the wrong domain type.")
        key = prediction.key
        if (
            key.experiment_id != self._experiment_id
            or type(key.trading_date) is not date
            or key.trading_date != self._trading_date
            or not _valid_int(key.symbol_id, minimum=1, maximum=MAX_UINT16)
            or key.symbol_id != self._symbol_id
            or not _valid_int(key.message_index, minimum=0, maximum=MAX_UINT64)
            or _validate_model_name(key.model_name) != self._model_name
        ):
            raise _fail(
                ErrorCode.PREDICTION_KEY,
                "Prediction row does not match the configured stream scope.",
                message_index=(key.message_index if isinstance(key.message_index, int) else None),
            )
        if previous is not None and key.message_index <= previous.key.message_index:
            raise _fail(
                ErrorCode.PREDICTION_KEY,
                "Prediction rows are duplicated or out of source order.",
                message_index=key.message_index,
            )
        return prediction

    def _validate_prediction_payload(
        self,
        prediction: SignalPrediction,
        previous: SignalPrediction | None,
    ) -> None:
        key = prediction.key
        if not _valid_int(prediction.timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(
                ErrorCode.PREDICTION_KEY,
                "Prediction dataset timestamp is invalid.",
                message_index=key.message_index,
            )
        if (
            isinstance(prediction.score, bool)
            or not isinstance(prediction.score, (int, float))
            or not math.isfinite(float(prediction.score))
            or not -1.0 <= float(prediction.score) <= 1.0
        ):
            raise _fail(
                ErrorCode.PREDICTION_KEY,
                "Prediction score is not finite and bounded.",
                message_index=key.message_index,
            )
        if previous is not None and prediction.timestamp_ns < previous.timestamp_ns:
            raise _fail(
                ErrorCode.PREDICTION_KEY,
                "Prediction rows are duplicated or out of source order.",
                message_index=key.message_index,
            )

    def select(self, *, decision_message_index: int, timestamp_ns: int) -> PredictionSelection:
        """Select the latest prediction at or before one ordered market decision."""
        if not _valid_int(decision_message_index, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.PREDICTION_KEY, "Decision message index is invalid.")
        if not _valid_int(timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Prediction decision timestamp is outside the exchange day.",
                message_index=decision_message_index,
            )
        if (
            self._last_decision_message_index is not None
            and self._last_decision_timestamp_ns is not None
            and (
                decision_message_index < self._last_decision_message_index
                or timestamp_ns < self._last_decision_timestamp_ns
            )
        ):
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Prediction decisions must remain in source order.",
                message_index=decision_message_index,
            )

        latest = self._latest
        previous = latest
        pulled: list[SignalPrediction] = []
        future: SignalPrediction | None = None
        try:
            while True:
                raw = self._take()
                if raw is None:
                    break
                pulled.append(raw)
                prediction = self._validate_prediction_key(raw, previous)
                if prediction.key.message_index > decision_message_index:
                    future = prediction
                    break
                self._validate_prediction_payload(prediction, previous)
                if prediction.timestamp_ns > timestamp_ns:
                    raise _fail(
                        ErrorCode.LEAKAGE_GUARD,
                        "Prediction timestamp follows the decision timestamp.",
                        message_index=prediction.key.message_index,
                    )
                latest = prediction
                previous = prediction
        except SimulationError:
            self._restore(pulled)
            raise

        if future is not None:
            self._restore([future])
        self._latest = latest
        self._last_decision_message_index = decision_message_index
        self._last_decision_timestamp_ns = timestamp_ns

        if latest is None:
            diagnostic = PredictionDiagnostic(
                code=PredictionDiagnosticCode.MISSING,
                decision_message_index=decision_message_index,
                symbol_id=self._symbol_id,
                prediction_message_index=None,
                reason="no_prediction_at_or_before_decision",
            )
            self._diagnostics.append(diagnostic)
            return PredictionSelection(
                key=None,
                prediction_timestamp_ns=None,
                age_ns=None,
                raw_score=None,
                effective_score=0.0,
                diagnostic=diagnostic,
            )

        age_ns = timestamp_ns - latest.timestamp_ns
        selected_diagnostic: PredictionDiagnostic | None = None
        effective_score = float(latest.score)
        if age_ns > self._max_prediction_age_ns:
            effective_score = 0.0
            selected_diagnostic = PredictionDiagnostic(
                code=PredictionDiagnosticCode.STALE,
                decision_message_index=decision_message_index,
                symbol_id=self._symbol_id,
                prediction_message_index=latest.key.message_index,
                reason="prediction_age_exceeded",
            )
            self._diagnostics.append(selected_diagnostic)
        return PredictionSelection(
            key=latest.key,
            prediction_timestamp_ns=latest.timestamp_ns,
            age_ns=age_ns,
            raw_score=float(latest.score),
            effective_score=effective_score,
            diagnostic=selected_diagnostic,
        )


__all__ = [
    "PREDICTION_MODEL_ORDER",
    "CausalPredictionJoin",
    "PredictionDiagnostic",
    "PredictionDiagnosticCode",
    "PredictionJoinState",
    "PredictionKey",
    "PredictionModelName",
    "PredictionSelection",
    "SignalPrediction",
]
