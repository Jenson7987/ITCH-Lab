"""Authenticated, chunked readers for event-v1 and snapshot-v1 files."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO, Final, TypeAlias

from itchlab_research.errors import ErrorCode, InterchangeReadError
from itchlab_research.interchange.models import (
    EventBatch,
    EventKind,
    EventRecord,
    InterchangeKind,
    InterchangeMetadata,
    SnapshotBatch,
    SnapshotDepthLevel,
    SnapshotRecord,
    SymbolEntry,
    TradingState,
)

_HEADER = struct.Struct("<8sHHHHIIHHQ32s32s4s")
_SYMBOL = struct.Struct("<HH8sI")
_EVENT = struct.Struct("<QQQQQIIIHBbcHB4sB7s")
_SNAPSHOT = struct.Struct("<QQHBBIQI4sQ")
_DEPTH_LEVEL = struct.Struct("<BB2sIQIQ")

_EVENT_MAGIC: Final = b"ITCHLE1\0"
_SNAPSHOT_MAGIC: Final = b"ITCHLS1\0"
_SCHEMA_VERSION: Final = 1
_HEADER_SIZE: Final = 104
_SYMBOL_SIZE: Final = 16
_EVENT_RECORD_SIZE: Final = 72
_SNAPSHOT_FIXED_SIZE: Final = 48
_SNAPSHOT_DEPTH_SIZE: Final = 28
_PRICE_SCALE: Final = 10_000
_MAX_DEPTH: Final = 50
_DAY_NS: Final = 86_400_000_000_000
_HASH_CHUNK_BYTES: Final = 1 << 20
_MAX_BATCH_BYTES: Final = 4 << 20

_PRIMARY: Final = 1 << 0
_SECONDARY: Final = 1 << 1
_SIDE: Final = 1 << 2
_PRICE: Final = 1 << 3
_QUANTITY: Final = 1 << 4
_REMAINING: Final = 1 << 5
_EXECUTION_PRICE: Final = 1 << 6
_AUXILIARY: Final = 1 << 7
_SUBTYPE: Final = 1 << 8
_IN_SESSION: Final = 1 << 9
_EVENT_ALLOWED_FLAGS: Final = (1 << 10) - 1

_SNAPSHOT_TRIGGER_PRICE: Final = 1 << 0
_SNAPSHOT_TRIGGER_QUANTITY: Final = 1 << 1
_SNAPSHOT_LAST_TRADE: Final = 1 << 2
_SNAPSHOT_TOP_CHANGED: Final = 1 << 6

_EVENT_KIND_BY_CODE: Final = {
    1: EventKind.ADD,
    2: EventKind.EXECUTE,
    3: EventKind.EXECUTE_PRICE,
    4: EventKind.CANCEL,
    5: EventKind.DELETE,
    6: EventKind.REPLACE,
    7: EventKind.TRADE,
    8: EventKind.CROSS,
    9: EventKind.BROKEN_TRADE,
    10: EventKind.TRADING_STATE,
}
_SOURCE_TYPES_BY_KIND: Final = {
    EventKind.ADD: frozenset({"A", "F"}),
    EventKind.EXECUTE: frozenset({"E"}),
    EventKind.EXECUTE_PRICE: frozenset({"C"}),
    EventKind.CANCEL: frozenset({"X"}),
    EventKind.DELETE: frozenset({"D"}),
    EventKind.REPLACE: frozenset({"U"}),
    EventKind.TRADE: frozenset({"P"}),
    EventKind.CROSS: frozenset({"Q"}),
    EventKind.BROKEN_TRADE: frozenset({"B"}),
    EventKind.TRADING_STATE: frozenset({"H"}),
}
_TRADING_STATE_BY_CODE: Final = {
    0: TradingState.UNKNOWN,
    1: TradingState.PREOPEN,
    2: TradingState.TRADING,
    3: TradingState.HALTED,
    4: TradingState.PAUSED,
    5: TradingState.QUOTATION_ONLY,
    6: TradingState.CLOSED,
}

_FileIdentity: TypeAlias = tuple[int, int, int, int, int]


@dataclass(slots=True)
class _OrderingState:
    message_index: int | None = None
    timestamp_ns: int | None = None


def _fail(
    code: ErrorCode,
    message: str,
    *,
    record_index: int | None = None,
) -> InterchangeReadError:
    return InterchangeReadError(code, message, record_index=record_index)


def _validate_reader_arguments(expected_sha256: str, chunk_records: int) -> None:
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise _fail(
            ErrorCode.HASH_MISMATCH,
            "Expected file SHA-256 must be 64 lowercase hexadecimal characters.",
        )
    if isinstance(chunk_records, bool) or not isinstance(chunk_records, int) or chunk_records <= 0:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Chunk record count must be a positive integer.")


def _read_exact(stream: BinaryIO, size: int, message: str) -> bytes:
    try:
        content = stream.read(size)
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "Interchange artefact could not be read.") from error
    if len(content) != size:
        raise _fail(ErrorCode.PARTIAL_ARTEFACT, message)
    return content


def _identity(file_status: os.stat_result) -> _FileIdentity:
    return (
        file_status.st_dev,
        file_status.st_ino,
        file_status.st_size,
        file_status.st_mtime_ns,
        file_status.st_ctime_ns,
    )


def _ensure_unchanged(stream: BinaryIO, expected: _FileIdentity) -> None:
    try:
        actual = _identity(os.fstat(stream.fileno()))
    except OSError as error:
        raise _fail(
            ErrorCode.HASH_MISMATCH,
            "Interchange artefact identity could not be rechecked.",
        ) from error
    if actual != expected:
        raise _fail(ErrorCode.HASH_MISMATCH, "Interchange artefact changed while being read.")


def _trading_date(value: int) -> date:
    year = value // 10_000
    month = value // 100 % 100
    day = value % 100
    try:
        return date(year, month, day)
    except ValueError as error:
        raise _fail(ErrorCode.INVARIANT, "Interchange header trading date is invalid.") from error


def _parse_symbol(raw: bytes) -> str:
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        raise _fail(ErrorCode.INVARIANT, "Interchange symbol dictionary contains invalid ASCII.")
    symbol = raw.rstrip(b" ")
    if not symbol or symbol.startswith(b" "):
        raise _fail(ErrorCode.INVARIANT, "Interchange symbol dictionary is not canonical.")
    return symbol.decode("ascii")


def _parse_metadata(
    stream: BinaryIO,
    file_size: int,
    expected_kind: InterchangeKind,
    file_sha256: str,
) -> tuple[InterchangeMetadata, int]:
    encoded_header = _read_exact(
        stream,
        _HEADER_SIZE,
        "Interchange artefact does not contain a complete header.",
    )
    if encoded_header == bytes(_HEADER_SIZE):
        raise _fail(
            ErrorCode.PARTIAL_ARTEFACT,
            "Interchange artefact contains a placeholder partial header.",
        )

    (
        magic,
        schema_version,
        header_size,
        record_size,
        depth,
        price_scale,
        encoded_trading_date,
        symbol_count,
        header_flags,
        record_count,
        config_hash,
        source_hash,
        reserved,
    ) = _HEADER.unpack(encoded_header)

    kind_by_magic = {
        _EVENT_MAGIC: InterchangeKind.EVENTS,
        _SNAPSHOT_MAGIC: InterchangeKind.SNAPSHOTS,
    }
    kind = kind_by_magic.get(magic)
    if kind is None or kind is not expected_kind:
        raise _fail(
            ErrorCode.SCHEMA_VERSION,
            "Interchange magic is not the expected supported version-1 file kind.",
        )
    if schema_version != _SCHEMA_VERSION:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Interchange schema version is unsupported.")
    if header_size != _HEADER_SIZE or price_scale != _PRICE_SCALE:
        raise _fail(
            ErrorCode.SCHEMA_VERSION,
            "Interchange header size or price scale is unsupported.",
        )
    if symbol_count == 0 or header_flags & 0xFFFE or reserved != b"\0" * 4:
        raise _fail(
            ErrorCode.INVARIANT,
            "Interchange header flags, dictionary count, or reserved bytes are invalid.",
        )

    if kind is InterchangeKind.EVENTS:
        valid_layout = depth == 0 and record_size == _EVENT_RECORD_SIZE
    else:
        valid_layout = (
            1 <= depth <= _MAX_DEPTH
            and record_size == _SNAPSHOT_FIXED_SIZE + _SNAPSHOT_DEPTH_SIZE * depth
        )
    if not valid_layout:
        raise _fail(
            ErrorCode.SCHEMA_VERSION,
            "Interchange depth and record size do not match version 1.",
        )
    if config_hash == bytes(32) or source_hash == bytes(32):
        raise _fail(
            ErrorCode.PARTIAL_ARTEFACT,
            "Interchange header contains placeholder identity hashes.",
        )

    symbols: list[SymbolEntry] = []
    stock_locates: set[int] = set()
    symbol_names: set[str] = set()
    for index in range(symbol_count):
        encoded_symbol = _read_exact(
            stream,
            _SYMBOL_SIZE,
            "Interchange artefact has an incomplete symbol dictionary.",
        )
        symbol_id, stock_locate, raw_symbol, round_lot_size = _SYMBOL.unpack(encoded_symbol)
        symbol = _parse_symbol(raw_symbol)
        if (
            symbol_id != index + 1
            or stock_locate == 0
            or stock_locate in stock_locates
            or symbol in symbol_names
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Interchange symbol dictionary is not canonical or unique.",
            )
        stock_locates.add(stock_locate)
        symbol_names.add(symbol)
        symbols.append(SymbolEntry(symbol_id, stock_locate, symbol, round_lot_size))

    records_offset = _HEADER_SIZE + symbol_count * _SYMBOL_SIZE
    expected_size = records_offset + record_count * record_size
    if file_size != expected_size:
        raise _fail(
            ErrorCode.PARTIAL_ARTEFACT,
            "Interchange size does not match its dictionary and declared record count.",
        )

    metadata = InterchangeMetadata(
        kind=kind,
        schema_version=schema_version,
        record_size=record_size,
        depth=depth,
        price_scale=price_scale,
        trading_date=_trading_date(encoded_trading_date),
        degraded=bool(header_flags & 1),
        record_count=record_count,
        config_sha256=config_hash.hex(),
        source_sha256=source_hash.hex(),
        file_sha256=file_sha256,
        symbols=tuple(symbols),
    )
    return metadata, records_offset


def _authenticate(
    stream: BinaryIO,
    expected_sha256: str,
    file_identity: _FileIdentity,
    records_offset: int,
) -> None:
    digest = hashlib.sha256()
    try:
        stream.seek(0)
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "Interchange artefact could not be hashed.") from error
    if digest.hexdigest() != expected_sha256:
        raise _fail(ErrorCode.HASH_MISMATCH, "Interchange file SHA-256 does not match expected.")
    _ensure_unchanged(stream, file_identity)
    try:
        stream.seek(records_offset)
    except OSError as error:
        raise _fail(
            ErrorCode.PARTIAL_ARTEFACT, "Interchange record region is unreachable."
        ) from error


def _bounded_batch_count(metadata: InterchangeMetadata, requested: int, remaining: int) -> int:
    encoded_limit = max(1, _MAX_BATCH_BYTES // metadata.record_size)
    return min(requested, encoded_limit, remaining)


def _required_event_flags(kind: EventKind, source_type: str) -> int:
    if kind is EventKind.ADD:
        return (
            _PRIMARY
            | _SIDE
            | _PRICE
            | _QUANTITY
            | _REMAINING
            | (_AUXILIARY if source_type == "F" else 0)
        )
    if kind is EventKind.EXECUTE:
        return _PRIMARY | _SECONDARY | _SIDE | _PRICE | _QUANTITY | _REMAINING
    if kind is EventKind.EXECUTE_PRICE:
        return _PRIMARY | _SECONDARY | _SIDE | _PRICE | _QUANTITY | _REMAINING | _EXECUTION_PRICE
    if kind in {EventKind.CANCEL, EventKind.DELETE}:
        return _PRIMARY | _SIDE | _PRICE | _QUANTITY | _REMAINING
    if kind is EventKind.REPLACE:
        return _PRIMARY | _SECONDARY | _SIDE | _PRICE | _QUANTITY | _REMAINING
    if kind is EventKind.TRADE:
        return _PRIMARY | _SECONDARY | _SIDE | _PRICE | _QUANTITY
    if kind is EventKind.CROSS:
        return _SECONDARY | _PRICE | _QUANTITY | _SUBTYPE
    if kind is EventKind.BROKEN_TRADE:
        return _PRIMARY
    return _SUBTYPE


def _validate_ordering(
    message_index: int,
    timestamp_ns: int,
    symbol_id: int,
    metadata: InterchangeMetadata,
    ordering: _OrderingState,
    record_index: int,
) -> None:
    if (
        symbol_id == 0
        or symbol_id > len(metadata.symbols)
        or timestamp_ns >= _DAY_NS
        or (ordering.message_index is not None and message_index <= ordering.message_index)
        or (ordering.timestamp_ns is not None and timestamp_ns < ordering.timestamp_ns)
    ):
        raise _fail(
            ErrorCode.INVARIANT,
            "Interchange symbol, timestamp, or source ordering is invalid.",
            record_index=record_index,
        )


def _decode_event(
    encoded: bytes,
    metadata: InterchangeMetadata,
    ordering: _OrderingState,
    record_index: int,
) -> EventRecord:
    (
        message_index,
        timestamp_ns,
        primary_reference,
        secondary_reference,
        quantity,
        price4,
        remaining_quantity,
        execution_price4,
        symbol_id,
        kind_code,
        side,
        raw_source_type,
        flags,
        reserved,
        raw_auxiliary,
        subtype,
        reserved_tail,
    ) = _EVENT.unpack(encoded)

    kind = _EVENT_KIND_BY_CODE.get(kind_code)
    source_type = chr(raw_source_type[0])
    if kind is None or source_type not in _SOURCE_TYPES_BY_KIND[kind]:
        raise _fail(
            ErrorCode.INVARIANT,
            "Event kind and source type are unsupported or inconsistent.",
            record_index=record_index,
        )
    required_flags = _required_event_flags(kind, source_type)
    optional_flags = _IN_SESSION | (_AUXILIARY if kind is EventKind.TRADING_STATE else 0)
    if (
        flags & ~_EVENT_ALLOWED_FLAGS
        or flags & required_flags != required_flags
        or flags & ~(required_flags | optional_flags)
        or reserved != 0
        or reserved_tail != b"\0" * 7
    ):
        raise _fail(
            ErrorCode.INVARIANT,
            "Event validity flags or reserved bytes are invalid.",
            record_index=record_index,
        )

    absent_values = (
        (_PRIMARY, primary_reference),
        (_SECONDARY, secondary_reference),
        (_QUANTITY, quantity),
        (_PRICE, price4),
        (_REMAINING, remaining_quantity),
        (_EXECUTION_PRICE, execution_price4),
        (_SIDE, side),
        (_SUBTYPE, subtype),
    )
    if any(flags & flag == 0 and value != 0 for flag, value in absent_values) or (
        flags & _AUXILIARY == 0 and raw_auxiliary != b"\0" * 4
    ):
        raise _fail(
            ErrorCode.INVARIANT,
            "Event absent fields do not use the canonical zero representation.",
            record_index=record_index,
        )
    if (flags & _SIDE and side not in {-1, 1}) or (flags & _SIDE == 0 and side != 0):
        raise _fail(
            ErrorCode.INVARIANT,
            "Event side encoding is invalid.",
            record_index=record_index,
        )
    if flags & _QUANTITY and quantity == 0:
        raise _fail(
            ErrorCode.QUANTITY,
            "Event valid quantity must be positive.",
            record_index=record_index,
        )
    if kind is EventKind.ADD and remaining_quantity != quantity:
        raise _fail(
            ErrorCode.QUANTITY,
            "Event add remaining quantity must equal quantity.",
            record_index=record_index,
        )
    if kind is EventKind.DELETE and remaining_quantity != 0:
        raise _fail(
            ErrorCode.QUANTITY,
            "Event delete remaining quantity must be zero.",
            record_index=record_index,
        )
    if kind is EventKind.REPLACE and remaining_quantity != quantity:
        raise _fail(
            ErrorCode.QUANTITY,
            "Event replacement remaining quantity must equal quantity.",
            record_index=record_index,
        )

    auxiliary: str | None = None
    if flags & _AUXILIARY:
        if any(byte < 0x20 or byte > 0x7E for byte in raw_auxiliary):
            raise _fail(
                ErrorCode.INVARIANT,
                "Event auxiliary code is not printable ASCII.",
                record_index=record_index,
            )
        auxiliary = raw_auxiliary.decode("ascii").rstrip(" ")

    event_subtype: str | None = None
    if flags & _SUBTYPE:
        if subtype > 0x7F or (kind is EventKind.TRADING_STATE and subtype not in b"HPQT"):
            raise _fail(
                ErrorCode.INVARIANT,
                "Event subtype is invalid.",
                record_index=record_index,
            )
        event_subtype = chr(subtype)

    _validate_ordering(
        message_index,
        timestamp_ns,
        symbol_id,
        metadata,
        ordering,
        record_index,
    )
    ordering.message_index = message_index
    ordering.timestamp_ns = timestamp_ns
    return EventRecord(
        trading_date=metadata.trading_date,
        message_index=message_index,
        timestamp_ns=timestamp_ns,
        symbol_id=symbol_id,
        event_kind=kind,
        source_type=source_type,
        primary_reference=primary_reference if flags & _PRIMARY else None,
        secondary_reference=secondary_reference if flags & _SECONDARY else None,
        side=side if flags & _SIDE else None,
        price4=price4 if flags & _PRICE else None,
        quantity=quantity if flags & _QUANTITY else None,
        remaining_quantity=remaining_quantity if flags & _REMAINING else None,
        execution_price4=execution_price4 if flags & _EXECUTION_PRICE else None,
        aux_code=auxiliary,
        event_subtype=event_subtype,
        in_session=bool(flags & _IN_SESSION),
        flags=flags,
    )


def _decode_snapshot(
    encoded: bytes,
    metadata: InterchangeMetadata,
    ordering: _OrderingState,
    record_index: int,
) -> SnapshotRecord:
    (
        message_index,
        timestamp_ns,
        symbol_id,
        kind_code,
        flags,
        trigger_price,
        trigger_quantity,
        last_trade_price,
        reserved,
        last_trade_quantity,
    ) = _SNAPSHOT.unpack_from(encoded)
    kind = _EVENT_KIND_BY_CODE.get(kind_code)
    state_code = flags >> 3 & 0x07
    trading_state = _TRADING_STATE_BY_CODE.get(state_code)
    if kind is None or trading_state is None or flags & 0x80 or reserved != b"\0" * 4:
        raise _fail(
            ErrorCode.INVARIANT,
            "Snapshot kind, flags, state, or reserved prefix is invalid.",
            record_index=record_index,
        )

    _validate_ordering(
        message_index,
        timestamp_ns,
        symbol_id,
        metadata,
        ordering,
        record_index,
    )
    if (
        (flags & _SNAPSHOT_TRIGGER_PRICE == 0 and trigger_price != 0)
        or (flags & _SNAPSHOT_TRIGGER_QUANTITY == 0 and trigger_quantity != 0)
        or (flags & _SNAPSHOT_TRIGGER_QUANTITY and trigger_quantity == 0)
        or (
            flags & _SNAPSHOT_LAST_TRADE == 0
            and (last_trade_price != 0 or last_trade_quantity != 0)
        )
        or (flags & _SNAPSHOT_LAST_TRADE and last_trade_quantity == 0)
    ):
        raise _fail(
            ErrorCode.INVARIANT,
            "Snapshot nullable prefix fields are not canonical.",
            record_index=record_index,
        )

    trigger_fields = flags & (_SNAPSHOT_TRIGGER_PRICE | _SNAPSHOT_TRIGGER_QUANTITY)
    both_trigger_fields = _SNAPSHOT_TRIGGER_PRICE | _SNAPSHOT_TRIGGER_QUANTITY
    top_changed = bool(flags & _SNAPSHOT_TOP_CHANGED)
    book_mutation = kind in {
        EventKind.ADD,
        EventKind.EXECUTE,
        EventKind.EXECUTE_PRICE,
        EventKind.CANCEL,
        EventKind.DELETE,
        EventKind.REPLACE,
    }
    unchanged_trade = kind in {EventKind.TRADE, EventKind.CROSS}
    state_change = kind is EventKind.TRADING_STATE
    if (
        (book_mutation and (trigger_fields != both_trigger_fields or not top_changed))
        or (unchanged_trade and (trigger_fields != both_trigger_fields or top_changed))
        or (state_change and (trigger_fields != 0 or top_changed))
        or kind is EventKind.BROKEN_TRADE
    ):
        raise _fail(
            ErrorCode.INVARIANT,
            "Snapshot trigger fields or top-change flag disagree with event kind.",
            record_index=record_index,
        )

    levels: list[SnapshotDepthLevel] = []
    bid_gap = False
    ask_gap = False
    previous_bid: int | None = None
    previous_ask: int | None = None
    for level_index in range(metadata.depth):
        offset = _SNAPSHOT_FIXED_SIZE + level_index * _SNAPSHOT_DEPTH_SIZE
        (
            bid_valid,
            ask_valid,
            level_reserved,
            bid_price,
            bid_quantity,
            ask_price,
            ask_quantity,
        ) = _DEPTH_LEVEL.unpack_from(encoded, offset)
        if bid_valid > 1 or ask_valid > 1 or level_reserved != b"\0" * 2:
            raise _fail(
                ErrorCode.INVARIANT,
                "Snapshot depth validity or reserved bytes are invalid.",
                record_index=record_index,
            )
        if (
            (bid_valid == 0 and (bid_price != 0 or bid_quantity != 0))
            or (ask_valid == 0 and (ask_price != 0 or ask_quantity != 0))
            or (
                bid_valid == 1
                and (
                    bid_quantity == 0
                    or bid_gap
                    or (previous_bid is not None and bid_price >= previous_bid)
                )
            )
            or (
                ask_valid == 1
                and (
                    ask_quantity == 0
                    or ask_gap
                    or (previous_ask is not None and ask_price <= previous_ask)
                )
            )
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Snapshot depth values are non-canonical or not best-to-worst.",
                record_index=record_index,
            )
        if bid_valid:
            previous_bid = bid_price
        else:
            bid_gap = True
        if ask_valid:
            previous_ask = ask_price
        else:
            ask_gap = True
        levels.append(
            SnapshotDepthLevel(
                bid_price4=bid_price if bid_valid else None,
                bid_quantity=bid_quantity if bid_valid else None,
                ask_price4=ask_price if ask_valid else None,
                ask_quantity=ask_quantity if ask_valid else None,
            )
        )

    ordering.message_index = message_index
    ordering.timestamp_ns = timestamp_ns
    return SnapshotRecord(
        trading_date=metadata.trading_date,
        message_index=message_index,
        timestamp_ns=timestamp_ns,
        symbol_id=symbol_id,
        event_kind=kind,
        event_price4=trigger_price if flags & _SNAPSHOT_TRIGGER_PRICE else None,
        event_quantity=trigger_quantity if flags & _SNAPSHOT_TRIGGER_QUANTITY else None,
        last_trade_price4=last_trade_price if flags & _SNAPSHOT_LAST_TRADE else None,
        last_trade_quantity=last_trade_quantity if flags & _SNAPSHOT_LAST_TRADE else None,
        top_n_changed=top_changed,
        trading_state=trading_state,
        levels=tuple(levels),
        flags=flags,
    )


def _validated_stream(
    path: Path,
    expected_kind: InterchangeKind,
    expected_sha256: str,
) -> Iterator[tuple[BinaryIO, InterchangeMetadata, _FileIdentity]]:
    if not path.name or any(component.endswith(".partial") for component in path.parts):
        raise _fail(
            ErrorCode.PARTIAL_ARTEFACT,
            "A partial interchange pathname is not a completed reader target.",
        )
    try:
        target_status = path.stat()
    except OSError as error:
        raise _fail(
            ErrorCode.INPUT_PATH,
            "Interchange target is not a readable regular file.",
        ) from error
    if not stat.S_ISREG(target_status.st_mode):
        raise _fail(ErrorCode.INPUT_PATH, "Interchange target is not a regular file.")
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(
            ErrorCode.INPUT_PATH,
            "Interchange target is not a readable regular file.",
        ) from error
    with stream:
        try:
            file_status = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(
                ErrorCode.INPUT_PATH, "Interchange target metadata is unavailable."
            ) from error
        if not stat.S_ISREG(file_status.st_mode):
            raise _fail(ErrorCode.INPUT_PATH, "Interchange target is not a regular file.")
        file_identity = _identity(file_status)
        metadata, records_offset = _parse_metadata(
            stream,
            file_status.st_size,
            expected_kind,
            expected_sha256,
        )
        _authenticate(stream, expected_sha256, file_identity, records_offset)
        yield stream, metadata, file_identity


def read_events(
    path: Path,
    *,
    expected_sha256: str,
    chunk_records: int,
) -> Iterator[EventBatch]:
    """Yield authenticated, validated event-v1 records in bounded source-order batches."""
    _validate_reader_arguments(expected_sha256, chunk_records)
    for stream, metadata, file_identity in _validated_stream(
        path,
        InterchangeKind.EVENTS,
        expected_sha256,
    ):
        ordering = _OrderingState()
        record_index = 0
        while record_index < metadata.record_count:
            batch_count = _bounded_batch_count(
                metadata,
                chunk_records,
                metadata.record_count - record_index,
            )
            encoded = _read_exact(
                stream,
                batch_count * metadata.record_size,
                "Event record region ended before the declared record count.",
            )
            records = tuple(
                _decode_event(
                    encoded[offset : offset + metadata.record_size],
                    metadata,
                    ordering,
                    record_index + batch_index,
                )
                for batch_index, offset in enumerate(range(0, len(encoded), metadata.record_size))
            )
            record_index += batch_count
            _ensure_unchanged(stream, file_identity)
            yield EventBatch(metadata, records)


def read_event_metadata(path: Path, *, expected_sha256: str) -> InterchangeMetadata:
    """Authenticate an event-v1 artefact and return its validated fixed metadata."""
    _validate_reader_arguments(expected_sha256, 1)
    for _stream, metadata, _identity_value in _validated_stream(
        path,
        InterchangeKind.EVENTS,
        expected_sha256,
    ):
        return metadata
    raise _fail(ErrorCode.INTERNAL, "Event metadata validation did not open its artefact.")


def read_snapshots(
    path: Path,
    *,
    expected_sha256: str,
    chunk_records: int,
) -> Iterator[SnapshotBatch]:
    """Yield authenticated, validated snapshot-v1 records in bounded source-order batches."""
    _validate_reader_arguments(expected_sha256, chunk_records)
    for stream, metadata, file_identity in _validated_stream(
        path,
        InterchangeKind.SNAPSHOTS,
        expected_sha256,
    ):
        ordering = _OrderingState()
        record_index = 0
        while record_index < metadata.record_count:
            batch_count = _bounded_batch_count(
                metadata,
                chunk_records,
                metadata.record_count - record_index,
            )
            encoded = _read_exact(
                stream,
                batch_count * metadata.record_size,
                "Snapshot record region ended before the declared record count.",
            )
            records = tuple(
                _decode_snapshot(
                    encoded[offset : offset + metadata.record_size],
                    metadata,
                    ordering,
                    record_index + batch_index,
                )
                for batch_index, offset in enumerate(range(0, len(encoded), metadata.record_size))
            )
            record_index += batch_count
            _ensure_unchanged(stream, file_identity)
            yield SnapshotBatch(metadata, records)


def read_snapshot_metadata(path: Path, *, expected_sha256: str) -> InterchangeMetadata:
    """Authenticate a snapshot-v1 artefact and return its validated fixed metadata."""
    _validate_reader_arguments(expected_sha256, 1)
    for _stream, metadata, _identity_value in _validated_stream(
        path,
        InterchangeKind.SNAPSHOTS,
        expected_sha256,
    ):
        return metadata
    raise _fail(ErrorCode.INTERNAL, "Snapshot metadata validation did not open its artefact.")


__all__ = [
    "read_event_metadata",
    "read_events",
    "read_snapshot_metadata",
    "read_snapshots",
]
