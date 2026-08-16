"""Bounded signal adjustment and validation-only strategy selection."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction
from typing import Final, Literal, cast

from itchlab_research.config import StrategyConfig
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.accounting import MAX_INT64, MIN_INT64
from itchlab_research.strategies.avellaneda_stoikov import (
    BaselineDecision,
    CausalVolatilityEstimator,
    InventoryAwareAvellanedaStoikov,
    QuoteProposal,
    QuoteSuppressionReason,
    VolatilityEstimate,
)
from itchlab_research.strategies.calibration import IntensityCalibration
from itchlab_research.strategies.prediction_join import (
    PREDICTION_MODEL_ORDER,
    CausalPredictionJoin,
    PredictionDiagnostic,
    PredictionModelName,
    PredictionSelection,
    SignalPrediction,
)

SIGNAL_WEIGHT_CANDIDATES: Final[tuple[float, ...]] = (0.0, 0.5, 1.0, 2.0)
MODEL_LOG_LOSS_TIE_TOLERANCE: Final = 1e-6
SIGNAL_PNL_TIE_TOLERANCE_MICROUSD: Final = 1
SIGNAL_SELECTION_LATENCY_NS: Final = 100_000
SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE: Final = -2_000


@dataclass(frozen=True, slots=True)
class ModelValidationMetric:
    """One validation-only model-family selection value."""

    partition: Literal["train", "validation", "test"]
    model_name: PredictionModelName
    multiclass_log_loss: float


@dataclass(frozen=True, slots=True)
class SignalModelSelection:
    """The frozen model family selected without consulting test metrics."""

    model_name: PredictionModelName
    validation_log_loss: float
    evaluations: tuple[ModelValidationMetric, ...]


@dataclass(frozen=True, slots=True)
class ValidationSignalPnl:
    """One complete validation-day result under the fixed selection scenario."""

    partition: Literal["train", "validation", "test"]
    trading_date: date
    signal_weight_ticks: float
    submission_latency_ns: int
    cancellation_latency_ns: int
    maker_fee_microusd_per_share: int
    net_pnl_microusd: int


@dataclass(frozen=True, slots=True)
class SignalWeightEvaluation:
    """Exact aggregate used to compare one configured signal-weight candidate."""

    signal_weight_ticks: float
    validation_days: int
    total_net_pnl_microusd: int
    mean_net_pnl_microusd: float


@dataclass(frozen=True, slots=True)
class SignalWeightSelection:
    """The frozen signal weight chosen before any test scenario is observed."""

    signal_weight_ticks: float
    evaluations: tuple[SignalWeightEvaluation, ...]


@dataclass(frozen=True, slots=True)
class SignalAdjustedDecision:
    """Auditable baseline inputs plus the selected bounded signal quote output."""

    baseline: BaselineDecision
    prediction: PredictionSelection | None
    signal_weight_ticks: float
    unclipped_adjustment_ticks: float
    adjustment_ticks: float
    signal_reservation_price_ticks: float | None
    bid: QuoteProposal | None
    ask: QuoteProposal | None
    bid_suppression_reason: QuoteSuppressionReason | None
    ask_suppression_reason: QuoteSuppressionReason | None

    @property
    def diagnostics(self) -> tuple[PredictionDiagnostic, ...]:
        """Return the decision-local non-fatal prediction diagnostic, if any."""
        if self.prediction is None or self.prediction.diagnostic is None:
            return ()
        return (self.prediction.diagnostic,)


def _fail(code: ErrorCode, message: str) -> SimulationError:
    return SimulationError(code, message)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _signal_weight(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Signal weight is invalid.")
    converted = float(value)
    if converted not in SIGNAL_WEIGHT_CANDIDATES:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Signal weight is outside the version-1 catalogue.")
    return converted


def select_signal_model(evaluations: Iterable[ModelValidationMetric]) -> SignalModelSelection:
    """Choose minimum validation log loss with the documented simplicity tie-break."""
    try:
        values = tuple(evaluations)
    except TypeError as error:
        raise _fail(
            ErrorCode.MODEL_TRAINING, "Model selection evidence is not iterable."
        ) from error
    by_name: dict[PredictionModelName, ModelValidationMetric] = {}
    for evaluation in values:
        if not isinstance(evaluation, ModelValidationMetric):
            raise _fail(ErrorCode.MODEL_TRAINING, "Model selection evidence is invalid.")
        if evaluation.partition != "validation":
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Signal model selection may consume only validation metrics.",
            )
        if evaluation.model_name not in PREDICTION_MODEL_ORDER:
            raise _fail(ErrorCode.MODEL_TRAINING, "Signal model name is invalid.")
        if evaluation.model_name in by_name:
            raise _fail(ErrorCode.MODEL_TRAINING, "Signal model evidence is duplicated.")
        if (
            isinstance(evaluation.multiclass_log_loss, bool)
            or not isinstance(evaluation.multiclass_log_loss, (int, float))
            or not math.isfinite(float(evaluation.multiclass_log_loss))
            or float(evaluation.multiclass_log_loss) < 0.0
        ):
            raise _fail(ErrorCode.MODEL_TRAINING, "Validation log loss is invalid.")
        by_name[evaluation.model_name] = evaluation
    if set(by_name) != set(PREDICTION_MODEL_ORDER):
        raise _fail(ErrorCode.MODEL_TRAINING, "Signal model evidence is incomplete.")

    ordered = tuple(by_name[name] for name in PREDICTION_MODEL_ORDER)
    best_loss = min(float(candidate.multiclass_log_loss) for candidate in ordered)
    selected = next(
        candidate
        for candidate in ordered
        if float(candidate.multiclass_log_loss) - best_loss <= MODEL_LOG_LOSS_TIE_TOLERANCE
    )
    return SignalModelSelection(
        model_name=selected.model_name,
        validation_log_loss=float(selected.multiclass_log_loss),
        evaluations=ordered,
    )


def select_signal_weight(evaluations: Iterable[ValidationSignalPnl]) -> SignalWeightSelection:
    """Choose exact mean validation-day P&L under the fixed selection scenario."""
    try:
        values = tuple(evaluations)
    except TypeError as error:
        raise _fail(ErrorCode.MODEL_TRAINING, "Signal-weight evidence is not iterable.") from error
    if not values:
        raise _fail(ErrorCode.EMPTY_DATASET, "Signal-weight selection has no validation days.")

    by_weight: dict[float, dict[date, int]] = {weight: {} for weight in SIGNAL_WEIGHT_CANDIDATES}
    for evaluation in values:
        if not isinstance(evaluation, ValidationSignalPnl):
            raise _fail(ErrorCode.MODEL_TRAINING, "Signal-weight evidence is invalid.")
        if evaluation.partition != "validation":
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Signal-weight selection may consume only validation results.",
            )
        if type(evaluation.trading_date) is not date:
            raise _fail(ErrorCode.PARTITION, "Signal-weight validation date is invalid.")
        weight = _signal_weight(evaluation.signal_weight_ticks)
        if (
            evaluation.submission_latency_ns != SIGNAL_SELECTION_LATENCY_NS
            or evaluation.cancellation_latency_ns != SIGNAL_SELECTION_LATENCY_NS
            or evaluation.maker_fee_microusd_per_share
            != SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE
        ):
            raise _fail(
                ErrorCode.CONFIG_SCHEMA,
                "Signal-weight evidence does not use the fixed validation scenario.",
            )
        if not _valid_int(
            evaluation.net_pnl_microusd,
            minimum=MIN_INT64,
            maximum=MAX_INT64,
        ):
            raise _fail(ErrorCode.COST, "Signal-weight daily net P&L is invalid.")
        if evaluation.trading_date in by_weight[weight]:
            raise _fail(ErrorCode.MODEL_TRAINING, "Signal-weight daily evidence is duplicated.")
        by_weight[weight][evaluation.trading_date] = evaluation.net_pnl_microusd

    expected_dates = set(by_weight[SIGNAL_WEIGHT_CANDIDATES[0]])
    if not expected_dates or any(set(results) != expected_dates for results in by_weight.values()):
        raise _fail(
            ErrorCode.PARTITION,
            "Every signal-weight candidate must cover the same validation days.",
        )

    exact_means: dict[float, Fraction] = {}
    summaries: list[SignalWeightEvaluation] = []
    for weight in SIGNAL_WEIGHT_CANDIDATES:
        daily = by_weight[weight]
        total = sum(daily.values())
        mean = Fraction(total, len(daily))
        exact_means[weight] = mean
        summaries.append(
            SignalWeightEvaluation(
                signal_weight_ticks=weight,
                validation_days=len(daily),
                total_net_pnl_microusd=total,
                mean_net_pnl_microusd=float(mean),
            )
        )

    best_mean = max(exact_means.values())
    tolerance = Fraction(SIGNAL_PNL_TIE_TOLERANCE_MICROUSD, 1)
    selected_weight = next(
        candidate
        for candidate in SIGNAL_WEIGHT_CANDIDATES
        if best_mean - exact_means[candidate] <= tolerance
    )
    return SignalWeightSelection(
        signal_weight_ticks=selected_weight,
        evaluations=tuple(summaries),
    )


class SignalAdjustedAvellanedaStoikov:
    """Compose the inventory-aware baseline with one bounded causal signal stream."""

    def __init__(
        self,
        config: StrategyConfig,
        *,
        symbol: str,
        symbol_id: int,
        tick_size4: int,
        trading_date: date,
        session_start_ns: int,
        session_end_ns: int,
        calibration: IntensityCalibration,
        experiment_id: str,
        model_name: PredictionModelName,
        predictions: Iterable[SignalPrediction],
    ) -> None:
        if not isinstance(config, StrategyConfig):
            raise _fail(
                ErrorCode.CONFIG_SCHEMA, "Signal strategy config has the wrong domain type."
            )
        if config.name != "signal_adjusted_avellaneda_stoikov":
            raise _fail(ErrorCode.CONFIG_SCHEMA, "Signal strategy name is invalid.")
        weight = _signal_weight(config.signal_weight_ticks)
        if (
            isinstance(config.max_signal_ticks, bool)
            or not isinstance(config.max_signal_ticks, (int, float))
            or not math.isfinite(float(config.max_signal_ticks))
            or float(config.max_signal_ticks) < 0.0
        ):
            raise _fail(ErrorCode.CONFIG_SCHEMA, "Maximum signal adjustment is invalid.")

        baseline_config = replace(
            config,
            name="inventory_aware_avellaneda_stoikov",
            signal_weight_ticks=0.0,
        )
        self._baseline = InventoryAwareAvellanedaStoikov(
            baseline_config,
            symbol=symbol,
            symbol_id=symbol_id,
            tick_size4=tick_size4,
            session_start_ns=session_start_ns,
            session_end_ns=session_end_ns,
            calibration=calibration,
        )
        self._config = config
        self._weight = weight
        self._predictions = CausalPredictionJoin(
            predictions,
            experiment_id=experiment_id,
            trading_date=trading_date,
            symbol_id=symbol_id,
            model_name=model_name,
            max_prediction_age_ns=config.max_prediction_age_ns,
        )

    @property
    def volatility(self) -> CausalVolatilityEstimator:
        """Return the baseline-owned causal volatility estimator."""
        return self._baseline.volatility

    @property
    def prediction_diagnostics(self) -> tuple[PredictionDiagnostic, ...]:
        """Return every non-fatal prediction fallback in decision order."""
        return self._predictions.diagnostics

    def observe_quote(
        self,
        *,
        message_index: int,
        timestamp_ns: int,
        best_bid_price4: int,
        best_ask_price4: int,
    ) -> VolatilityEstimate | None:
        """Observe one already-applied quote through the shared baseline state."""
        return self._baseline.observe_quote(
            message_index=message_index,
            timestamp_ns=timestamp_ns,
            best_bid_price4=best_bid_price4,
            best_ask_price4=best_ask_price4,
        )

    def decide(
        self,
        *,
        decision_message_index: int,
        timestamp_ns: int,
        inventory_shares: int,
    ) -> SignalAdjustedDecision:
        """Apply the causal bounded score before shared quote constraints."""
        baseline = self._baseline.decide(
            decision_message_index=decision_message_index,
            timestamp_ns=timestamp_ns,
            inventory_shares=inventory_shares,
        )
        selection = None
        effective_score = 0.0
        if self._weight != 0.0:
            selection = self._predictions.select(
                decision_message_index=decision_message_index,
                timestamp_ns=timestamp_ns,
            )
            effective_score = selection.effective_score

        unclipped = self._weight * effective_score
        maximum = float(self._config.max_signal_ticks)
        adjustment = max(-maximum, min(maximum, unclipped))
        if not math.isfinite(unclipped) or not math.isfinite(adjustment):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Signal adjustment is not finite.")

        signal_reservation = baseline.reservation_price_ticks
        bid = baseline.bid
        ask = baseline.ask
        bid_reason = baseline.bid_suppression_reason
        ask_reason = baseline.ask_suppression_reason
        if baseline.reservation_price_ticks is not None:
            half_spread = cast(float, baseline.half_spread_ticks)
            signal_reservation = baseline.reservation_price_ticks + adjustment
            bid, ask, bid_reason, ask_reason = self._baseline._quotes_for_reservation(
                reservation_price_ticks=signal_reservation,
                half_spread_ticks=half_spread,
                best_bid_price4=baseline.best_bid_price4,
                best_ask_price4=baseline.best_ask_price4,
                inventory_shares=baseline.inventory_shares,
            )
        return SignalAdjustedDecision(
            baseline=baseline,
            prediction=selection,
            signal_weight_ticks=self._weight,
            unclipped_adjustment_ticks=unclipped,
            adjustment_ticks=adjustment,
            signal_reservation_price_ticks=signal_reservation,
            bid=bid,
            ask=ask,
            bid_suppression_reason=bid_reason,
            ask_suppression_reason=ask_reason,
        )


__all__ = [
    "MODEL_LOG_LOSS_TIE_TOLERANCE",
    "SIGNAL_PNL_TIE_TOLERANCE_MICROUSD",
    "SIGNAL_SELECTION_LATENCY_NS",
    "SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE",
    "SIGNAL_WEIGHT_CANDIDATES",
    "ModelValidationMetric",
    "SignalAdjustedAvellanedaStoikov",
    "SignalAdjustedDecision",
    "SignalModelSelection",
    "SignalWeightEvaluation",
    "SignalWeightSelection",
    "ValidationSignalPnl",
    "select_signal_model",
    "select_signal_weight",
]
