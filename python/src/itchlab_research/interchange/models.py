"""Immutable typed models yielded by interchange-v1 readers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class InterchangeKind(StrEnum):
    """Supported version-1 binary artefact kinds."""

    EVENTS = "events"
    SNAPSHOTS = "snapshots"


class EventKind(StrEnum):
    """Normalised event-kind values shared by event and snapshot records."""

    ADD = "add"
    EXECUTE = "execute"
    EXECUTE_PRICE = "execute_price"
    CANCEL = "cancel"
    DELETE = "delete"
    REPLACE = "replace"
    TRADE = "trade"
    CROSS = "cross"
    BROKEN_TRADE = "broken_trade"
    TRADING_STATE = "trading_state"


class TradingState(StrEnum):
    """Persisted snapshot trading states."""

    UNKNOWN = "unknown"
    PREOPEN = "preopen"
    TRADING = "trading"
    HALTED = "halted"
    PAUSED = "paused"
    QUOTATION_ONLY = "quotation_only"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SymbolEntry:
    """One canonical requested-order symbol dictionary entry."""

    symbol_id: int
    stock_locate: int
    symbol: str
    round_lot_size: int


@dataclass(frozen=True, slots=True)
class InterchangeMetadata:
    """Authenticated file metadata shared by every yielded batch."""

    kind: InterchangeKind
    schema_version: int
    record_size: int
    depth: int
    price_scale: int
    trading_date: date
    degraded: bool
    record_count: int
    config_sha256: str
    source_sha256: str
    file_sha256: str
    symbols: tuple[SymbolEntry, ...]


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One fully validated event-v1 record with explicit nullability."""

    trading_date: date
    message_index: int
    timestamp_ns: int
    symbol_id: int
    event_kind: EventKind
    source_type: str
    primary_reference: int | None
    secondary_reference: int | None
    side: int | None
    price4: int | None
    quantity: int | None
    remaining_quantity: int | None
    execution_price4: int | None
    aux_code: str | None
    event_subtype: str | None
    in_session: bool
    flags: int


@dataclass(frozen=True, slots=True)
class SnapshotDepthLevel:
    """One fixed snapshot depth slot with independently nullable sides."""

    bid_price4: int | None
    bid_quantity: int | None
    ask_price4: int | None
    ask_quantity: int | None


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """One fully validated snapshot-v1 record."""

    trading_date: date
    message_index: int
    timestamp_ns: int
    symbol_id: int
    event_kind: EventKind
    event_price4: int | None
    event_quantity: int | None
    last_trade_price4: int | None
    last_trade_quantity: int | None
    top_n_changed: bool
    trading_state: TradingState
    levels: tuple[SnapshotDepthLevel, ...]
    flags: int


@dataclass(frozen=True, slots=True)
class EventBatch:
    """One bounded, source-ordered batch of event records."""

    metadata: InterchangeMetadata
    records: tuple[EventRecord, ...]

    def __len__(self) -> int:
        return len(self.records)


@dataclass(frozen=True, slots=True)
class SnapshotBatch:
    """One bounded, source-ordered batch of snapshot records."""

    metadata: InterchangeMetadata
    records: tuple[SnapshotRecord, ...]

    def __len__(self) -> int:
        return len(self.records)


__all__ = [
    "EventBatch",
    "EventKind",
    "EventRecord",
    "InterchangeKind",
    "InterchangeMetadata",
    "SnapshotBatch",
    "SnapshotDepthLevel",
    "SnapshotRecord",
    "SymbolEntry",
    "TradingState",
]
