"""Checked integer accounting for causal passive fills and terminal trades."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import (
    MAX_TIMESTAMP_NS,
    MAX_UINT16,
    MAX_UINT32,
    MAX_UINT64,
    SimulatedOrder,
    validate_simulated_order,
)
from itchlab_research.simulation.queue_model import QueueFill

MIN_INT64: Final = -(1 << 63)
MAX_INT64: Final = (1 << 63) - 1
MAX_FEE_MICROUSD_PER_SHARE: Final = 1_000_000
MAX_MID2: Final = 2 * MAX_UINT32


@dataclass(frozen=True, slots=True)
class AccountedFill:
    """One observed passive fill with the documented version-1 accounting fields."""

    fill_id: int
    simulated_order_id: int
    market_message_index: int
    timestamp_ns: int
    price4: int
    quantity: int
    fee_microusd: int
    cash_delta_microusd: int
    inventory_after: int


@dataclass(frozen=True, slots=True)
class TerminalTrade:
    """One prevalidated policy decision to close a non-zero terminal position."""

    symbol_id: int
    side: int
    price4: int
    quantity: int
    mark_mid2: int
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class LiquidationAccounting:
    """Integer accounting fields for one explicit terminal liquidation."""

    liquidation_id: int
    timestamp_ns: int
    symbol_id: int
    side: int
    price4: int
    quantity: int
    fee_microusd: int
    cash_delta_microusd: int
    inventory_before: int
    inventory_after: int
    mark_mid2: int
    slippage_microusd: int


@dataclass(frozen=True, slots=True)
class SymbolAccounting:
    """One symbol's current position, exact mark and observed inventory peak."""

    symbol_id: int
    inventory: int
    mark_mid2: int | None
    marked_value_microusd: int
    max_abs_inventory: int


@dataclass(frozen=True, slots=True)
class AccountingSnapshot:
    """Immutable integer-only view of the complete scenario accounting ledger."""

    symbols: tuple[SymbolAccounting, ...]
    passive_fill_count: int
    passive_fill_quantity: int
    liquidation_count: int
    liquidation_quantity: int
    gross_passive_cash_microusd: int
    gross_liquidation_cash_microusd: int
    maker_fee_microusd: int
    taker_fee_microusd: int
    signed_fee_microusd: int
    cash_microusd: int
    marked_inventory_value_microusd: int
    marked_pnl_microusd: int
    passive_spread_capture_microusd: int
    inventory_mark_to_market_microusd: int
    terminal_liquidation_slippage_microusd: int
    settled: bool


@dataclass(frozen=True, slots=True)
class PreparedTerminalAccounting:
    """Validated atomic terminal-ledger replacement awaiting session-order expiry."""

    revision: int
    records: tuple[LiquidationAccounting, ...]
    positions: tuple[tuple[int, int], ...]
    marks: tuple[tuple[int, int], ...]
    max_abs_positions: tuple[tuple[int, int], ...]
    gross_liquidation_cash_microusd: int
    taker_fee_microusd: int
    inventory_mark_to_market_microusd: int
    terminal_liquidation_slippage_microusd: int
    liquidation_count: int
    liquidation_quantity: int


def _fail(
    code: ErrorCode,
    message: str,
    *,
    simulated_order_id: int | None = None,
    message_index: int | None = None,
) -> SimulationError:
    return SimulationError(
        code,
        message,
        simulated_order_id=simulated_order_id,
        message_index=message_index,
    )


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _checked_int64(value: int, message: str) -> int:
    if not _valid_int(value, minimum=MIN_INT64, maximum=MAX_INT64):
        raise _fail(ErrorCode.COST, message)
    return cast(int, value)


def _checked_sum_int64(values: tuple[int, ...], message: str) -> int:
    total = 0
    for value in values:
        total = _checked_int64(total + value, message)
    return total


def _validate_fee_rate(value: object) -> int:
    if not _valid_int(
        value,
        minimum=-MAX_FEE_MICROUSD_PER_SHARE,
        maximum=MAX_FEE_MICROUSD_PER_SHARE,
    ):
        raise _fail(ErrorCode.COST, "Fee/rebate is outside the version-1 bounds.")
    return cast(int, value)


def _validate_symbol_id(value: object) -> int:
    if not _valid_int(value, minimum=1, maximum=MAX_UINT16):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Accounting symbol ID is invalid.")
    return cast(int, value)


def _validate_mark_mid2(value: object) -> int:
    if not _valid_int(value, minimum=0, maximum=MAX_MID2):
        raise _fail(ErrorCode.PRICE, "Accounting mid2 mark is invalid.")
    return cast(int, value)


def _marked_value(inventory: int, mark_mid2: int) -> int:
    return _checked_int64(
        inventory * mark_mid2 * 50,
        "Marked inventory value overflowed signed 64-bit microusd.",
    )


def _trade_cash_delta(side: int, price4: int, quantity: int) -> int:
    return _checked_int64(
        -side * price4 * quantity * 100,
        "Trade cash delta overflowed signed 64-bit microusd.",
    )


def _trade_fee(rate: int, quantity: int) -> int:
    return _checked_int64(
        rate * quantity,
        "Trade fee/rebate overflowed signed 64-bit microusd.",
    )


def _mark_relative_value(side: int, price4: int, quantity: int, mark_mid2: int) -> int:
    return _checked_int64(
        side * (mark_mid2 - 2 * price4) * quantity * 50,
        "Mark-relative trade value overflowed signed 64-bit microusd.",
    )


class AccountingLedger:
    """Own per-symbol inventory and exactly reconciled signed-microusd accounting state."""

    def __init__(self, *, maker_fee_microusd_per_share: int, inventory_limit: int) -> None:
        self._maker_fee_rate = _validate_fee_rate(maker_fee_microusd_per_share)
        if not _valid_int(inventory_limit, minimum=1, maximum=MAX_INT64):
            raise _fail(ErrorCode.INVENTORY_LIMIT, "Inventory limit is invalid.")
        self._inventory_limit = inventory_limit
        self._positions: dict[int, int] = {}
        self._marks: dict[int, int] = {}
        self._max_abs_positions: dict[int, int] = {}
        self._accounted_quantity_by_order: dict[int, int] = {}
        self._fill_keys: set[tuple[int, int, int]] = set()
        self._fills: list[AccountedFill] = []
        self._liquidations: list[LiquidationAccounting] = []
        self._next_fill_id = 0
        self._revision = 0
        self._passive_fill_quantity = 0
        self._liquidation_quantity = 0
        self._gross_passive_cash = 0
        self._gross_liquidation_cash = 0
        self._maker_fees = 0
        self._taker_fees = 0
        self._passive_spread_capture = 0
        self._inventory_mark_to_market = 0
        self._terminal_liquidation_slippage = 0
        self._settled = False
        self._prepared_terminal_plan: PreparedTerminalAccounting | None = None

    @property
    def inventory_limit(self) -> int:
        """Return the configured absolute per-symbol share limit."""
        return self._inventory_limit

    @property
    def fills(self) -> tuple[AccountedFill, ...]:
        """Return accounted passive fills in deterministic fill-ID order."""
        return tuple(self._fills)

    @property
    def liquidations(self) -> tuple[LiquidationAccounting, ...]:
        """Return explicit terminal liquidations in symbol order."""
        return tuple(self._liquidations)

    def inventory(self, symbol_id: int) -> int:
        """Return one symbol's current inventory, defaulting to zero."""
        symbol = _validate_symbol_id(symbol_id)
        return self._positions.get(symbol, 0)

    def update_mark(self, symbol_id: int, mark_mid2: int) -> AccountingSnapshot:
        """Apply one exact midpoint mark and return the resulting reconciled snapshot."""
        if self._settled:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal accounting is already settled.")
        symbol = _validate_symbol_id(symbol_id)
        mark = _validate_mark_mid2(mark_mid2)
        positions = dict(self._positions)
        marks = dict(self._marks)
        inventory_mark_to_market = self._apply_mark_to_copies(
            symbol,
            mark,
            positions=positions,
            marks=marks,
            inventory_mark_to_market=self._inventory_mark_to_market,
        )
        self._validate_reconciliation(
            positions=positions,
            marks=marks,
            inventory_mark_to_market=inventory_mark_to_market,
        )
        self._marks = marks
        self._inventory_mark_to_market = inventory_mark_to_market
        self._revision += 1
        return self.snapshot()

    def record_queue_fill(
        self,
        fill: QueueFill,
        order: SimulatedOrder,
        *,
        mark_mid2: int,
    ) -> AccountedFill:
        """Atomically account one causal queue fill at the current exact midpoint mark."""
        if self._settled:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Cannot account a fill after settlement.")
        self._validate_queue_fill(fill, order)
        mark = _validate_mark_mid2(mark_mid2)
        key = (fill.simulated_order_id, fill.market_message_index, fill.match_number)
        if key in self._fill_keys:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Queue fill was already accounted.",
                simulated_order_id=fill.simulated_order_id,
                message_index=fill.market_message_index,
            )
        if self._next_fill_id > MAX_UINT64:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Fill identifier overflowed.")

        previously_accounted = self._accounted_quantity_by_order.get(fill.simulated_order_id, 0)
        accounted_after = previously_accounted + fill.quantity
        expected_accounted = order.original_quantity - order.remaining_quantity
        if accounted_after != expected_accounted:
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Queue fills were not presented to accounting in lifecycle order.",
                simulated_order_id=fill.simulated_order_id,
                message_index=fill.market_message_index,
            )

        positions = dict(self._positions)
        marks = dict(self._marks)
        inventory_mark_to_market = self._apply_mark_to_copies(
            order.symbol_id,
            mark,
            positions=positions,
            marks=marks,
            inventory_mark_to_market=self._inventory_mark_to_market,
        )
        inventory_before = positions.get(order.symbol_id, 0)
        inventory_after = inventory_before + order.side * fill.quantity
        if not _valid_int(
            inventory_after,
            minimum=-self._inventory_limit,
            maximum=self._inventory_limit,
        ):
            raise _fail(
                ErrorCode.INVENTORY_LIMIT,
                "Passive fill would breach the configured inventory limit.",
                simulated_order_id=fill.simulated_order_id,
                message_index=fill.market_message_index,
            )
        positions[order.symbol_id] = inventory_after

        cash_delta = _trade_cash_delta(order.side, fill.price4, fill.quantity)
        fee = _trade_fee(self._maker_fee_rate, fill.quantity)
        gross_passive_cash = _checked_int64(
            self._gross_passive_cash + cash_delta,
            "Passive cash ledger overflowed signed 64-bit microusd.",
        )
        maker_fees = _checked_int64(
            self._maker_fees + fee,
            "Maker-fee ledger overflowed signed 64-bit microusd.",
        )
        passive_spread_capture = _checked_int64(
            self._passive_spread_capture
            + _mark_relative_value(order.side, fill.price4, fill.quantity, mark),
            "Passive spread-capture ledger overflowed signed 64-bit microusd.",
        )
        passive_fill_quantity = _checked_int64(
            self._passive_fill_quantity + fill.quantity,
            "Passive fill quantity overflowed the accounting domain.",
        )
        max_abs_positions = dict(self._max_abs_positions)
        max_abs_positions[order.symbol_id] = max(
            max_abs_positions.get(order.symbol_id, 0), abs(inventory_after)
        )

        self._validate_reconciliation(
            positions=positions,
            marks=marks,
            gross_passive_cash=gross_passive_cash,
            maker_fees=maker_fees,
            passive_spread_capture=passive_spread_capture,
            inventory_mark_to_market=inventory_mark_to_market,
        )
        accounted = AccountedFill(
            fill_id=self._next_fill_id,
            simulated_order_id=fill.simulated_order_id,
            market_message_index=fill.market_message_index,
            timestamp_ns=fill.timestamp_ns,
            price4=fill.price4,
            quantity=fill.quantity,
            fee_microusd=fee,
            cash_delta_microusd=cash_delta,
            inventory_after=inventory_after,
        )

        self._positions = positions
        self._marks = marks
        self._max_abs_positions = max_abs_positions
        self._accounted_quantity_by_order[fill.simulated_order_id] = accounted_after
        self._fill_keys.add(key)
        self._fills.append(accounted)
        self._next_fill_id += 1
        self._passive_fill_quantity = passive_fill_quantity
        self._gross_passive_cash = gross_passive_cash
        self._maker_fees = maker_fees
        self._passive_spread_capture = passive_spread_capture
        self._inventory_mark_to_market = inventory_mark_to_market
        self._revision += 1
        return accounted

    def prepare_terminal_accounting(
        self,
        trades: tuple[TerminalTrade, ...],
        *,
        taker_fee_microusd_per_share: int,
    ) -> PreparedTerminalAccounting:
        """Validate complete session-end accounting without mutating monetary state."""
        if self._settled:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal accounting is already settled.")
        taker_rate = _validate_fee_rate(taker_fee_microusd_per_share)
        positions = dict(self._positions)
        marks = dict(self._marks)
        max_abs_positions = dict(self._max_abs_positions)
        inventory_mark_to_market = self._inventory_mark_to_market
        gross_liquidation_cash = self._gross_liquidation_cash
        taker_fees = self._taker_fees
        terminal_slippage = self._terminal_liquidation_slippage
        liquidation_quantity = self._liquidation_quantity
        records: list[LiquidationAccounting] = []
        seen_symbols: set[int] = set()

        for trade in trades:
            self._validate_terminal_trade(trade)
            if trade.symbol_id in seen_symbols:
                raise _fail(
                    ErrorCode.SIMULATION_ANOMALY,
                    "Terminal accounting contains duplicate symbol trades.",
                )
            seen_symbols.add(trade.symbol_id)
            inventory_mark_to_market = self._apply_mark_to_copies(
                trade.symbol_id,
                trade.mark_mid2,
                positions=positions,
                marks=marks,
                inventory_mark_to_market=inventory_mark_to_market,
            )

        nonzero_symbols = {symbol for symbol, inventory in positions.items() if inventory != 0}
        if seen_symbols != nonzero_symbols:
            raise _fail(
                ErrorCode.PRICE,
                "Every non-zero terminal position requires exactly one visible opposite quote.",
            )

        for trade in trades:
            inventory_before = positions[trade.symbol_id]
            expected_side = -1 if inventory_before > 0 else 1
            if trade.side != expected_side or trade.quantity != abs(inventory_before):
                raise _fail(
                    ErrorCode.INVENTORY_LIMIT,
                    "Terminal trade does not exactly reduce its position to zero.",
                )
            cash_delta = _trade_cash_delta(trade.side, trade.price4, trade.quantity)
            fee = _trade_fee(taker_rate, trade.quantity)
            slippage = _mark_relative_value(
                trade.side,
                trade.price4,
                trade.quantity,
                trade.mark_mid2,
            )
            gross_liquidation_cash = _checked_int64(
                gross_liquidation_cash + cash_delta,
                "Liquidation cash ledger overflowed signed 64-bit microusd.",
            )
            taker_fees = _checked_int64(
                taker_fees + fee,
                "Taker-fee ledger overflowed signed 64-bit microusd.",
            )
            terminal_slippage = _checked_int64(
                terminal_slippage + slippage,
                "Terminal-slippage ledger overflowed signed 64-bit microusd.",
            )
            liquidation_quantity = _checked_int64(
                liquidation_quantity + trade.quantity,
                "Liquidation quantity overflowed the accounting domain.",
            )
            positions[trade.symbol_id] = 0
            records.append(
                LiquidationAccounting(
                    liquidation_id=len(self._liquidations) + len(records),
                    timestamp_ns=trade.timestamp_ns,
                    symbol_id=trade.symbol_id,
                    side=trade.side,
                    price4=trade.price4,
                    quantity=trade.quantity,
                    fee_microusd=fee,
                    cash_delta_microusd=cash_delta,
                    inventory_before=inventory_before,
                    inventory_after=0,
                    mark_mid2=trade.mark_mid2,
                    slippage_microusd=slippage,
                )
            )

        self._validate_reconciliation(
            positions=positions,
            marks=marks,
            gross_liquidation_cash=gross_liquidation_cash,
            taker_fees=taker_fees,
            inventory_mark_to_market=inventory_mark_to_market,
            terminal_liquidation_slippage=terminal_slippage,
        )
        plan = PreparedTerminalAccounting(
            revision=self._revision,
            records=tuple(records),
            positions=tuple(sorted(positions.items())),
            marks=tuple(sorted(marks.items())),
            max_abs_positions=tuple(sorted(max_abs_positions.items())),
            gross_liquidation_cash_microusd=gross_liquidation_cash,
            taker_fee_microusd=taker_fees,
            inventory_mark_to_market_microusd=inventory_mark_to_market,
            terminal_liquidation_slippage_microusd=terminal_slippage,
            liquidation_count=len(self._liquidations) + len(records),
            liquidation_quantity=liquidation_quantity,
        )
        self._prepared_terminal_plan = plan
        return plan

    def commit_terminal_accounting(
        self, plan: PreparedTerminalAccounting
    ) -> tuple[LiquidationAccounting, ...]:
        """Commit a previously validated terminal plan without fallible arithmetic."""
        if (
            not isinstance(plan, PreparedTerminalAccounting)
            or plan is not self._prepared_terminal_plan
            or plan.revision != self._revision
        ):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal accounting plan is stale.")
        if self._settled:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal accounting is already settled.")
        if plan.liquidation_count != len(self._liquidations) + len(plan.records):
            raise _fail(ErrorCode.INVARIANT, "Liquidation count did not reconcile.")
        self._positions = dict(plan.positions)
        self._marks = dict(plan.marks)
        self._max_abs_positions = dict(plan.max_abs_positions)
        self._gross_liquidation_cash = plan.gross_liquidation_cash_microusd
        self._taker_fees = plan.taker_fee_microusd
        self._inventory_mark_to_market = plan.inventory_mark_to_market_microusd
        self._terminal_liquidation_slippage = plan.terminal_liquidation_slippage_microusd
        self._liquidation_quantity = plan.liquidation_quantity
        self._liquidations.extend(plan.records)
        self._settled = True
        self._prepared_terminal_plan = None
        self._revision += 1
        return plan.records

    def snapshot(self) -> AccountingSnapshot:
        """Return a validated immutable snapshot of current accounting state."""
        marked_values: list[int] = []
        symbols: list[SymbolAccounting] = []
        for symbol_id in sorted(set(self._positions) | set(self._marks)):
            inventory = self._positions.get(symbol_id, 0)
            mark = self._marks.get(symbol_id)
            if inventory != 0 and mark is None:
                raise _fail(ErrorCode.PRICE, "Non-zero inventory has no valid mark.")
            marked_value = 0 if mark is None else _marked_value(inventory, mark)
            marked_values.append(marked_value)
            symbols.append(
                SymbolAccounting(
                    symbol_id=symbol_id,
                    inventory=inventory,
                    mark_mid2=mark,
                    marked_value_microusd=marked_value,
                    max_abs_inventory=self._max_abs_positions.get(symbol_id, 0),
                )
            )
        marked_inventory_value = _checked_sum_int64(
            tuple(marked_values),
            "Aggregate marked inventory overflowed signed 64-bit microusd.",
        )
        signed_fees = _checked_int64(
            self._maker_fees + self._taker_fees,
            "Aggregate fee ledger overflowed signed 64-bit microusd.",
        )
        gross_cash = _checked_int64(
            self._gross_passive_cash + self._gross_liquidation_cash,
            "Aggregate gross cash overflowed signed 64-bit microusd.",
        )
        cash = _checked_int64(
            gross_cash - signed_fees,
            "Net cash overflowed signed 64-bit microusd.",
        )
        marked_pnl = _checked_int64(
            cash + marked_inventory_value,
            "Marked P&L overflowed signed 64-bit microusd.",
        )
        fee_contribution = _checked_int64(
            -signed_fees,
            "Fee contribution overflowed signed 64-bit microusd.",
        )
        decomposition = _checked_sum_int64(
            (
                self._passive_spread_capture,
                self._inventory_mark_to_market,
                self._terminal_liquidation_slippage,
                fee_contribution,
            ),
            "P&L decomposition overflowed signed 64-bit microusd.",
        )
        if marked_pnl != decomposition:
            raise _fail(ErrorCode.INVARIANT, "Accounting P&L decomposition did not reconcile.")
        return AccountingSnapshot(
            symbols=tuple(symbols),
            passive_fill_count=len(self._fills),
            passive_fill_quantity=self._passive_fill_quantity,
            liquidation_count=len(self._liquidations),
            liquidation_quantity=self._liquidation_quantity,
            gross_passive_cash_microusd=self._gross_passive_cash,
            gross_liquidation_cash_microusd=self._gross_liquidation_cash,
            maker_fee_microusd=self._maker_fees,
            taker_fee_microusd=self._taker_fees,
            signed_fee_microusd=signed_fees,
            cash_microusd=cash,
            marked_inventory_value_microusd=marked_inventory_value,
            marked_pnl_microusd=marked_pnl,
            passive_spread_capture_microusd=self._passive_spread_capture,
            inventory_mark_to_market_microusd=self._inventory_mark_to_market,
            terminal_liquidation_slippage_microusd=self._terminal_liquidation_slippage,
            settled=self._settled,
        )

    def _validate_queue_fill(self, fill: QueueFill, order: SimulatedOrder) -> None:
        if not isinstance(fill, QueueFill) or not isinstance(order, SimulatedOrder):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Accounting fill inputs are invalid.")
        validate_simulated_order(order)
        if (
            fill.simulated_order_id != order.simulated_order_id
            or fill.price4 != order.price4
            or fill.remaining_quantity_after != order.remaining_quantity
        ):
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Queue fill does not match its simulated order.",
                simulated_order_id=fill.simulated_order_id,
                message_index=fill.market_message_index,
            )
        if not _valid_int(
            fill.market_message_index, minimum=0, maximum=MAX_UINT64
        ) or not _valid_int(fill.match_number, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Queue fill identity is invalid.")
        if not _valid_int(fill.timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(ErrorCode.TIMESTAMP, "Queue fill timestamp is invalid.")
        if not _valid_int(fill.price4, minimum=0, maximum=MAX_UINT32):
            raise _fail(ErrorCode.PRICE, "Queue fill Price4 is invalid.")
        if not _valid_int(fill.quantity, minimum=1, maximum=MAX_UINT64):
            raise _fail(ErrorCode.QUANTITY, "Queue fill quantity is invalid.")
        if not _valid_int(fill.queue_ahead_before, minimum=0, maximum=MAX_UINT64) or not _valid_int(
            fill.queue_ahead_after, minimum=0, maximum=fill.queue_ahead_before
        ):
            raise _fail(ErrorCode.QUEUE_STATE, "Queue fill ahead quantities are invalid.")

    @staticmethod
    def _validate_terminal_trade(trade: TerminalTrade) -> None:
        if not isinstance(trade, TerminalTrade):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Terminal trade has the wrong type.")
        _validate_symbol_id(trade.symbol_id)
        if trade.side not in {-1, 1} or isinstance(trade.side, bool):
            raise _fail(ErrorCode.INVENTORY_LIMIT, "Terminal trade side is invalid.")
        if not _valid_int(trade.price4, minimum=0, maximum=MAX_UINT32):
            raise _fail(ErrorCode.PRICE, "Terminal trade Price4 is invalid.")
        if not _valid_int(trade.quantity, minimum=1, maximum=MAX_UINT64):
            raise _fail(ErrorCode.QUANTITY, "Terminal trade quantity is invalid.")
        _validate_mark_mid2(trade.mark_mid2)
        if not _valid_int(trade.timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(ErrorCode.TIMESTAMP, "Terminal trade timestamp is invalid.")

    @staticmethod
    def _apply_mark_to_copies(
        symbol_id: int,
        mark_mid2: int,
        *,
        positions: dict[int, int],
        marks: dict[int, int],
        inventory_mark_to_market: int,
    ) -> int:
        previous_mark = marks.get(symbol_id)
        inventory = positions.get(symbol_id, 0)
        if previous_mark is not None:
            change = _checked_int64(
                inventory * (mark_mid2 - previous_mark) * 50,
                "Inventory mark-to-market change overflowed signed 64-bit microusd.",
            )
            inventory_mark_to_market = _checked_int64(
                inventory_mark_to_market + change,
                "Inventory mark-to-market ledger overflowed signed 64-bit microusd.",
            )
        marks[symbol_id] = mark_mid2
        positions.setdefault(symbol_id, inventory)
        return inventory_mark_to_market

    def _validate_reconciliation(
        self,
        *,
        positions: dict[int, int],
        marks: dict[int, int],
        gross_passive_cash: int | None = None,
        gross_liquidation_cash: int | None = None,
        maker_fees: int | None = None,
        taker_fees: int | None = None,
        passive_spread_capture: int | None = None,
        inventory_mark_to_market: int | None = None,
        terminal_liquidation_slippage: int | None = None,
    ) -> None:
        marked_values: list[int] = []
        for symbol_id, inventory in positions.items():
            mark = marks.get(symbol_id)
            if inventory != 0 and mark is None:
                raise _fail(ErrorCode.PRICE, "Non-zero inventory has no valid mark.")
            if mark is not None:
                marked_values.append(_marked_value(inventory, mark))
        marked_value = _checked_sum_int64(
            tuple(marked_values),
            "Aggregate marked inventory overflowed signed 64-bit microusd.",
        )
        passive_cash = (
            self._gross_passive_cash if gross_passive_cash is None else gross_passive_cash
        )
        liquidation_cash = (
            self._gross_liquidation_cash
            if gross_liquidation_cash is None
            else gross_liquidation_cash
        )
        maker = self._maker_fees if maker_fees is None else maker_fees
        taker = self._taker_fees if taker_fees is None else taker_fees
        spread = (
            self._passive_spread_capture
            if passive_spread_capture is None
            else passive_spread_capture
        )
        inventory_mtm = (
            self._inventory_mark_to_market
            if inventory_mark_to_market is None
            else inventory_mark_to_market
        )
        terminal = (
            self._terminal_liquidation_slippage
            if terminal_liquidation_slippage is None
            else terminal_liquidation_slippage
        )
        signed_fees = _checked_int64(
            maker + taker, "Aggregate fees overflowed signed 64-bit microusd."
        )
        cash = _checked_int64(
            _checked_int64(
                passive_cash + liquidation_cash,
                "Gross cash overflowed signed 64-bit microusd.",
            )
            - signed_fees,
            "Net cash overflowed signed 64-bit microusd.",
        )
        marked_pnl = _checked_int64(
            cash + marked_value,
            "Marked P&L overflowed signed 64-bit microusd.",
        )
        fee_contribution = _checked_int64(
            -signed_fees,
            "Fee contribution overflowed signed 64-bit microusd.",
        )
        decomposition = _checked_sum_int64(
            (spread, inventory_mtm, terminal, fee_contribution),
            "P&L decomposition overflowed signed 64-bit microusd.",
        )
        if marked_pnl != decomposition:
            raise _fail(ErrorCode.INVARIANT, "Accounting P&L decomposition did not reconcile.")


__all__ = [
    "MAX_FEE_MICROUSD_PER_SHARE",
    "MAX_INT64",
    "MAX_MID2",
    "MIN_INT64",
    "AccountedFill",
    "AccountingLedger",
    "AccountingSnapshot",
    "LiquidationAccounting",
    "PreparedTerminalAccounting",
    "SymbolAccounting",
    "TerminalTrade",
]
