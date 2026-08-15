"""Explicit session-end expiry and visible-spread terminal liquidation policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.accounting import (
    MAX_FEE_MICROUSD_PER_SHARE,
    AccountingLedger,
    AccountingSnapshot,
    LiquidationAccounting,
    TerminalTrade,
)
from itchlab_research.simulation.order import (
    MAX_TIMESTAMP_NS,
    MAX_UINT16,
    MAX_UINT32,
    validate_simulated_order,
)
from itchlab_research.simulation.state_machine import OrderStateMachine, OrderTransition


@dataclass(frozen=True, slots=True)
class TerminalQuote:
    """Last valid visible two-sided quote retained at or before session end."""

    symbol_id: int
    timestamp_ns: int
    best_bid_price4: int
    best_ask_price4: int

    @property
    def mid2(self) -> int:
        """Return the exact sum of bid and ask Price4 values."""
        return self.best_bid_price4 + self.best_ask_price4


@dataclass(frozen=True, slots=True)
class TerminalSettlement:
    """Complete deterministic result of expiring orders and flattening inventory."""

    expired_orders: tuple[OrderTransition, ...]
    liquidations: tuple[LiquidationAccounting, ...]
    accounting: AccountingSnapshot


def _fail(code: ErrorCode, message: str) -> SimulationError:
    return SimulationError(code, message)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _validate_quote(quote: TerminalQuote, *, session_end_timestamp_ns: int) -> None:
    if not isinstance(quote, TerminalQuote):
        raise _fail(ErrorCode.PRICE, "Terminal quote has the wrong domain type.")
    if not _valid_int(quote.symbol_id, minimum=1, maximum=MAX_UINT16):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Terminal quote symbol ID is invalid.")
    if not _valid_int(
        quote.timestamp_ns,
        minimum=0,
        maximum=session_end_timestamp_ns,
    ):
        raise _fail(ErrorCode.TIMESTAMP, "Terminal quote is later than session end.")
    if not _valid_int(quote.best_bid_price4, minimum=0, maximum=MAX_UINT32) or not _valid_int(
        quote.best_ask_price4,
        minimum=0,
        maximum=MAX_UINT32,
    ):
        raise _fail(ErrorCode.PRICE, "Terminal quote Price4 is invalid.")
    if quote.best_bid_price4 > quote.best_ask_price4:
        raise _fail(ErrorCode.BOOK_CROSSED, "Terminal visible quote is crossed.")


def settle_session_end(
    state_machine: OrderStateMachine,
    ledger: AccountingLedger,
    *,
    session_end_timestamp_ns: int,
    last_quotes: Iterable[TerminalQuote],
    taker_fee_microusd_per_share: int,
) -> TerminalSettlement:
    """Expire open orders, cross non-zero positions, and atomically settle accounting."""
    if not isinstance(state_machine, OrderStateMachine) or not isinstance(ledger, AccountingLedger):
        raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal settlement inputs are invalid.")
    if not _valid_int(
        session_end_timestamp_ns,
        minimum=0,
        maximum=MAX_TIMESTAMP_NS,
    ):
        raise _fail(ErrorCode.TIMESTAMP, "Session-end timestamp is invalid.")
    if not _valid_int(
        taker_fee_microusd_per_share,
        minimum=-MAX_FEE_MICROUSD_PER_SHARE,
        maximum=MAX_FEE_MICROUSD_PER_SHARE,
    ):
        raise _fail(ErrorCode.COST, "Taker fee/rebate is outside the version-1 bounds.")

    try:
        quote_rows = tuple(last_quotes)
    except TypeError as error:
        raise _fail(ErrorCode.PRICE, "Terminal quotes are not iterable.") from error
    quotes_by_symbol: dict[int, TerminalQuote] = {}
    for quote in quote_rows:
        _validate_quote(quote, session_end_timestamp_ns=session_end_timestamp_ns)
        if quote.symbol_id in quotes_by_symbol:
            raise _fail(ErrorCode.PRICE, "Terminal quotes contain a duplicate symbol.")
        quotes_by_symbol[quote.symbol_id] = quote

    snapshot = ledger.snapshot()
    trades: list[TerminalTrade] = []
    for symbol in snapshot.symbols:
        if symbol.inventory == 0:
            continue
        terminal_quote = quotes_by_symbol.get(symbol.symbol_id)
        if terminal_quote is None:
            raise _fail(
                ErrorCode.PRICE,
                "Non-zero terminal inventory has no valid visible opposite quote.",
            )
        side = -1 if symbol.inventory > 0 else 1
        trades.append(
            TerminalTrade(
                symbol_id=symbol.symbol_id,
                side=side,
                price4=(
                    terminal_quote.best_bid_price4 if side == -1 else terminal_quote.best_ask_price4
                ),
                quantity=abs(symbol.inventory),
                mark_mid2=terminal_quote.mid2,
                timestamp_ns=session_end_timestamp_ns,
            )
        )

    plan = ledger.prepare_terminal_accounting(
        tuple(trades),
        taker_fee_microusd_per_share=taker_fee_microusd_per_share,
    )

    open_orders = tuple(order for order in state_machine.orders if not order.terminal)
    for order in open_orders:
        validate_simulated_order(order)
        if session_end_timestamp_ns < order.requested_timestamp_ns:
            raise _fail(ErrorCode.TIMESTAMP, "Session end precedes an open order request.")

    expirations = tuple(
        state_machine.expire(
            order.simulated_order_id,
            timestamp_ns=session_end_timestamp_ns,
        )
        for order in open_orders
    )
    liquidations = ledger.commit_terminal_accounting(plan)
    return TerminalSettlement(
        expired_orders=expirations,
        liquidations=liquidations,
        accounting=ledger.snapshot(),
    )


__all__ = ["TerminalQuote", "TerminalSettlement", "settle_session_end"]
