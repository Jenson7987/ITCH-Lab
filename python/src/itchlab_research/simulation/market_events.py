"""Typed, queue-relevant adaptation of untrusted normalised event rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.interchange import EventKind
from itchlab_research.simulation.order import (
    MAX_TIMESTAMP_NS,
    MAX_UINT16,
    MAX_UINT32,
    MAX_UINT64,
)


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One source-ordered normalised event with queue-relevant fields."""

    message_index: int
    timestamp_ns: int
    symbol_id: int
    event_kind: EventKind
    primary_reference: int | None
    secondary_reference: int | None
    side: int | None
    price4: int | None
    quantity: int | None
    remaining_quantity: int | None
    execution_price4: int | None


def _fail(message: str, *, message_index: int | None = None) -> SimulationError:
    return SimulationError(ErrorCode.QUEUE_STATE, message, message_index=message_index)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _optional_int(
    value: object,
    *,
    name: str,
    maximum: int,
    message_index: int,
) -> int | None:
    if value is None:
        return None
    if not _valid_int(value, minimum=0, maximum=maximum):
        raise _fail(f"Market event {name} is invalid.", message_index=message_index)
    return cast(int, value)


def _require_fields(event: MarketEvent, *names: str) -> None:
    for name in names:
        if getattr(event, name) is None:
            raise _fail(
                f"Market event {event.event_kind.value} is missing {name}.",
                message_index=event.message_index,
            )


def _require_absent(event: MarketEvent, *names: str) -> None:
    for name in names:
        if getattr(event, name) is not None:
            raise _fail(
                f"Market event {event.event_kind.value} has unexpected {name}.",
                message_index=event.message_index,
            )


def validate_market_event(event: MarketEvent) -> None:
    """Validate kind-specific fields before queue or lifecycle state is touched."""
    if not isinstance(event, MarketEvent):
        raise _fail("Market event has the wrong domain type.")
    if not _valid_int(event.message_index, minimum=0, maximum=MAX_UINT64):
        raise _fail("Market event message index is invalid.")
    if not _valid_int(event.timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
        raise _fail("Market event timestamp is invalid.", message_index=event.message_index)
    if not _valid_int(event.symbol_id, minimum=1, maximum=MAX_UINT16):
        raise _fail("Market event symbol ID is invalid.", message_index=event.message_index)
    if not isinstance(event.event_kind, EventKind):
        raise _fail("Market event kind is invalid.", message_index=event.message_index)
    for name, value, maximum in (
        ("primary_reference", event.primary_reference, MAX_UINT64),
        ("secondary_reference", event.secondary_reference, MAX_UINT64),
        ("price4", event.price4, MAX_UINT32),
        ("quantity", event.quantity, MAX_UINT64),
        ("remaining_quantity", event.remaining_quantity, MAX_UINT64),
        ("execution_price4", event.execution_price4, MAX_UINT32),
    ):
        if value is not None and not _valid_int(value, minimum=0, maximum=maximum):
            raise _fail(f"Market event {name} is invalid.", message_index=event.message_index)
    if event.side is not None and (isinstance(event.side, bool) or event.side not in {-1, 1}):
        raise _fail("Market event side is invalid.", message_index=event.message_index)
    if event.quantity is not None and event.quantity == 0:
        raise _fail("Market event quantity must be positive.", message_index=event.message_index)

    lifecycle = {
        EventKind.ADD,
        EventKind.EXECUTE,
        EventKind.EXECUTE_PRICE,
        EventKind.CANCEL,
        EventKind.DELETE,
        EventKind.REPLACE,
    }
    if event.event_kind in lifecycle:
        _require_fields(
            event,
            "primary_reference",
            "side",
            "price4",
            "quantity",
            "remaining_quantity",
        )
    if event.event_kind in {EventKind.EXECUTE, EventKind.EXECUTE_PRICE, EventKind.REPLACE}:
        _require_fields(event, "secondary_reference")
    if event.event_kind is EventKind.EXECUTE_PRICE:
        _require_fields(event, "execution_price4")
    else:
        _require_absent(event, "execution_price4")

    if event.event_kind is EventKind.ADD:
        _require_absent(event, "secondary_reference")
        if event.remaining_quantity != event.quantity:
            raise _fail(
                "Add remaining quantity must equal event quantity.",
                message_index=event.message_index,
            )
    elif event.event_kind in {EventKind.CANCEL, EventKind.DELETE}:
        _require_absent(event, "secondary_reference")
        if event.event_kind is EventKind.DELETE and event.remaining_quantity != 0:
            raise _fail(
                "Delete remaining quantity must be zero.",
                message_index=event.message_index,
            )
    elif event.event_kind is EventKind.REPLACE:
        if event.remaining_quantity != event.quantity:
            raise _fail(
                "Replacement remaining quantity must equal event quantity.",
                message_index=event.message_index,
            )
    elif event.event_kind is EventKind.TRADE:
        _require_fields(
            event,
            "primary_reference",
            "secondary_reference",
            "side",
            "price4",
            "quantity",
        )
        _require_absent(event, "remaining_quantity")
    elif event.event_kind is EventKind.CROSS:
        _require_fields(event, "secondary_reference", "price4", "quantity")
        _require_absent(event, "primary_reference", "side", "remaining_quantity")
    elif event.event_kind is EventKind.BROKEN_TRADE:
        _require_fields(event, "primary_reference")
        _require_absent(
            event,
            "secondary_reference",
            "side",
            "price4",
            "quantity",
            "remaining_quantity",
        )
    elif event.event_kind is EventKind.TRADING_STATE:
        _require_absent(
            event,
            "primary_reference",
            "secondary_reference",
            "side",
            "price4",
            "quantity",
            "remaining_quantity",
        )


def adapt_market_event(row: Mapping[str, object] | MarketEvent) -> MarketEvent:
    """Build a validated queue-domain event from one normalised Parquet-style row."""
    if isinstance(row, MarketEvent):
        validate_market_event(row)
        return row
    if not isinstance(row, Mapping):
        raise _fail("Market event row is not a mapping.")

    raw_message_index = row.get("message_index")
    if not _valid_int(raw_message_index, minimum=0, maximum=MAX_UINT64):
        raise _fail("Market event message index is invalid.")
    message_index = cast(int, raw_message_index)
    raw_kind = row.get("event_kind")
    try:
        if isinstance(raw_kind, EventKind):
            kind = raw_kind
        elif isinstance(raw_kind, str):
            kind = EventKind(raw_kind)
        else:
            raise ValueError
    except ValueError as error:
        raise _fail("Market event kind is invalid.", message_index=message_index) from error

    raw_timestamp = row.get("timestamp_ns")
    raw_symbol = row.get("symbol_id")
    if not _valid_int(raw_timestamp, minimum=0, maximum=MAX_TIMESTAMP_NS):
        raise _fail("Market event timestamp is invalid.", message_index=message_index)
    if not _valid_int(raw_symbol, minimum=1, maximum=MAX_UINT16):
        raise _fail("Market event symbol ID is invalid.", message_index=message_index)
    raw_side = row.get("side")
    if raw_side is not None and (isinstance(raw_side, bool) or raw_side not in {-1, 1}):
        raise _fail("Market event side is invalid.", message_index=message_index)

    event = MarketEvent(
        message_index=message_index,
        timestamp_ns=cast(int, raw_timestamp),
        symbol_id=cast(int, raw_symbol),
        event_kind=kind,
        primary_reference=_optional_int(
            row.get("primary_reference"),
            name="primary_reference",
            maximum=MAX_UINT64,
            message_index=message_index,
        ),
        secondary_reference=_optional_int(
            row.get("secondary_reference"),
            name="secondary_reference",
            maximum=MAX_UINT64,
            message_index=message_index,
        ),
        side=cast(int | None, raw_side),
        price4=_optional_int(
            row.get("price4"),
            name="price4",
            maximum=MAX_UINT32,
            message_index=message_index,
        ),
        quantity=_optional_int(
            row.get("quantity"),
            name="quantity",
            maximum=MAX_UINT64,
            message_index=message_index,
        ),
        remaining_quantity=_optional_int(
            row.get("remaining_quantity"),
            name="remaining_quantity",
            maximum=MAX_UINT64,
            message_index=message_index,
        ),
        execution_price4=_optional_int(
            row.get("execution_price4"),
            name="execution_price4",
            maximum=MAX_UINT32,
            message_index=message_index,
        ),
    )
    validate_market_event(event)
    return event


__all__ = ["MarketEvent", "adapt_market_event", "validate_market_event"]
