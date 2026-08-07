"""Bounded causal microstructure features over ordered event and snapshot batches."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final, cast

import pyarrow as pa

from itchlab_research.config import FeatureConfig
from itchlab_research.conversion import event_schema, snapshot_schema
from itchlab_research.datasets.models import FeatureDefinition, FeaturePartitionContext
from itchlab_research.errors import ErrorCode, FeatureComputationError

_DAY_NS: Final = 86_400_000_000_000
_DEPTH_LEVELS: Final = (1, 5, 10)
_EVENT_WINDOWS: Final = (20, 100, 500)
_CLOCK_WINDOWS_NS: Final = (100_000_000, 1_000_000_000)
_OUTPUT_BATCH_ROWS: Final = 65_536
_MAX_INPUT_BATCH_ROWS: Final = 1_048_576
_OWNER: Final = "itchlab_research.datasets.features"
_EVENT_KINDS: Final = {
    "add",
    "execute",
    "execute_price",
    "cancel",
    "delete",
    "replace",
    "trade",
    "cross",
    "broken_trade",
    "trading_state",
}
_RATE_CATEGORY: Final = {
    "add": "add",
    "cancel": "cancel_delete",
    "delete": "cancel_delete",
    "execute": "execution",
    "execute_price": "execution",
}
_CLOCK_SUFFIX: Final = {100_000_000: "100ms", 1_000_000_000: "1s"}


def _fail(
    code: ErrorCode,
    message: str,
    *,
    message_index: int | None = None,
) -> FeatureComputationError:
    return FeatureComputationError(code, message, message_index=message_index)


def _validate_config(config: FeatureConfig) -> None:
    if not isinstance(config, FeatureConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Feature config has the wrong domain type.")
    if any(
        type(value) is not int
        for values in (config.depth_levels, config.event_windows, config.clock_windows_ns)
        for value in values
    ):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Feature config values must be integers.")
    if config.depth_levels != _DEPTH_LEVELS:
        raise _fail(ErrorCode.DEPTH, "Feature depth levels do not match version 1.")
    if config.event_windows != _EVENT_WINDOWS:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Feature event windows do not match version 1.")
    if config.clock_windows_ns != _CLOCK_WINDOWS_NS:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Feature clock windows do not match version 1.")


def _validate_context(context: FeaturePartitionContext) -> None:
    try:
        encoded_symbol = context.symbol.encode("ascii", errors="strict")
    except (AttributeError, UnicodeEncodeError) as error:
        raise _fail(
            ErrorCode.UNKNOWN_SYMBOL, "Feature partition symbol is not valid ASCII."
        ) from error
    if (
        not isinstance(context.trading_date, date)
        or not 1 <= len(encoded_symbol) <= 8
        or isinstance(context.symbol_id, bool)
        or not 1 <= context.symbol_id <= 65_535
    ):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Feature partition identity is invalid.")
    if (
        isinstance(context.tick_size4, bool)
        or not isinstance(context.tick_size4, int)
        or not 1 <= context.tick_size4 <= 0xFFFF_FFFF
    ):
        raise _fail(ErrorCode.PRICE, "Feature partition tick size is invalid.")
    if (
        isinstance(context.session_start_ns, bool)
        or isinstance(context.session_end_ns, bool)
        or not isinstance(context.session_start_ns, int)
        or not isinstance(context.session_end_ns, int)
        or not 0 <= context.session_start_ns < context.session_end_ns <= _DAY_NS
    ):
        raise _fail(ErrorCode.SESSION_WINDOW, "Feature partition session window is invalid.")


def _definition(
    name: str,
    *,
    dtype: str = "float64",
    nullable: bool,
    formula: str,
    lookback_kind: str = "current",
    lookback_value: int | None = None,
    unit: str,
    null_policy: str,
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        dtype=dtype,
        nullable=nullable,
        formula=formula,
        lookback_kind=lookback_kind,
        lookback_value=lookback_value,
        unit=unit,
        null_policy=null_policy,
        owner=_OWNER,
    )


def feature_catalogue(config: FeatureConfig) -> tuple[FeatureDefinition, ...]:
    """Return the exact ordered version-1 feature catalogue."""
    _validate_config(config)
    definitions = [
        _definition(
            "spread_ticks",
            nullable=False,
            formula="(ask_price4_1-bid_price4_1)/tick_size4",
            unit="ticks",
            null_policy="never",
        )
    ]
    for depth in config.depth_levels:
        definitions.append(
            _definition(
                f"imbalance_{depth}",
                nullable=True,
                formula=f"(B({depth})-A({depth}))/(B({depth})+A({depth}))",
                unit="ratio",
                null_policy="zero_denominator",
            )
        )
    definitions.extend(
        [
            _definition(
                "microprice4",
                nullable=False,
                formula="(ask_price4_1*B(1)+bid_price4_1*A(1))/(B(1)+A(1))",
                unit="price4",
                null_policy="never",
            ),
            _definition(
                "microprice_displacement_ticks",
                nullable=False,
                formula="(microprice4-(bid_price4_1+ask_price4_1)/2)/tick_size4",
                unit="ticks",
                null_policy="never",
            ),
            _definition(
                "aggressor_sign",
                dtype="int8",
                nullable=True,
                formula="-resting_side for an exact execute or execute_price trigger",
                unit="sign",
                null_policy="no_observable_execution_trigger",
            ),
            _definition(
                "session_progress",
                nullable=False,
                formula="clip((timestamp_ns-session_start_ns)/(session_end_ns-session_start_ns),0,1)",
                unit="fraction",
                null_policy="never",
            ),
            _definition(
                "session_progress_squared",
                nullable=False,
                formula="session_progress^2",
                unit="fraction_squared",
                null_policy="never",
            ),
        ]
    )
    for window in config.event_windows:
        definitions.extend(
            [
                _definition(
                    f"ofi_{window}",
                    nullable=True,
                    formula=f"sum(e_j) over {window} qualifying transitions",
                    lookback_kind="qualifying_transitions",
                    lookback_value=window,
                    unit="shares",
                    null_policy="incomplete_history",
                ),
                _definition(
                    f"ofi_normalised_{window}",
                    nullable=True,
                    formula=f"ofi_{window}/sum(B(1)+A(1)) over the same transitions",
                    lookback_kind="qualifying_transitions",
                    lookback_value=window,
                    unit="ratio",
                    null_policy="incomplete_history_or_zero_denominator",
                ),
                _definition(
                    f"realised_volatility_{window}",
                    nullable=True,
                    formula=f"sqrt(sum(log(mid_j/mid_(j-1))^2)) over {window} transitions",
                    lookback_kind="qualifying_transitions",
                    lookback_value=window,
                    unit="unannualised_volatility",
                    null_policy="incomplete_history",
                ),
                _definition(
                    f"execution_imbalance_{window}",
                    nullable=True,
                    formula="signed eligible E/C quantity / total eligible E/C quantity",
                    lookback_kind="qualifying_transitions",
                    lookback_value=window,
                    unit="ratio",
                    null_policy="incomplete_history_then_zero_without_execution",
                ),
            ]
        )
    for window_ns in config.clock_windows_ns:
        suffix = _CLOCK_SUFFIX[window_ns]
        window_seconds = window_ns / 1_000_000_000
        for category in ("add", "cancel_delete", "execution"):
            for side_name in ("bid", "ask"):
                definitions.append(
                    _definition(
                        f"{category}_{side_name}_rate_{suffix}",
                        nullable=True,
                        formula=(
                            f"count({category},{side_name}) in (t-{window_ns}ns,t] "
                            f"/ {window_seconds:g}s"
                        ),
                        lookback_kind="clock_ns",
                        lookback_value=window_ns,
                        unit="events_per_second",
                        null_policy="incomplete_history",
                    )
                )
    return tuple(definitions)


def feature_catalogue_document(config: FeatureConfig) -> dict[str, Any]:
    """Return a JSON-compatible deterministic feature-catalogue document."""
    return {
        "schema_version": 1,
        "features": [
            {
                "name": item.name,
                "dtype": item.dtype,
                "nullable": item.nullable,
                "formula": item.formula,
                "lookback": {
                    "kind": item.lookback_kind,
                    "value": item.lookback_value,
                },
                "unit": item.unit,
                "null_policy": item.null_policy,
                "owner": item.owner,
            }
            for item in feature_catalogue(config)
        ],
    }


def feature_schema(config: FeatureConfig) -> pa.Schema:
    """Return the exact version-1 feature-row Arrow schema."""
    fields = cast(
        list[Any],
        [
            pa.field("trading_date", pa.date32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("symbol_id", pa.uint16(), nullable=False),
            pa.field("message_index", pa.uint64(), nullable=False),
            pa.field("timestamp_ns", pa.uint64(), nullable=False),
            pa.field("qualifying_ordinal", pa.uint64(), nullable=False),
            pa.field("history_complete", pa.bool_(), nullable=False),
        ],
    )
    for item in feature_catalogue(config):
        dtype = pa.int8() if item.dtype == "int8" else pa.float64()
        fields.append(pa.field(item.name, dtype, nullable=item.nullable))
    return pa.schema(fields)


def _snapshot_depth(schema: pa.Schema) -> int:
    fixed_fields = 13
    variable_fields = len(schema) - fixed_fields
    if variable_fields <= 0 or variable_fields % 4:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot feature input schema is invalid.")
    depth = variable_fields // 4
    if not schema.equals(snapshot_schema(depth), check_metadata=False):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot feature input schema is unsupported.")
    return depth


def _batch_rows(
    batches: Iterable[pa.RecordBatch],
    *,
    kind: str,
) -> Iterator[dict[str, Any]]:
    expected_events = event_schema()
    expected_snapshot_depth: int | None = None
    for batch in batches:
        if not isinstance(batch, pa.RecordBatch) or batch.num_rows > _MAX_INPUT_BATCH_ROWS:
            raise _fail(ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} feature batch is invalid.")
        if kind == "event":
            if not batch.schema.equals(expected_events, check_metadata=False):
                raise _fail(ErrorCode.SCHEMA_VERSION, "Event feature input schema is unsupported.")
        else:
            depth = _snapshot_depth(batch.schema)
            if expected_snapshot_depth is None:
                expected_snapshot_depth = depth
            elif expected_snapshot_depth != depth:
                raise _fail(
                    ErrorCode.SCHEMA_VERSION, "Snapshot feature depth changed between batches."
                )
        yield from cast(list[dict[str, Any]], batch.to_pylist())


class _PeekableRows:
    def __init__(self, rows: Iterator[dict[str, Any]]) -> None:
        self._rows = rows
        self._next: dict[str, Any] | None = None
        self._finished = False

    def peek(self) -> dict[str, Any] | None:
        if self._next is None and not self._finished:
            try:
                self._next = next(self._rows)
            except StopIteration:
                self._finished = True
        return self._next

    def pop(self) -> dict[str, Any]:
        row = self.peek()
        if row is None:
            raise RuntimeError("Cannot pop an exhausted feature row iterator")
        self._next = None
        return row


@dataclass(slots=True)
class _RollingSum:
    window: int
    values: deque[float] = field(default_factory=deque)
    total: float = 0.0

    def append(self, value: float) -> None:
        self.values.append(value)
        self.total += value
        if len(self.values) > self.window:
            self.total -= self.values.popleft()

    @property
    def complete(self) -> bool:
        return len(self.values) == self.window


@dataclass(slots=True, eq=False)
class _ExecutionContribution:
    match_number: int
    signed_quantity: int
    quantity: int
    active: bool = True
    windows: set[int] = field(default_factory=set)


@dataclass(slots=True)
class _ExecutionWindow:
    window: int
    buckets: deque[tuple[_ExecutionContribution, ...]] = field(default_factory=deque)
    signed_quantity: int = 0
    quantity: int = 0

    def append(
        self, contributions: tuple[_ExecutionContribution, ...]
    ) -> tuple[_ExecutionContribution, ...]:
        evicted: tuple[_ExecutionContribution, ...] = ()
        if len(self.buckets) == self.window:
            evicted = self.buckets.popleft()
            for item in evicted:
                if self.window in item.windows:
                    item.windows.remove(self.window)
                    if item.active:
                        self.signed_quantity -= item.signed_quantity
                        self.quantity -= item.quantity
        self.buckets.append(contributions)
        for item in contributions:
            item.windows.add(self.window)
            if item.active:
                self.signed_quantity += item.signed_quantity
                self.quantity += item.quantity
        return evicted

    def break_contribution(self, contribution: _ExecutionContribution) -> None:
        if contribution.active and self.window in contribution.windows:
            self.signed_quantity -= contribution.signed_quantity
            self.quantity -= contribution.quantity


class _FeatureState:
    def __init__(self, config: FeatureConfig, context: FeaturePartitionContext) -> None:
        self.config = config
        self.context = context
        self.previous_event_index: int | None = None
        self.previous_event_timestamp: int | None = None
        self.previous_snapshot_index: int | None = None
        self.previous_snapshot_timestamp: int | None = None
        self.previous_top: tuple[int, int, int, int] | None = None
        self.qualifying_ordinal = -1
        self.ofi = {window: _RollingSum(window) for window in config.event_windows}
        self.depth_sum = {window: _RollingSum(window) for window in config.event_windows}
        self.squared_return = {window: _RollingSum(window) for window in config.event_windows}
        self.execution = {window: _ExecutionWindow(window) for window in config.event_windows}
        self.pending_executions: list[_ExecutionContribution] = []
        self.matches: dict[int, _ExecutionContribution] = {}
        self.clock_events: dict[tuple[int, str, int], deque[tuple[int, int]]] = {
            (window, category, side): deque()
            for window in config.clock_windows_ns
            for category in ("add", "cancel_delete", "execution")
            for side in (1, -1)
        }

    def _validate_identity(self, row: Mapping[str, Any], *, kind: str) -> tuple[int, int]:
        if (
            row.get("trading_date") != self.context.trading_date
            or row.get("symbol") != self.context.symbol
            or row.get("symbol_id") != self.context.symbol_id
        ):
            raise _fail(ErrorCode.INVARIANT, f"{kind.capitalize()} row left its feature partition.")
        message_index = row["message_index"]
        timestamp_ns = row["timestamp_ns"]
        if isinstance(message_index, bool) or not isinstance(message_index, int):
            raise _fail(ErrorCode.INVARIANT, f"{kind.capitalize()} message index is invalid.")
        if (
            isinstance(timestamp_ns, bool)
            or not isinstance(timestamp_ns, int)
            or not 0 <= timestamp_ns < _DAY_NS
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                f"{kind.capitalize()} timestamp is invalid.",
                message_index=message_index,
            )
        return message_index, timestamp_ns

    def consume_event(self, row: Mapping[str, Any]) -> None:
        message_index, timestamp_ns = self._validate_identity(row, kind="event")
        if self.previous_event_index is not None and message_index <= self.previous_event_index:
            raise _fail(
                ErrorCode.INVARIANT,
                "Event message indices are not strictly increasing.",
                message_index=message_index,
            )
        if (
            self.previous_event_timestamp is not None
            and timestamp_ns < self.previous_event_timestamp
        ):
            raise _fail(
                ErrorCode.TIMESTAMP, "Event timestamps are decreasing.", message_index=message_index
            )
        self.previous_event_index = message_index
        self.previous_event_timestamp = timestamp_ns

        in_session = cast(bool, row["in_session"])
        expected_in_session = (
            self.context.session_start_ns <= timestamp_ns < self.context.session_end_ns
        )
        if in_session != expected_in_session:
            raise _fail(
                ErrorCode.INVARIANT,
                "Event session flag disagrees with feature context.",
                message_index=message_index,
            )
        event_kind = cast(str, row["event_kind"])
        if event_kind not in _EVENT_KINDS:
            raise _fail(
                ErrorCode.SCHEMA_VERSION,
                "Event kind is unsupported by feature schema.",
                message_index=message_index,
            )
        if not in_session:
            return

        category = _RATE_CATEGORY.get(event_kind)
        if category is not None:
            side = row["side"]
            if side not in {1, -1}:
                raise _fail(
                    ErrorCode.INVARIANT,
                    "Rate event has no valid resting side.",
                    message_index=message_index,
                )
            for window in self.config.clock_windows_ns:
                queue = self.clock_events[(window, category, cast(int, side))]
                queue.append((timestamp_ns, message_index))
                lower = timestamp_ns - window
                while queue and queue[0][0] <= lower:
                    queue.popleft()

        if event_kind in {"execute", "execute_price"}:
            side = row["side"]
            quantity = row["quantity"]
            match_number = row["secondary_reference"]
            if (
                side not in {1, -1}
                or not isinstance(quantity, int)
                or quantity <= 0
                or not isinstance(match_number, int)
            ):
                raise _fail(
                    ErrorCode.INVARIANT,
                    "Execution event lacks causal flow fields.",
                    message_index=message_index,
                )
            if match_number in self.matches:
                raise _fail(
                    ErrorCode.INVARIANT,
                    "Execution match number is duplicated in an active feature window.",
                    message_index=message_index,
                )
            contribution = _ExecutionContribution(
                match_number=match_number,
                signed_quantity=-cast(int, side) * quantity,
                quantity=quantity,
            )
            self.pending_executions.append(contribution)
            self.matches[match_number] = contribution
        elif event_kind == "broken_trade":
            match_number = row["primary_reference"]
            if not isinstance(match_number, int):
                raise _fail(
                    ErrorCode.INVARIANT,
                    "Broken trade has no match number.",
                    message_index=message_index,
                )
            broken_contribution = self.matches.get(match_number)
            if broken_contribution is not None and broken_contribution.active:
                for execution_window in self.execution.values():
                    execution_window.break_contribution(broken_contribution)
                broken_contribution.active = False

    def validate_snapshot(self, row: Mapping[str, Any]) -> tuple[int, int]:
        message_index, timestamp_ns = self._validate_identity(row, kind="snapshot")
        if (
            self.previous_snapshot_index is not None
            and message_index <= self.previous_snapshot_index
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Snapshot message indices are not strictly increasing.",
                message_index=message_index,
            )
        if (
            self.previous_snapshot_timestamp is not None
            and timestamp_ns < self.previous_snapshot_timestamp
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Snapshot timestamps are decreasing.",
                message_index=message_index,
            )
        if not self.context.session_start_ns <= timestamp_ns < self.context.session_end_ns:
            raise _fail(
                ErrorCode.SESSION_WINDOW,
                "Snapshot lies outside the feature session.",
                message_index=message_index,
            )
        self.previous_snapshot_index = message_index
        self.previous_snapshot_timestamp = timestamp_ns
        return message_index, timestamp_ns

    def _clock_rates(self, timestamp_ns: int) -> dict[str, float | None]:
        values: dict[str, float | None] = {}
        for window_ns in self.config.clock_windows_ns:
            suffix = _CLOCK_SUFFIX[window_ns]
            complete = timestamp_ns - self.context.session_start_ns >= window_ns
            lower = timestamp_ns - window_ns
            seconds = window_ns / 1_000_000_000
            for category in ("add", "cancel_delete", "execution"):
                for side, side_name in ((1, "bid"), (-1, "ask")):
                    queue = self.clock_events[(window_ns, category, side)]
                    while queue and queue[0][0] <= lower:
                        queue.popleft()
                    name = f"{category}_{side_name}_rate_{suffix}"
                    values[name] = len(queue) / seconds if complete else None
        return values

    def _append_transition(self, top: tuple[int, int, int, int]) -> None:
        if self.previous_top is None:
            self.previous_top = top
            return
        bid, ask, bid_quantity, ask_quantity = top
        previous_bid, previous_ask, previous_bid_quantity, previous_ask_quantity = self.previous_top
        increment = (
            (bid_quantity if bid >= previous_bid else 0)
            - (previous_bid_quantity if bid <= previous_bid else 0)
            - (ask_quantity if ask <= previous_ask else 0)
            + (previous_ask_quantity if ask >= previous_ask else 0)
        )
        current_mid2 = bid + ask
        previous_mid2 = previous_bid + previous_ask
        if current_mid2 <= 0 or previous_mid2 <= 0:
            raise _fail(ErrorCode.PRICE, "A qualifying mid-price is not positive.")
        squared_return = math.log(current_mid2 / previous_mid2) ** 2
        for window in self.config.event_windows:
            self.ofi[window].append(float(increment))
            self.depth_sum[window].append(float(bid_quantity + ask_quantity))
            self.squared_return[window].append(squared_return)
        self.previous_top = top

    def _append_execution_bucket(self) -> None:
        bucket = tuple(self.pending_executions)
        self.pending_executions.clear()
        evicted: set[_ExecutionContribution] = set()
        for window in self.execution.values():
            evicted.update(window.append(bucket))
        for contribution in evicted:
            if (
                not contribution.windows
                and self.matches.get(contribution.match_number) is contribution
            ):
                del self.matches[contribution.match_number]

    @staticmethod
    def _depth_totals(
        snapshot: Mapping[str, Any],
        *,
        maximum_depth: int,
        message_index: int,
    ) -> dict[tuple[str, int], int]:
        totals: dict[tuple[str, int], int] = {}
        for side in ("bid", "ask"):
            total = 0
            previous_price: int | None = None
            padding_started = False
            for level in range(1, maximum_depth + 1):
                price = snapshot[f"{side}_price4_{level}"]
                quantity = snapshot[f"{side}_quantity_{level}"]
                if price is None and quantity is None:
                    padding_started = True
                    totals[(side, level)] = total
                    continue
                if price is None or quantity is None or padding_started:
                    raise _fail(
                        ErrorCode.INVARIANT,
                        "Qualifying depth slots are not canonically paired and contiguous.",
                        message_index=message_index,
                    )
                if not isinstance(price, int) or price <= 0:
                    raise _fail(
                        ErrorCode.PRICE,
                        "Qualifying depth contains an invalid price.",
                        message_index=message_index,
                    )
                if not isinstance(quantity, int) or quantity <= 0:
                    raise _fail(
                        ErrorCode.QUANTITY,
                        "Qualifying depth contains an invalid quantity.",
                        message_index=message_index,
                    )
                if previous_price is not None and (
                    (side == "bid" and price >= previous_price)
                    or (side == "ask" and price <= previous_price)
                ):
                    raise _fail(
                        ErrorCode.PRICE,
                        "Qualifying depth prices are not strictly ordered.",
                        message_index=message_index,
                    )
                total += quantity
                totals[(side, level)] = total
                previous_price = price
        return totals

    def feature_row(
        self,
        snapshot: Mapping[str, Any],
        current_event: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        message_index = cast(int, snapshot["message_index"])
        timestamp_ns = cast(int, snapshot["timestamp_ns"])
        best_bid = snapshot["bid_price4_1"]
        best_ask = snapshot["ask_price4_1"]
        best_bid_quantity = snapshot["bid_quantity_1"]
        best_ask_quantity = snapshot["ask_quantity_1"]
        if not all(
            isinstance(value, int)
            for value in (best_bid, best_ask, best_bid_quantity, best_ask_quantity)
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Qualifying snapshot has no complete top of book.",
                message_index=message_index,
            )
        bid = cast(int, best_bid)
        ask = cast(int, best_ask)
        bid_quantity = cast(int, best_bid_quantity)
        ask_quantity = cast(int, best_ask_quantity)
        if bid_quantity <= 0 or ask_quantity <= 0:
            raise _fail(
                ErrorCode.QUANTITY,
                "Qualifying top-of-book values are invalid.",
                message_index=message_index,
            )
        if bid + ask <= 0:
            raise _fail(
                ErrorCode.PRICE,
                "Qualifying mid-price is not positive.",
                message_index=message_index,
            )
        if ask <= bid:
            raise _fail(
                ErrorCode.BOOK_CROSSED,
                "Qualifying top of book is locked or crossed.",
                message_index=message_index,
            )
        depth_totals = self._depth_totals(
            snapshot,
            maximum_depth=max(self.config.depth_levels),
            message_index=message_index,
        )
        if (
            current_event is None
            or current_event["timestamp_ns"] != timestamp_ns
            or current_event["event_kind"] != snapshot["event_kind"]
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Qualifying snapshot has no exact triggering event.",
                message_index=message_index,
            )

        self.qualifying_ordinal += 1
        top = (bid, ask, bid_quantity, ask_quantity)
        self._append_transition(top)
        self._append_execution_bucket()

        row: dict[str, Any] = {
            "trading_date": self.context.trading_date,
            "symbol": self.context.symbol,
            "symbol_id": self.context.symbol_id,
            "message_index": message_index,
            "timestamp_ns": timestamp_ns,
            "qualifying_ordinal": self.qualifying_ordinal,
            "history_complete": (
                self.qualifying_ordinal >= max(self.config.event_windows)
                and timestamp_ns - self.context.session_start_ns
                >= max(self.config.clock_windows_ns)
            ),
            "spread_ticks": (ask - bid) / self.context.tick_size4,
        }
        for depth in self.config.depth_levels:
            bid_total = depth_totals[("bid", depth)]
            ask_total = depth_totals[("ask", depth)]
            denominator = bid_total + ask_total
            row[f"imbalance_{depth}"] = (
                (bid_total - ask_total) / denominator if denominator else None
            )
        microprice4 = (ask * bid_quantity + bid * ask_quantity) / (bid_quantity + ask_quantity)
        row["microprice4"] = microprice4
        row["microprice_displacement_ticks"] = (
            microprice4 - (bid + ask) / 2
        ) / self.context.tick_size4
        row["aggressor_sign"] = (
            -cast(int, current_event["side"])
            if current_event["event_kind"] in {"execute", "execute_price"}
            else None
        )
        progress = (timestamp_ns - self.context.session_start_ns) / (
            self.context.session_end_ns - self.context.session_start_ns
        )
        progress = min(1.0, max(0.0, progress))
        row["session_progress"] = progress
        row["session_progress_squared"] = progress * progress

        for window in self.config.event_windows:
            if not self.ofi[window].complete:
                row[f"ofi_{window}"] = None
                row[f"ofi_normalised_{window}"] = None
                row[f"realised_volatility_{window}"] = None
                row[f"execution_imbalance_{window}"] = None
                continue
            ofi = self.ofi[window].total
            rolling_denominator = self.depth_sum[window].total
            execution = self.execution[window]
            row[f"ofi_{window}"] = ofi
            row[f"ofi_normalised_{window}"] = (
                ofi / rolling_denominator if rolling_denominator else None
            )
            row[f"realised_volatility_{window}"] = math.sqrt(
                max(0.0, self.squared_return[window].total)
            )
            row[f"execution_imbalance_{window}"] = (
                execution.signed_quantity / execution.quantity if execution.quantity else 0.0
            )
        row.update(self._clock_rates(timestamp_ns))
        for name, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise _fail(
                    ErrorCode.INVARIANT,
                    f"Feature {name} is not finite.",
                    message_index=message_index,
                )
        return row


def _qualifies(snapshot: Mapping[str, Any]) -> bool:
    return bool(
        snapshot["top_n_changed"]
        and snapshot["trading_state"] == "trading"
        and snapshot["bid_price4_1"] is not None
        and snapshot["bid_quantity_1"] is not None
        and snapshot["ask_price4_1"] is not None
        and snapshot["ask_quantity_1"] is not None
    )


def _record_batch(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    try:
        return pa.RecordBatch.from_pylist(list(rows), schema=schema)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.INVARIANT, "Feature rows could not be represented by schema version 1."
        ) from error


def build_feature_batches(
    events: Iterable[pa.RecordBatch],
    snapshots: Iterable[pa.RecordBatch],
    config: FeatureConfig,
    context: FeaturePartitionContext,
) -> Iterator[pa.RecordBatch]:
    """Yield bounded causal feature batches for one ordered day/symbol partition."""
    _validate_config(config)
    _validate_context(context)
    schema = feature_schema(config)
    state = _FeatureState(config, context)
    event_rows = _PeekableRows(_batch_rows(events, kind="event"))
    output: list[dict[str, Any]] = []
    observed_depth: int | None = None

    for snapshot in _batch_rows(snapshots, kind="snapshot"):
        if observed_depth is None:
            observed_depth = (len(snapshot) - 13) // 4
            if observed_depth < max(config.depth_levels):
                raise _fail(ErrorCode.DEPTH, "Snapshot depth cannot satisfy the feature catalogue.")
        message_index, _timestamp_ns = state.validate_snapshot(snapshot)
        current_event: Mapping[str, Any] | None = None
        while (event := event_rows.peek()) is not None and cast(
            int, event["message_index"]
        ) <= message_index:
            consumed = event_rows.pop()
            state.consume_event(consumed)
            if consumed["message_index"] == message_index:
                current_event = consumed
        if not _qualifies(snapshot):
            continue
        output.append(state.feature_row(snapshot, current_event))
        if len(output) == _OUTPUT_BATCH_ROWS:
            yield _record_batch(output, schema)
            output.clear()
    if output:
        yield _record_batch(output, schema)


__all__ = [
    "build_feature_batches",
    "feature_catalogue",
    "feature_catalogue_document",
    "feature_schema",
]
