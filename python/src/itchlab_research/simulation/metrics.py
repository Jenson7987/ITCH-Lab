"""Integer-only accounting metrics for one simulation day/scenario."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.accounting import (
    MAX_INT64,
    MIN_INT64,
    AccountingLedger,
    AccountingSnapshot,
)


@dataclass(frozen=True, slots=True)
class AccountingMetrics:
    """Reconciled metrics that remain valid for zero-fill and flat days."""

    passive_fill_count: int
    passive_fill_quantity: int
    liquidation_count: int
    liquidation_quantity: int
    gross_cash_microusd: int
    maker_fee_microusd: int
    taker_fee_microusd: int
    signed_fee_microusd: int
    fee_contribution_microusd: int
    cash_microusd: int
    ending_inventory_by_symbol: tuple[tuple[int, int], ...]
    max_abs_inventory_by_symbol: tuple[tuple[int, int], ...]
    marked_inventory_value_microusd: int
    passive_spread_capture_microusd: int
    inventory_mark_to_market_microusd: int
    terminal_liquidation_slippage_microusd: int
    marked_pnl_microusd: int
    reconciled: bool
    settled: bool


@dataclass(frozen=True, slots=True)
class TemporalMetrics:
    """Path-dependent execution metrics over one ordered scenario equity stream."""

    max_drawdown_microusd: int
    turnover_microusd: int
    adverse_selection_100ms_microusd: int | None
    adverse_selection_observation_count: int
    adverse_selection_eligible_fill_count: int
    adverse_selection_coverage: float


def _checked_int64(value: int, message: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not MIN_INT64 <= value <= MAX_INT64:
        raise SimulationError(ErrorCode.COST, message)
    return value


def _checked_add(left: int, right: int, message: str) -> int:
    return _checked_int64(left + right, message)


def temporal_metrics(
    equity_microusd: Iterable[int],
    trade_notionals_microusd: Iterable[int],
    adverse_selection_microusd: Iterable[int | None],
) -> TemporalMetrics:
    """Compute exact drawdown/turnover and a fixed-horizon adverse-selection summary."""
    try:
        equity = tuple(equity_microusd)
        notionals = tuple(trade_notionals_microusd)
        adverse = tuple(adverse_selection_microusd)
    except TypeError as error:
        raise SimulationError(
            ErrorCode.SIMULATION_ANOMALY, "Temporal metrics are not iterable."
        ) from error
    if any(not isinstance(value, int) or isinstance(value, bool) for value in equity):
        raise SimulationError(ErrorCode.COST, "Equity curve contains an invalid value.")
    peak = 0
    maximum_drawdown = 0
    for value in equity:
        checked = _checked_int64(value, "Equity curve overflowed signed 64-bit microusd.")
        peak = max(peak, checked)
        maximum_drawdown = max(
            maximum_drawdown,
            _checked_int64(peak - checked, "Drawdown overflowed signed 64-bit microusd."),
        )
    turnover = 0
    for value in notionals:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SimulationError(ErrorCode.COST, "Trade notional is invalid.")
        turnover = _checked_add(turnover, value, "Turnover overflowed signed 64-bit microusd.")
    observed = tuple(value for value in adverse if value is not None)
    adverse_total: int | None = None
    if observed:
        adverse_total = 0
        for value in observed:
            if not isinstance(value, int) or isinstance(value, bool):
                raise SimulationError(ErrorCode.COST, "Adverse-selection value is invalid.")
            adverse_total = _checked_add(
                adverse_total,
                value,
                "Adverse-selection total overflowed signed 64-bit microusd.",
            )
    eligible = len(adverse)
    return TemporalMetrics(
        max_drawdown_microusd=maximum_drawdown,
        turnover_microusd=turnover,
        adverse_selection_100ms_microusd=adverse_total,
        adverse_selection_observation_count=len(observed),
        adverse_selection_eligible_fill_count=eligible,
        adverse_selection_coverage=0.0 if eligible == 0 else len(observed) / eligible,
    )


def accounting_metrics(
    source: AccountingLedger | AccountingSnapshot,
) -> AccountingMetrics:
    """Build and independently verify one exact integer accounting summary."""
    if isinstance(source, AccountingLedger):
        snapshot = source.snapshot()
    elif isinstance(source, AccountingSnapshot):
        snapshot = source
    else:
        raise SimulationError(ErrorCode.SIMULATION_ANOMALY, "Metrics source is invalid.")

    gross_cash = _checked_int64(
        snapshot.gross_passive_cash_microusd + snapshot.gross_liquidation_cash_microusd,
        "Metric gross cash overflowed signed 64-bit microusd.",
    )
    fee_contribution = _checked_int64(
        -snapshot.signed_fee_microusd,
        "Metric fee contribution overflowed signed 64-bit microusd.",
    )
    decomposition = 0
    for component in (
        snapshot.passive_spread_capture_microusd,
        snapshot.inventory_mark_to_market_microusd,
        snapshot.terminal_liquidation_slippage_microusd,
        fee_contribution,
    ):
        decomposition = _checked_add(
            decomposition,
            component,
            "Metric P&L decomposition overflowed signed 64-bit microusd.",
        )
    cash_reconciliation = _checked_int64(
        gross_cash - snapshot.signed_fee_microusd,
        "Metric cash reconciliation overflowed signed 64-bit microusd.",
    )
    if (
        cash_reconciliation != snapshot.cash_microusd
        or decomposition != snapshot.marked_pnl_microusd
    ):
        raise SimulationError(ErrorCode.INVARIANT, "Accounting metrics did not reconcile.")

    return AccountingMetrics(
        passive_fill_count=snapshot.passive_fill_count,
        passive_fill_quantity=snapshot.passive_fill_quantity,
        liquidation_count=snapshot.liquidation_count,
        liquidation_quantity=snapshot.liquidation_quantity,
        gross_cash_microusd=gross_cash,
        maker_fee_microusd=snapshot.maker_fee_microusd,
        taker_fee_microusd=snapshot.taker_fee_microusd,
        signed_fee_microusd=snapshot.signed_fee_microusd,
        fee_contribution_microusd=fee_contribution,
        cash_microusd=snapshot.cash_microusd,
        ending_inventory_by_symbol=tuple(
            (symbol.symbol_id, symbol.inventory) for symbol in snapshot.symbols
        ),
        max_abs_inventory_by_symbol=tuple(
            (symbol.symbol_id, symbol.max_abs_inventory) for symbol in snapshot.symbols
        ),
        marked_inventory_value_microusd=snapshot.marked_inventory_value_microusd,
        passive_spread_capture_microusd=snapshot.passive_spread_capture_microusd,
        inventory_mark_to_market_microusd=snapshot.inventory_mark_to_market_microusd,
        terminal_liquidation_slippage_microusd=(snapshot.terminal_liquidation_slippage_microusd),
        marked_pnl_microusd=snapshot.marked_pnl_microusd,
        reconciled=True,
        settled=snapshot.settled,
    )


__all__ = ["AccountingMetrics", "TemporalMetrics", "accounting_metrics", "temporal_metrics"]
