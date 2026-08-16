"""Causal inventory-aware Avellaneda--Stoikov-inspired baseline quoting.

The approximation follows Avellaneda and Stoikov, *High-frequency trading in a limit order book*,
Quantitative Finance 8(3), 2008, DOI 10.1080/14697680701381228. Project-specific calibration,
clock-window and conservative rounding rules are specified in ``docs/01-product-requirements.md``.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from itchlab_research.config import MAX_IJSON_INTEGER, StrategyConfig
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import MAX_UINT16, MAX_UINT32, MAX_UINT64
from itchlab_research.simulation.risk_limits import InventoryRiskLimit
from itchlab_research.strategies.calibration import (
    CalibrationSource,
    IntensityCalibration,
)

_DAY_NS: Final = 86_400_000_000_000
_NANOSECONDS_PER_SECOND: Final = 1_000_000_000


class QuoteSuppressionReason(StrEnum):
    """Stable reason why one desired passive side was not proposed."""

    INSUFFICIENT_VOLATILITY = "insufficient_volatility"
    PRICE_OUT_OF_RANGE = "price_out_of_range"
    PROJECTED_INVENTORY_LIMIT = "projected_inventory_limit"


@dataclass(frozen=True, slots=True)
class VolatilityEstimate:
    """One causal trailing midpoint variance-rate estimate in tick units."""

    decision_message_index: int
    timestamp_ns: int
    window_start_ns: int
    elapsed_ns: int
    change_count: int
    squared_change_sum_ticks2: float
    sigma_squared: float


@dataclass(frozen=True, slots=True)
class VolatilityState:
    """Immutable diagnostic snapshot of a causal volatility estimator."""

    first_timestamp_ns: int | None
    last_message_index: int | None
    last_timestamp_ns: int | None
    best_bid_price4: int | None
    best_ask_price4: int | None
    mid2: int | None
    changes: tuple[tuple[int, int, float], ...]


@dataclass(frozen=True, slots=True)
class QuoteProposal:
    """One exact tick-grid passive quote after the projected inventory gate."""

    side: int
    price4: int
    quantity: int
    projected_inventory: int


@dataclass(frozen=True, slots=True)
class BaselineDecision:
    """Auditable equation inputs, outputs and side-specific quote decisions."""

    symbol: str
    symbol_id: int
    decision_message_index: int
    timestamp_ns: int
    best_bid_price4: int
    best_ask_price4: int
    mid2: int
    mid_price_ticks: float
    inventory_shares: int
    inventory_units: float
    kappa: float
    kappa_source: CalibrationSource
    volatility: VolatilityEstimate | None
    tau_seconds: float
    reservation_price_ticks: float | None
    half_spread_ticks: float | None
    bid: QuoteProposal | None
    ask: QuoteProposal | None
    bid_suppression_reason: QuoteSuppressionReason | None
    ask_suppression_reason: QuoteSuppressionReason | None


def _fail(code: ErrorCode, message: str) -> SimulationError:
    return SimulationError(code, message)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _valid_number(value: object, *, positive: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    converted = float(value)
    return math.isfinite(converted) and (converted > 0.0 if positive else converted >= 0.0)


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _validate_symbol(symbol: object) -> str:
    if not isinstance(symbol, str):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Strategy symbol is invalid.")
    try:
        encoded = symbol.encode("ascii", errors="strict")
    except UnicodeEncodeError as error:
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Strategy symbol is invalid ASCII.") from error
    if not 1 <= len(encoded) <= 8 or symbol.strip() != symbol:
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Strategy symbol is invalid.")
    return symbol


def _validate_quote_values(best_bid_price4: object, best_ask_price4: object) -> tuple[int, int]:
    if not _valid_int(best_bid_price4, minimum=0, maximum=MAX_UINT32) or not _valid_int(
        best_ask_price4, minimum=0, maximum=MAX_UINT32
    ):
        raise _fail(ErrorCode.PRICE, "Visible quote Price4 values are invalid.")
    bid = cast(int, best_bid_price4)
    ask = cast(int, best_ask_price4)
    if bid > ask:
        raise _fail(ErrorCode.BOOK_CROSSED, "Visible quote is crossed.")
    if bid + ask <= 0:
        raise _fail(ErrorCode.PRICE, "Visible quote midpoint must be positive.")
    return bid, ask


class CausalVolatilityEstimator:
    """Track midpoint changes ending inside one bounded trailing clock window."""

    def __init__(self, *, window_ns: int, tick_size4: int) -> None:
        if not _valid_int(window_ns, minimum=1, maximum=MAX_IJSON_INTEGER):
            raise _fail(ErrorCode.CONFIG_SCHEMA, "Volatility window is invalid.")
        if not _valid_int(tick_size4, minimum=1, maximum=MAX_UINT32):
            raise _fail(ErrorCode.PRICE, "Strategy tick size is invalid.")
        self._window_ns = window_ns
        self._tick_size4 = tick_size4
        self._first_timestamp_ns: int | None = None
        self._last_message_index: int | None = None
        self._last_timestamp_ns: int | None = None
        self._best_bid_price4: int | None = None
        self._best_ask_price4: int | None = None
        self._mid2: int | None = None
        self._changes: deque[tuple[int, int, float]] = deque()

    @property
    def window_ns(self) -> int:
        """Return the configured trailing clock window."""
        return self._window_ns

    @property
    def tick_size4(self) -> int:
        """Return the exact Price4 tick size."""
        return self._tick_size4

    def snapshot(self) -> VolatilityState:
        """Return immutable current state for diagnostics and atomicity checks."""
        return VolatilityState(
            first_timestamp_ns=self._first_timestamp_ns,
            last_message_index=self._last_message_index,
            last_timestamp_ns=self._last_timestamp_ns,
            best_bid_price4=self._best_bid_price4,
            best_ask_price4=self._best_ask_price4,
            mid2=self._mid2,
            changes=tuple(self._changes),
        )

    def observe_quote(
        self,
        *,
        message_index: int,
        timestamp_ns: int,
        best_bid_price4: int,
        best_ask_price4: int,
    ) -> VolatilityEstimate | None:
        """Atomically add one already-applied visible quote in source order."""
        if not _valid_int(message_index, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Volatility message index is invalid.")
        if not _valid_int(timestamp_ns, minimum=0, maximum=_DAY_NS - 1):
            raise _fail(ErrorCode.TIMESTAMP, "Volatility timestamp is outside the exchange day.")
        bid, ask = _validate_quote_values(best_bid_price4, best_ask_price4)
        last_message_index = self._last_message_index
        last_timestamp_ns = self._last_timestamp_ns
        if last_message_index is not None and last_timestamp_ns is None:
            raise _fail(ErrorCode.INTERNAL, "Volatility estimator lost its last timestamp.")
        if (
            last_message_index is not None
            and last_timestamp_ns is not None
            and (message_index <= last_message_index or timestamp_ns < last_timestamp_ns)
        ):
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Volatility quote observations must remain in source order.",
            )

        mid2 = bid + ask
        changes = deque(
            change for change in self._changes if change[0] > timestamp_ns - self._window_ns
        )
        if self._mid2 is not None:
            midpoint_change_ticks = (mid2 - self._mid2) / (2.0 * self._tick_size4)
            squared_change = midpoint_change_ticks * midpoint_change_ticks
            if not math.isfinite(squared_change):
                raise _fail(ErrorCode.SIMULATION_ANOMALY, "Midpoint variance calculation failed.")
            changes.append((timestamp_ns, message_index, squared_change))

        first_timestamp = (
            timestamp_ns if self._first_timestamp_ns is None else self._first_timestamp_ns
        )
        estimate = self._calculate_estimate(
            first_timestamp_ns=first_timestamp,
            changes=changes,
            decision_message_index=message_index,
            timestamp_ns=timestamp_ns,
        )
        self._first_timestamp_ns = first_timestamp
        self._last_message_index = message_index
        self._last_timestamp_ns = timestamp_ns
        self._best_bid_price4 = bid
        self._best_ask_price4 = ask
        self._mid2 = mid2
        self._changes = changes
        return estimate

    def _calculate_estimate(
        self,
        *,
        first_timestamp_ns: int,
        changes: deque[tuple[int, int, float]],
        decision_message_index: int,
        timestamp_ns: int,
    ) -> VolatilityEstimate | None:
        elapsed_ns = min(self._window_ns, timestamp_ns - first_timestamp_ns)
        if elapsed_ns <= 0:
            return None
        window_start = timestamp_ns - self._window_ns
        eligible = tuple(
            squared
            for change_timestamp, change_index, squared in changes
            if change_timestamp > window_start
            and (
                change_timestamp < timestamp_ns
                or (change_timestamp == timestamp_ns and change_index <= decision_message_index)
            )
        )
        try:
            squared_sum = math.fsum(eligible)
        except (OverflowError, ValueError) as error:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY, "Midpoint variance estimate failed."
            ) from error
        sigma_squared = squared_sum / (elapsed_ns / _NANOSECONDS_PER_SECOND)
        if not math.isfinite(sigma_squared) or sigma_squared < 0.0:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Midpoint variance estimate is invalid.")
        return VolatilityEstimate(
            decision_message_index=decision_message_index,
            timestamp_ns=timestamp_ns,
            window_start_ns=max(first_timestamp_ns, window_start),
            elapsed_ns=elapsed_ns,
            change_count=len(eligible),
            squared_change_sum_ticks2=squared_sum,
            sigma_squared=sigma_squared,
        )

    def estimate(
        self, *, decision_message_index: int, timestamp_ns: int
    ) -> VolatilityEstimate | None:
        """Return the estimate available at one causal decision key without mutation."""
        if self._last_message_index is None or self._last_timestamp_ns is None:
            raise _fail(ErrorCode.EMPTY_DATASET, "Volatility has no visible quote observation.")
        if not _valid_int(decision_message_index, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Decision message index is invalid.")
        if not _valid_int(timestamp_ns, minimum=0, maximum=_DAY_NS - 1):
            raise _fail(ErrorCode.TIMESTAMP, "Decision timestamp is outside the exchange day.")
        if timestamp_ns < self._last_timestamp_ns or (
            timestamp_ns == self._last_timestamp_ns
            and decision_message_index < self._last_message_index
        ):
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Volatility decision cannot precede the latest quote observation.",
            )
        first_timestamp = self._first_timestamp_ns
        if first_timestamp is None:
            raise _fail(ErrorCode.INTERNAL, "Volatility estimator lost its first observation.")
        return self._calculate_estimate(
            first_timestamp_ns=first_timestamp,
            changes=self._changes,
            decision_message_index=decision_message_index,
            timestamp_ns=timestamp_ns,
        )


def _validate_strategy_config(config: StrategyConfig) -> None:
    if not isinstance(config, StrategyConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Baseline strategy config has the wrong domain type.")
    if (
        config.name != "inventory_aware_avellaneda_stoikov"
        or not _finite_number(config.signal_weight_ticks)
        or float(config.signal_weight_ticks) != 0.0
    ):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Baseline strategy requires zero signal weight.")
    integer_fields = (
        config.decision_interval_ns,
        config.max_prediction_age_ns,
        config.order_quantity,
        config.inventory_limit,
        config.volatility_window_ns,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Baseline integer parameters are invalid.")
    if not 1 <= config.decision_interval_ns <= MAX_IJSON_INTEGER:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Decision interval is invalid.")
    if not 0 <= config.max_prediction_age_ns <= MAX_IJSON_INTEGER:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Prediction-age bound is invalid.")
    if not 1 <= config.order_quantity <= MAX_IJSON_INTEGER:
        raise _fail(ErrorCode.QUANTITY, "Baseline order quantity is invalid.")
    if not 1 <= config.inventory_limit <= MAX_IJSON_INTEGER or (
        config.inventory_limit < config.order_quantity
    ):
        raise _fail(ErrorCode.INVENTORY_LIMIT, "Baseline inventory limit is invalid.")
    if not 1 <= config.volatility_window_ns <= MAX_IJSON_INTEGER:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Volatility window is invalid.")
    if not _valid_number(config.gamma, positive=True):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Baseline gamma must be finite and positive.")
    if not _valid_number(config.risk_horizon_seconds, positive=True) or (
        float(config.risk_horizon_seconds) > 86_400.0
    ):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Baseline risk horizon is invalid.")
    if not _valid_number(config.max_signal_ticks, positive=False):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Maximum signal adjustment is invalid.")


class InventoryAwareAvellanedaStoikov:
    """Own one session-symbol causal volatility state and emit baseline quote decisions."""

    def __init__(
        self,
        config: StrategyConfig,
        *,
        symbol: str,
        symbol_id: int,
        tick_size4: int,
        session_start_ns: int,
        session_end_ns: int,
        calibration: IntensityCalibration,
    ) -> None:
        _validate_strategy_config(config)
        validated_symbol = _validate_symbol(symbol)
        if not _valid_int(symbol_id, minimum=1, maximum=MAX_UINT16):
            raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Strategy symbol ID is invalid.")
        if not _valid_int(tick_size4, minimum=1, maximum=MAX_UINT32):
            raise _fail(ErrorCode.PRICE, "Strategy tick size is invalid.")
        if (
            not _valid_int(session_start_ns, minimum=0, maximum=_DAY_NS - 1)
            or not _valid_int(session_end_ns, minimum=1, maximum=_DAY_NS)
            or session_start_ns >= session_end_ns
        ):
            raise _fail(ErrorCode.SESSION_WINDOW, "Strategy session window is invalid.")
        if not isinstance(calibration, IntensityCalibration):
            raise _fail(ErrorCode.MODEL_TRAINING, "Strategy intensity calibration is invalid.")
        selected = calibration.for_symbol(validated_symbol)
        if (
            selected.symbol != validated_symbol
            or not _valid_number(selected.kappa, positive=True)
            or not _finite_number(selected.intercept)
            or not isinstance(selected.source, CalibrationSource)
        ):
            raise _fail(ErrorCode.MODEL_TRAINING, "Strategy intensity estimate is invalid.")

        self._config = config
        self._symbol = validated_symbol
        self._symbol_id = symbol_id
        self._tick_size4 = tick_size4
        self._session_start_ns = session_start_ns
        self._session_end_ns = session_end_ns
        self._kappa = selected.kappa
        self._kappa_source = selected.source
        self._risk = InventoryRiskLimit(config.inventory_limit)
        self._volatility = CausalVolatilityEstimator(
            window_ns=config.volatility_window_ns,
            tick_size4=tick_size4,
        )

    @property
    def volatility(self) -> CausalVolatilityEstimator:
        """Return the owned causal volatility estimator for diagnostics."""
        return self._volatility

    def observe_quote(
        self,
        *,
        message_index: int,
        timestamp_ns: int,
        best_bid_price4: int,
        best_ask_price4: int,
    ) -> VolatilityEstimate | None:
        """Observe an in-session already-applied quote before a possible decision."""
        if not _valid_int(
            timestamp_ns,
            minimum=self._session_start_ns,
            maximum=self._session_end_ns - 1,
        ):
            raise _fail(ErrorCode.SESSION_WINDOW, "Strategy quote lies outside its session.")
        return self._volatility.observe_quote(
            message_index=message_index,
            timestamp_ns=timestamp_ns,
            best_bid_price4=best_bid_price4,
            best_ask_price4=best_ask_price4,
        )

    def _proposal(
        self, *, side: int, tick_index: int, inventory_shares: int
    ) -> tuple[QuoteProposal | None, QuoteSuppressionReason | None]:
        if tick_index < 0 or tick_index > MAX_UINT32 // self._tick_size4:
            return None, QuoteSuppressionReason.PRICE_OUT_OF_RANGE
        risk = self._risk.evaluate(
            current_inventory=inventory_shares,
            side=side,
            quantity=self._config.order_quantity,
        )
        if not risk.permitted:
            return None, QuoteSuppressionReason.PROJECTED_INVENTORY_LIMIT
        return (
            QuoteProposal(
                side=side,
                price4=tick_index * self._tick_size4,
                quantity=self._config.order_quantity,
                projected_inventory=risk.projected_inventory,
            ),
            None,
        )

    def decide(
        self,
        *,
        decision_message_index: int,
        timestamp_ns: int,
        inventory_shares: int,
    ) -> BaselineDecision:
        """Return the causal inventory-aware passive quote decision at one market key."""
        if not _valid_int(
            timestamp_ns,
            minimum=self._session_start_ns,
            maximum=self._session_end_ns - 1,
        ):
            raise _fail(ErrorCode.SESSION_WINDOW, "Strategy decision lies outside its session.")
        if not _valid_int(
            inventory_shares,
            minimum=-self._config.inventory_limit,
            maximum=self._config.inventory_limit,
        ):
            raise _fail(ErrorCode.INVENTORY_LIMIT, "Strategy inventory is outside its limit.")
        state = self._volatility.snapshot()
        if state.best_bid_price4 is None or state.best_ask_price4 is None or state.mid2 is None:
            raise _fail(ErrorCode.EMPTY_DATASET, "Strategy has no current visible quote.")
        volatility = self._volatility.estimate(
            decision_message_index=decision_message_index,
            timestamp_ns=timestamp_ns,
        )
        tau_seconds = min(
            float(self._config.risk_horizon_seconds),
            (self._session_end_ns - timestamp_ns) / _NANOSECONDS_PER_SECOND,
        )
        mid_price_ticks = state.mid2 / (2.0 * self._tick_size4)
        inventory_units = inventory_shares / self._config.order_quantity
        if volatility is None:
            return BaselineDecision(
                symbol=self._symbol,
                symbol_id=self._symbol_id,
                decision_message_index=decision_message_index,
                timestamp_ns=timestamp_ns,
                best_bid_price4=state.best_bid_price4,
                best_ask_price4=state.best_ask_price4,
                mid2=state.mid2,
                mid_price_ticks=mid_price_ticks,
                inventory_shares=inventory_shares,
                inventory_units=inventory_units,
                kappa=self._kappa,
                kappa_source=self._kappa_source,
                volatility=None,
                tau_seconds=tau_seconds,
                reservation_price_ticks=None,
                half_spread_ticks=None,
                bid=None,
                ask=None,
                bid_suppression_reason=QuoteSuppressionReason.INSUFFICIENT_VOLATILITY,
                ask_suppression_reason=QuoteSuppressionReason.INSUFFICIENT_VOLATILITY,
            )

        gamma = float(self._config.gamma)
        risk_term = gamma * volatility.sigma_squared * tau_seconds
        reservation = mid_price_ticks - inventory_units * risk_term
        arrival_term = math.log1p(gamma / self._kappa) / gamma
        half_spread = risk_term / 2.0 + arrival_term
        if not all(math.isfinite(value) for value in (risk_term, reservation, half_spread)) or (
            half_spread <= 0.0
        ):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Baseline quote equation is not finite.")

        desired_bid_tick = math.floor(reservation - half_spread)
        desired_ask_tick = math.ceil(reservation + half_spread)
        passive_bid_tick = min(
            desired_bid_tick,
            state.best_bid_price4 // self._tick_size4,
            (state.best_ask_price4 - 1) // self._tick_size4,
        )
        passive_ask_tick = max(
            desired_ask_tick,
            (state.best_ask_price4 + self._tick_size4 - 1) // self._tick_size4,
            state.best_bid_price4 // self._tick_size4 + 1,
        )
        bid, bid_reason = self._proposal(
            side=1,
            tick_index=passive_bid_tick,
            inventory_shares=inventory_shares,
        )
        ask, ask_reason = self._proposal(
            side=-1,
            tick_index=passive_ask_tick,
            inventory_shares=inventory_shares,
        )
        return BaselineDecision(
            symbol=self._symbol,
            symbol_id=self._symbol_id,
            decision_message_index=decision_message_index,
            timestamp_ns=timestamp_ns,
            best_bid_price4=state.best_bid_price4,
            best_ask_price4=state.best_ask_price4,
            mid2=state.mid2,
            mid_price_ticks=mid_price_ticks,
            inventory_shares=inventory_shares,
            inventory_units=inventory_units,
            kappa=self._kappa,
            kappa_source=self._kappa_source,
            volatility=volatility,
            tau_seconds=tau_seconds,
            reservation_price_ticks=reservation,
            half_spread_ticks=half_spread,
            bid=bid,
            ask=ask,
            bid_suppression_reason=bid_reason,
            ask_suppression_reason=ask_reason,
        )


__all__ = [
    "BaselineDecision",
    "CausalVolatilityEstimator",
    "InventoryAwareAvellanedaStoikov",
    "QuoteProposal",
    "QuoteSuppressionReason",
    "VolatilityEstimate",
    "VolatilityState",
]
