"""Bounded future-horizon labels over ordered qualifying snapshot rows."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date
from typing import Any, Final, cast

import pyarrow as pa

from itchlab_research.config import LabelConfig
from itchlab_research.conversion import snapshot_schema
from itchlab_research.datasets.models import FeaturePartitionContext
from itchlab_research.errors import ErrorCode, LabelComputationError

_DAY_NS: Final = 86_400_000_000_000
_OUTPUT_BATCH_ROWS: Final = 65_536
_MAX_INPUT_BATCH_ROWS: Final = 1_048_576


def _fail(
    code: ErrorCode,
    message: str,
    *,
    message_index: int | None = None,
) -> LabelComputationError:
    return LabelComputationError(code, message, message_index=message_index)


def label_horizons(config: LabelConfig) -> tuple[int, ...]:
    """Return validated version-independent horizons in deterministic numeric order."""
    if not isinstance(config, LabelConfig):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Label config has the wrong domain type.")
    values = (config.primary_event_horizon, *config.secondary_event_horizons)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
        raise _fail(ErrorCode.HORIZON, "Label horizons must be positive integers.")
    if tuple(config.secondary_event_horizons) != tuple(sorted(config.secondary_event_horizons)):
        raise _fail(ErrorCode.HORIZON, "Secondary label horizons must be sorted.")
    if len(set(values)) != len(values):
        raise _fail(ErrorCode.HORIZON, "Label horizons must be unique.")
    if (
        isinstance(config.flat_threshold_ticks, bool)
        or not isinstance(config.flat_threshold_ticks, int)
        or config.flat_threshold_ticks < 0
    ):
        raise _fail(ErrorCode.HORIZON, "The flat label threshold must be a non-negative integer.")
    return tuple(sorted(values))


def label_column(horizon: int) -> str:
    """Return the stable version-1 column name for one event horizon."""
    return f"label_horizon_{horizon}"


def label_schema(config: LabelConfig) -> pa.Schema:
    """Return the exact raw future-label Arrow schema."""
    fields: list[pa.Field[Any]] = [
        pa.field("trading_date", pa.date32(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("symbol_id", pa.uint16(), nullable=False),
        pa.field("message_index", pa.uint64(), nullable=False),
        pa.field("timestamp_ns", pa.uint64(), nullable=False),
        pa.field("qualifying_ordinal", pa.uint64(), nullable=False),
    ]
    fields.extend(
        pa.field(label_column(horizon), pa.int8(), nullable=True)
        for horizon in label_horizons(config)
    )
    return pa.schema(fields)


def _validate_context(context: FeaturePartitionContext) -> None:
    try:
        encoded_symbol = context.symbol.encode("ascii", errors="strict")
    except (AttributeError, UnicodeEncodeError) as error:
        raise _fail(
            ErrorCode.UNKNOWN_SYMBOL, "Label partition symbol is not valid ASCII."
        ) from error
    if (
        not isinstance(context.trading_date, date)
        or not 1 <= len(encoded_symbol) <= 8
        or isinstance(context.symbol_id, bool)
        or not isinstance(context.symbol_id, int)
        or not 1 <= context.symbol_id <= 65_535
    ):
        raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Label partition identity is invalid.")
    if (
        isinstance(context.tick_size4, bool)
        or not isinstance(context.tick_size4, int)
        or not 1 <= context.tick_size4 <= 0xFFFF_FFFF
    ):
        raise _fail(ErrorCode.PRICE, "Label partition tick size is invalid.")
    if (
        isinstance(context.session_start_ns, bool)
        or isinstance(context.session_end_ns, bool)
        or not isinstance(context.session_start_ns, int)
        or not isinstance(context.session_end_ns, int)
        or not 0 <= context.session_start_ns < context.session_end_ns <= _DAY_NS
    ):
        raise _fail(ErrorCode.SESSION_WINDOW, "Label partition session window is invalid.")


def _snapshot_depth(schema: pa.Schema) -> int:
    fixed_fields = 13
    variable_fields = len(schema) - fixed_fields
    if variable_fields <= 0 or variable_fields % 4:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot label input schema is invalid.")
    depth = variable_fields // 4
    if not schema.equals(snapshot_schema(depth), check_metadata=False):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot label input schema is unsupported.")
    return depth


def _batch_rows(batches: Iterable[pa.RecordBatch]) -> Iterator[dict[str, Any]]:
    expected_depth: int | None = None
    for batch in batches:
        if not isinstance(batch, pa.RecordBatch) or batch.num_rows > _MAX_INPUT_BATCH_ROWS:
            raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot label batch is invalid.")
        depth = _snapshot_depth(batch.schema)
        if expected_depth is None:
            expected_depth = depth
        elif depth != expected_depth:
            raise _fail(ErrorCode.SCHEMA_VERSION, "Snapshot label depth changed between batches.")
        yield from cast(list[dict[str, Any]], batch.to_pylist())


def _qualifies(snapshot: Mapping[str, Any]) -> bool:
    return bool(
        snapshot["top_n_changed"]
        and snapshot["trading_state"] == "trading"
        and snapshot["bid_price4_1"] is not None
        and snapshot["bid_quantity_1"] is not None
        and snapshot["ask_price4_1"] is not None
        and snapshot["ask_quantity_1"] is not None
    )


def _label(delta_mid2: int, threshold_mid2: int) -> int:
    if delta_mid2 > threshold_mid2:
        return 1
    if delta_mid2 < -threshold_mid2:
        return -1
    return 0


def _record_batch(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    try:
        return pa.RecordBatch.from_pylist(list(rows), schema=schema)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.INVARIANT, "Label rows could not be represented by schema version 1."
        ) from error


def build_label_batches(
    snapshots: Iterable[pa.RecordBatch],
    config: LabelConfig,
    context: FeaturePartitionContext,
) -> Iterator[pa.RecordBatch]:
    """Yield bounded labels for one ordered day/symbol snapshot partition."""
    horizons = label_horizons(config)
    _validate_context(context)
    schema = label_schema(config)
    maximum_horizon = max(horizons)
    threshold_mid2 = 2 * context.tick_size4 * config.flat_threshold_ticks
    buffer: deque[dict[str, Any]] = deque()
    output: list[dict[str, Any]] = []
    previous_index: int | None = None
    previous_timestamp: int | None = None
    qualifying_ordinal = -1

    def emit_oldest() -> dict[str, Any]:
        current = buffer[0]
        row = {name: value for name, value in current.items() if name != "mid2"}
        current_mid2 = cast(int, current["mid2"])
        for horizon in horizons:
            row[label_column(horizon)] = (
                _label(cast(int, buffer[horizon]["mid2"]) - current_mid2, threshold_mid2)
                if horizon < len(buffer)
                else None
            )
        buffer.popleft()
        return row

    for snapshot in _batch_rows(snapshots):
        if (
            snapshot.get("trading_date") != context.trading_date
            or snapshot.get("symbol") != context.symbol
            or snapshot.get("symbol_id") != context.symbol_id
        ):
            raise _fail(ErrorCode.INVARIANT, "Snapshot row left its label partition.")
        message_index = snapshot["message_index"]
        timestamp_ns = snapshot["timestamp_ns"]
        if isinstance(message_index, bool) or not isinstance(message_index, int):
            raise _fail(ErrorCode.INVARIANT, "Snapshot label message index is invalid.")
        if (
            isinstance(timestamp_ns, bool)
            or not isinstance(timestamp_ns, int)
            or not context.session_start_ns <= timestamp_ns < context.session_end_ns
        ):
            raise _fail(
                ErrorCode.SESSION_WINDOW,
                "Snapshot lies outside the label session.",
                message_index=message_index,
            )
        if previous_index is not None and message_index <= previous_index:
            raise _fail(
                ErrorCode.INVARIANT,
                "Snapshot label message indices are not strictly increasing.",
                message_index=message_index,
            )
        if previous_timestamp is not None and timestamp_ns < previous_timestamp:
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Snapshot label timestamps are decreasing.",
                message_index=message_index,
            )
        previous_index = message_index
        previous_timestamp = timestamp_ns
        if not _qualifies(snapshot):
            continue

        bid = snapshot["bid_price4_1"]
        ask = snapshot["ask_price4_1"]
        bid_quantity = snapshot["bid_quantity_1"]
        ask_quantity = snapshot["ask_quantity_1"]
        if not all(isinstance(value, int) for value in (bid, ask, bid_quantity, ask_quantity)):
            raise _fail(
                ErrorCode.INVARIANT,
                "Qualifying label snapshot has no complete top of book.",
                message_index=message_index,
            )
        if cast(int, bid) <= 0 or cast(int, ask) <= cast(int, bid):
            raise _fail(
                ErrorCode.BOOK_CROSSED,
                "Qualifying label top of book is invalid.",
                message_index=message_index,
            )
        if cast(int, bid_quantity) <= 0 or cast(int, ask_quantity) <= 0:
            raise _fail(
                ErrorCode.QUANTITY,
                "Qualifying label top-of-book quantity is invalid.",
                message_index=message_index,
            )
        qualifying_ordinal += 1
        buffer.append(
            {
                "trading_date": context.trading_date,
                "symbol": context.symbol,
                "symbol_id": context.symbol_id,
                "message_index": message_index,
                "timestamp_ns": timestamp_ns,
                "qualifying_ordinal": qualifying_ordinal,
                "mid2": cast(int, bid) + cast(int, ask),
            }
        )
        if len(buffer) > maximum_horizon:
            output.append(emit_oldest())
        if len(output) == _OUTPUT_BATCH_ROWS:
            yield _record_batch(output, schema)
            output.clear()

    while buffer:
        output.append(emit_oldest())
        if len(output) == _OUTPUT_BATCH_ROWS:
            yield _record_batch(output, schema)
            output.clear()
    if output:
        yield _record_batch(output, schema)


__all__ = ["build_label_batches", "label_column", "label_horizons", "label_schema"]
