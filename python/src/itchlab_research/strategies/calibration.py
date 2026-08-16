"""Training-only execution-intensity calibration for the baseline strategy.

The fixed buckets, smoothing and weighted fit are specified in
``docs/01-product-requirements.md`` under ``Inventory-aware baseline``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import MAX_UINT64

MIN_DISTANCE_TICKS: Final = 0
MAX_DISTANCE_TICKS: Final = 10
_NANOSECONDS_PER_SECOND: Final = 1_000_000_000


class CalibrationSource(StrEnum):
    """Training-only source selected for one symbol's kappa estimate."""

    SYMBOL = "symbol"
    POOLED = "pooled"


@dataclass(frozen=True, slots=True)
class IntensityBucket:
    """Exact aggregate for one outward same-side-best distance bucket."""

    distance_ticks: int
    exposure_ns: int
    execution_count: int

    @property
    def exposure_seconds(self) -> float:
        """Return exact nanosecond exposure converted to seconds for calibration."""
        return self.exposure_ns / _NANOSECONDS_PER_SECOND


@dataclass(frozen=True, slots=True)
class SymbolIntensityCalibration:
    """One symbol's selected finite positive intensity-decay estimate."""

    symbol: str
    kappa: float
    intercept: float
    source: CalibrationSource
    buckets: tuple[IntensityBucket, ...]


@dataclass(frozen=True, slots=True)
class IntensityCalibration:
    """Complete deterministic training-only symbol and pooled calibration result."""

    training_dates: tuple[date, ...]
    pooled_kappa: float
    pooled_intercept: float
    pooled_buckets: tuple[IntensityBucket, ...]
    symbols: tuple[SymbolIntensityCalibration, ...]

    def for_symbol(self, symbol: str) -> SymbolIntensityCalibration:
        """Return one configured symbol calibration or fail with a stable code."""
        for calibration in self.symbols:
            if calibration.symbol == symbol:
                return calibration
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Strategy calibration symbol is not configured.")


@dataclass(frozen=True, slots=True)
class _Fit:
    kappa: float
    intercept: float


def _fail(code: ErrorCode, message: str) -> SimulationError:
    return SimulationError(code, message)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _validate_symbol(symbol: object) -> str:
    if not isinstance(symbol, str):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Calibration symbol is invalid.")
    try:
        encoded = symbol.encode("ascii", errors="strict")
    except UnicodeEncodeError as error:
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Calibration symbol is invalid ASCII.") from error
    if not 1 <= len(encoded) <= 8 or symbol.strip() != symbol:
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Calibration symbol is invalid.")
    return symbol


def _empty_buckets() -> list[list[int]]:
    return [[0, 0] for _ in range(MAX_DISTANCE_TICKS + 1)]


def _bucket_tuple(values: list[list[int]]) -> tuple[IntensityBucket, ...]:
    return tuple(
        IntensityBucket(
            distance_ticks=distance,
            exposure_ns=value[0],
            execution_count=value[1],
        )
        for distance, value in enumerate(values)
    )


def _fit_intensity(buckets: tuple[IntensityBucket, ...]) -> _Fit | None:
    points: list[tuple[float, float, float]] = []
    for bucket in buckets:
        exposure_seconds = bucket.exposure_seconds
        if exposure_seconds <= 0.0:
            continue
        intensity = (bucket.execution_count + 1.0) / (exposure_seconds + 1.0)
        if not math.isfinite(intensity) or intensity <= 0.0:
            return None
        points.append((float(bucket.distance_ticks), math.log(intensity), exposure_seconds))
    if len(points) < 2:
        return None

    total_weight = math.fsum(point[2] for point in points)
    if not math.isfinite(total_weight) or total_weight <= 0.0:
        return None
    mean_distance = math.fsum(x * weight for x, _, weight in points) / total_weight
    mean_log_intensity = math.fsum(y * weight for _, y, weight in points) / total_weight
    centred_square = math.fsum(weight * (x - mean_distance) ** 2 for x, _, weight in points)
    if not math.isfinite(centred_square) or centred_square <= 0.0:
        return None
    covariance = math.fsum(
        weight * (x - mean_distance) * (y - mean_log_intensity) for x, y, weight in points
    )
    slope = covariance / centred_square
    kappa = -slope
    intercept = mean_log_intensity - slope * mean_distance
    if not math.isfinite(kappa) or kappa <= 0.0 or not math.isfinite(intercept):
        return None
    return _Fit(kappa=kappa, intercept=intercept)


class CausalIntensityCalibrator:
    """Accumulate fixed buckets from declared training partitions in bounded memory."""

    def __init__(self, *, symbols: Iterable[str], training_dates: Iterable[date]) -> None:
        try:
            symbol_values = tuple(symbols)
            date_values = tuple(training_dates)
        except TypeError as error:
            raise _fail(ErrorCode.CONFIG_SCHEMA, "Calibration scope is not iterable.") from error
        if not symbol_values:
            raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Calibration requires at least one symbol.")
        validated_symbols = tuple(_validate_symbol(symbol) for symbol in symbol_values)
        if len(set(validated_symbols)) != len(validated_symbols):
            raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Calibration symbols contain duplicates.")
        if (
            not date_values
            or any(type(value) is not date for value in date_values)
            or tuple(sorted(date_values)) != date_values
            or len(set(date_values)) != len(date_values)
        ):
            raise _fail(
                ErrorCode.PARTITION,
                "Calibration training dates must be non-empty, sorted and unique.",
            )
        self._symbols = tuple(sorted(validated_symbols))
        self._training_dates = date_values
        self._training_date_set = frozenset(date_values)
        self._buckets = {symbol: _empty_buckets() for symbol in self._symbols}

    @property
    def symbols(self) -> tuple[str, ...]:
        """Return configured symbols in deterministic order."""
        return self._symbols

    @property
    def training_dates(self) -> tuple[date, ...]:
        """Return the exact authorised training partitions."""
        return self._training_dates

    def snapshot(self) -> tuple[tuple[str, tuple[IntensityBucket, ...]], ...]:
        """Return immutable current aggregates for testing and diagnostics."""
        return tuple((symbol, _bucket_tuple(self._buckets[symbol])) for symbol in self._symbols)

    def _bucket(self, trading_date: date, symbol: str, distance_ticks: int) -> list[int]:
        if type(trading_date) is not date:
            raise _fail(ErrorCode.PARTITION, "Calibration observation date is invalid.")
        if trading_date not in self._training_date_set:
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Intensity calibration may consume only declared training dates.",
            )
        validated_symbol = _validate_symbol(symbol)
        if validated_symbol not in self._buckets:
            raise _fail(
                ErrorCode.UNKNOWN_SYMBOL,
                "Calibration observation symbol is not configured.",
            )
        if not _valid_int(
            distance_ticks,
            minimum=MIN_DISTANCE_TICKS,
            maximum=MAX_DISTANCE_TICKS,
        ):
            raise _fail(
                ErrorCode.CONFIG_SCHEMA,
                "Calibration distance must be an integer from 0 through 10 ticks.",
            )
        return self._buckets[validated_symbol][distance_ticks]

    def record_exposure(
        self,
        trading_date: date,
        symbol: str,
        distance_ticks: int,
        exposure_ns: int,
    ) -> None:
        """Add exact visible-level exposure after validating the complete observation."""
        bucket = self._bucket(trading_date, symbol, distance_ticks)
        if not _valid_int(exposure_ns, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.TIMESTAMP, "Calibration exposure nanoseconds are invalid.")
        updated = bucket[0] + exposure_ns
        if updated > MAX_UINT64:
            raise _fail(ErrorCode.TIMESTAMP, "Calibration exposure nanoseconds overflowed.")
        bucket[0] = updated

    def record_execution(
        self,
        trading_date: date,
        symbol: str,
        distance_ticks: int,
        count: int = 1,
    ) -> None:
        """Count E/C messages at their pre-event resting-distance bucket."""
        bucket = self._bucket(trading_date, symbol, distance_ticks)
        if not _valid_int(count, minimum=1, maximum=MAX_UINT64):
            raise _fail(ErrorCode.QUANTITY, "Calibration execution count is invalid.")
        updated = bucket[1] + count
        if updated > MAX_UINT64:
            raise _fail(ErrorCode.QUANTITY, "Calibration execution count overflowed.")
        bucket[1] = updated

    def finalise(self) -> IntensityCalibration:
        """Fit pooled and per-symbol decay, applying only the documented pooled fallback."""
        pooled_values = _empty_buckets()
        symbol_buckets: dict[str, tuple[IntensityBucket, ...]] = {}
        for symbol in self._symbols:
            buckets = _bucket_tuple(self._buckets[symbol])
            symbol_buckets[symbol] = buckets
            for bucket in buckets:
                pooled = pooled_values[bucket.distance_ticks]
                pooled[0] += bucket.exposure_ns
                pooled[1] += bucket.execution_count
                if pooled[0] > MAX_UINT64 or pooled[1] > MAX_UINT64:
                    raise _fail(
                        ErrorCode.MODEL_TRAINING,
                        "Pooled intensity calibration aggregates overflowed.",
                    )

        pooled_buckets = _bucket_tuple(pooled_values)
        pooled_fit = _fit_intensity(pooled_buckets)
        if pooled_fit is None:
            raise _fail(
                ErrorCode.MODEL_TRAINING,
                "No finite positive pooled training intensity calibration is available.",
            )

        calibrated_symbols: list[SymbolIntensityCalibration] = []
        for symbol in self._symbols:
            buckets = symbol_buckets[symbol]
            fit = _fit_intensity(buckets)
            if fit is None:
                fit = pooled_fit
                source = CalibrationSource.POOLED
            else:
                source = CalibrationSource.SYMBOL
            calibrated_symbols.append(
                SymbolIntensityCalibration(
                    symbol=symbol,
                    kappa=fit.kappa,
                    intercept=fit.intercept,
                    source=source,
                    buckets=buckets,
                )
            )
        return IntensityCalibration(
            training_dates=self._training_dates,
            pooled_kappa=pooled_fit.kappa,
            pooled_intercept=pooled_fit.intercept,
            pooled_buckets=pooled_buckets,
            symbols=tuple(calibrated_symbols),
        )


__all__ = [
    "MAX_DISTANCE_TICKS",
    "MIN_DISTANCE_TICKS",
    "CalibrationSource",
    "CausalIntensityCalibrator",
    "IntensityBucket",
    "IntensityCalibration",
    "SymbolIntensityCalibration",
]
