"""Authenticated bounded conversion from interchange-v1 to partitioned Parquet."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, TypeAlias, cast
from urllib.parse import quote, unquote

import pyarrow as pa
import pyarrow.parquet as pq
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from itchlab_research import __version__
from itchlab_research.canonical_json import (
    canonical_json_bytes,
    config_document,
    config_hashes,
)
from itchlab_research.config import ConversionConfig, ReplayConfig, parse_config
from itchlab_research.conversion.models import ConversionProgress, ConversionResult
from itchlab_research.errors import (
    ConfigValidationError,
    ConversionError,
    ErrorCode,
    InterchangeReadError,
)
from itchlab_research.interchange import (
    EventBatch,
    EventRecord,
    InterchangeMetadata,
    SnapshotBatch,
    SnapshotRecord,
    read_event_metadata,
    read_events,
    read_snapshot_metadata,
    read_snapshots,
)

_MANIFEST_NAME: Final = "conversion-manifest.json"
_IDENTITY_MARKER: Final = "identity.sha256"
_MAX_MANIFEST_BYTES: Final = 4 << 20
_HASH_CHUNK_BYTES: Final = 1 << 20
_READ_CHUNK_RECORDS: Final = 1_048_576
_MAX_OPEN_FILES: Final = 32
_SCHEMA_VERSION: Final = 1
_PARTITION_KEYS: Final = ("trading_date", "symbol")
_SORT_KEYS: Final = ("message_index",)

CancelCheck: TypeAlias = Callable[[], bool]
ProgressCallback: TypeAlias = Callable[[ConversionProgress], None]
_FileIdentity: TypeAlias = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _ChildArtefact:
    path: Path
    sha256: str
    size_bytes: int
    record_count: int
    record_size: int
    depth: int


@dataclass(frozen=True, slots=True)
class _ParentReplay:
    locator: str
    manifest_path: Path
    manifest_sha256: str
    manifest_identity: _FileIdentity
    replay_id: str
    status: str
    trading_date: date
    config_sha256: str
    identity_sha256: str
    source_sha256: str
    events: _ChildArtefact
    snapshots: _ChildArtefact
    event_metadata: InterchangeMetadata
    snapshot_metadata: InterchangeMetadata


@dataclass(frozen=True, slots=True)
class _ParquetArtefact:
    kind: str
    path: str
    sha256: str
    size_bytes: int
    row_count: int
    trading_date: str
    symbol: str


@dataclass(frozen=True, slots=True)
class _RunPaths:
    output_root: Path
    conversion_root: Path
    lock_path: Path
    staging_directory: Path
    final_directory: Path


def _fail(code: ErrorCode, message: str, *, partial_exists: bool = False) -> ConversionError:
    return ConversionError(code, message, partial_exists=partial_exists)


def _identity(status_result: os.stat_result) -> _FileIdentity:
    return (
        status_result.st_dev,
        status_result.st_ino,
        status_result.st_size,
        status_result.st_mtime_ns,
        status_result.st_ctime_ns,
    )


def _check_cancel(cancel_requested: CancelCheck) -> None:
    if cancel_requested():
        raise _fail(ErrorCode.CANCELLED, "Conversion was cancelled at a complete batch boundary.")


def _path_has_symlink(path: Path) -> bool:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            status_result = current.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "A path component could not be inspected.") from error
        if stat.S_ISLNK(status_result.st_mode):
            return True
    return False


def _safe_relative_path(value: str) -> bool:
    normalised = value.replace("\\", "/")
    path = Path(normalised)
    segments = normalised.split("/")
    return bool(
        normalised
        and not normalised.startswith("/")
        and not (len(normalised) >= 2 and normalised[1] == ":")
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} and not part.endswith(".partial") for part in segments)
    )


def _read_regular_file(path: Path, maximum_bytes: int) -> tuple[bytes, _FileIdentity]:
    if any(component.endswith(".partial") for component in path.parts) or _path_has_symlink(path):
        raise _fail(ErrorCode.PARTIAL_ARTEFACT, "A partial or symlinked manifest is not accepted.")
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A required manifest is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "Manifest metadata is unavailable.") from error
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise _fail(ErrorCode.INPUT_PATH, "Manifest is not a bounded regular file.")
        try:
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "Manifest could not be read safely.") from error
        if (
            len(content) != before.st_size
            or len(content) > maximum_bytes
            or _identity(before) != _identity(after)
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Manifest changed or exceeded its size bound.")
        return content, _identity(after)


def _reject_duplicate_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _strict_json(content: bytes) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        text = content.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_names,
            parse_constant=reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Manifest is not strict JSON/I-JSON.") from error
    if not isinstance(document, dict):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Manifest root is not an object.")
    return cast(dict[str, Any], document)


@lru_cache(maxsize=2)
def _manifest_validator(kind: str) -> Draft202012Validator:
    schema_names = ["replay-config.schema.json", "replay-manifest.schema.json"]
    if kind == "conversion":
        schema_names = ["conversion-config.schema.json", "conversion-manifest.schema.json"]
    resources: list[tuple[str, Resource[Any]]] = []
    documents: dict[str, dict[str, Any]] = {}
    for name in schema_names:
        resource = files("itchlab_research._schemas").joinpath(name)
        document = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
        documents[name] = document
        resources.append((cast(str, document["$id"]), Resource.from_contents(document)))
    registry = Registry().with_resources(resources)
    root_name = (
        "replay-manifest.schema.json" if kind == "replay" else "conversion-manifest.schema.json"
    )
    return Draft202012Validator(
        documents[root_name],
        registry=registry,
        format_checker=FormatChecker(),
    )


def _validate_manifest_document(document: Mapping[str, Any], kind: str) -> None:
    errors = list(_manifest_validator(kind).iter_errors(document))
    if errors:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} manifest violates schema version 1."
        )


def _sha256_file(path: Path, cancel_requested: CancelCheck) -> tuple[str, int]:
    if _path_has_symlink(path):
        raise _fail(ErrorCode.INPUT_PATH, "A symlinked artefact is not accepted.")
    digest = hashlib.sha256()
    observed = 0
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A required artefact is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "Artefact metadata is unavailable.") from error
        if not stat.S_ISREG(before.st_mode):
            raise _fail(ErrorCode.INPUT_PATH, "A required artefact is not a regular file.")
        try:
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                _check_cancel(cancel_requested)
                digest.update(chunk)
                observed += len(chunk)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(
                ErrorCode.HASH_MISMATCH, "Artefact changed or failed while hashing."
            ) from error
        if observed != before.st_size or _identity(before) != _identity(after):
            raise _fail(ErrorCode.HASH_MISMATCH, "Artefact changed or failed while hashing.")
    return digest.hexdigest(), observed


def _stage_identity(
    domain: bytes,
    parent_hashes: Sequence[str],
    identity_config_sha256: str,
    tool_sha256: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(b"\0")
    for value in parent_hashes:
        digest.update(bytes.fromhex(value))
    digest.update(bytes.fromhex(identity_config_sha256))
    digest.update(bytes.fromhex(tool_sha256))
    digest.update(_SCHEMA_VERSION.to_bytes(2, "big"))
    return digest.hexdigest()


def _package_content_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    allowed_suffixes = {".py", ".json"}
    package_files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix in allowed_suffixes or path.name in {"py.typed"})
    )
    digest = hashlib.sha256(b"itchlab-python-package-content-v1\0")
    for path in package_files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _parse_child(directory: Path, entry: Mapping[str, Any]) -> _ChildArtefact:
    relative = cast(str, entry["path"])
    if not _safe_relative_path(relative) or Path(relative).name != relative:
        raise _fail(ErrorCode.INPUT_PATH, "Replay manifest names an unsafe child artefact.")
    path = directory / relative
    if _path_has_symlink(path):
        raise _fail(ErrorCode.INPUT_PATH, "Replay manifest names a symlinked child artefact.")
    return _ChildArtefact(
        path=path,
        sha256=cast(str, entry["sha256"]),
        size_bytes=cast(int, entry["size_bytes"]),
        record_count=cast(int, entry["record_count"]),
        record_size=cast(int, entry["record_size"]),
        depth=cast(int, entry.get("depth", 0)),
    )


def _metadata_matches_child(
    metadata: InterchangeMetadata,
    child: _ChildArtefact,
    document: Mapping[str, Any],
) -> bool:
    return (
        metadata.file_sha256 == child.sha256
        and metadata.record_count == child.record_count
        and metadata.record_size == child.record_size
        and metadata.depth == child.depth
        and metadata.config_sha256 == document["config_sha256"]
        and metadata.source_sha256 == document["source"]["sha256"]
        and metadata.trading_date.isoformat() == document["source"]["trading_date"]
        and metadata.degraded == (document["status"] == "degraded")
    )


def _load_parent(locator: str, base_directory: Path) -> _ParentReplay:
    path = base_directory / locator
    content, manifest_identity = _read_regular_file(path, _MAX_MANIFEST_BYTES)
    document = _strict_json(content)
    _validate_manifest_document(document, "replay")

    try:
        replay_config = cast(ReplayConfig, parse_config(json.dumps(document["config"]), "replay"))
    except ConfigValidationError as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, "Replay manifest config is semantically invalid."
        ) from error
    hashes = config_hashes(replay_config)
    if (
        hashes.config_sha256 != document["config_sha256"]
        or hashes.identity_config_sha256 != document["identity_config_sha256"]
        or replay_config.input.sha256 != document["source"]["sha256"]
        or replay_config.input.trading_date != document["source"]["trading_date"]
    ):
        raise _fail(
            ErrorCode.HASH_MISMATCH, "Replay manifest configuration lineage is inconsistent."
        )
    expected_identity = _stage_identity(
        b"itchlab-replay-v1",
        [cast(str, document["source"]["sha256"])],
        cast(str, document["identity_config_sha256"]),
        cast(str, document["executable_sha256"]),
    )
    if expected_identity != document["identity_sha256"]:
        raise _fail(ErrorCode.HASH_MISMATCH, "Replay manifest identity is inconsistent.")

    directory = path.parent
    events = _parse_child(directory, cast(Mapping[str, Any], document["artefacts"][0]))
    snapshots = _parse_child(directory, cast(Mapping[str, Any], document["artefacts"][1]))
    try:
        event_metadata = read_event_metadata(events.path, expected_sha256=events.sha256)
        snapshot_metadata = read_snapshot_metadata(snapshots.path, expected_sha256=snapshots.sha256)
    except InterchangeReadError as error:
        raise _fail(error.code, error.message) from error
    if not _metadata_matches_child(event_metadata, events, document) or not _metadata_matches_child(
        snapshot_metadata, snapshots, document
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Replay child metadata disagrees with its manifest.")
    if event_metadata.symbols != snapshot_metadata.symbols:
        raise _fail(ErrorCode.INVARIANT, "Replay child symbol dictionaries disagree.")

    instruments = cast(list[dict[str, Any]], document["instruments"])
    manifest_symbols = tuple(
        (item["symbol_id"], item["stock_locate"], item["symbol"], item["round_lot_size"])
        for item in instruments
    )
    binary_symbols = tuple(
        (item.symbol_id, item.stock_locate, item.symbol, item.round_lot_size)
        for item in event_metadata.symbols
    )
    if manifest_symbols != binary_symbols:
        raise _fail(ErrorCode.INVARIANT, "Replay manifest and binary symbol dictionaries disagree.")

    return _ParentReplay(
        locator=locator,
        manifest_path=path,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
        manifest_identity=manifest_identity,
        replay_id=cast(str, document["replay_id"]),
        status=cast(str, document["status"]),
        trading_date=event_metadata.trading_date,
        config_sha256=event_metadata.config_sha256,
        identity_sha256=cast(str, document["identity_sha256"]),
        source_sha256=event_metadata.source_sha256,
        events=events,
        snapshots=snapshots,
        event_metadata=event_metadata,
        snapshot_metadata=snapshot_metadata,
    )


def _validate_parent_set(parents: Sequence[_ParentReplay], allow_degraded: bool) -> None:
    replay_ids = {parent.replay_id for parent in parents}
    trading_dates = {parent.trading_date for parent in parents}
    if len(replay_ids) != len(parents) or len(trading_dates) != len(parents):
        raise _fail(ErrorCode.INVARIANT, "Conversion parents repeat a replay ID or trading date.")
    depths = {parent.snapshot_metadata.depth for parent in parents}
    if len(depths) != 1:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion parents use different snapshot depths.")
    if not allow_degraded and any(parent.status == "degraded" for parent in parents):
        raise _fail(
            ErrorCode.INVARIANT, "A degraded replay requires the explicit allow-degraded option."
        )


def _validate_conversion_config(config: ConversionConfig) -> None:
    if isinstance(config.schema_version, bool) or config.schema_version != _SCHEMA_VERSION:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Conversion config schema version is unsupported.")
    if (
        not config.replay_manifests
        or any(not isinstance(locator, str) for locator in config.replay_manifests)
        or len(set(config.replay_manifests)) != len(config.replay_manifests)
    ):
        raise _fail(
            ErrorCode.CONFIG_SCHEMA, "Conversion replay manifests must be non-empty and unique."
        )
    if any(not _safe_relative_path(locator) for locator in config.replay_manifests):
        raise _fail(ErrorCode.INPUT_PATH, "Conversion parent locators must be safe relative paths.")
    if not isinstance(config.output_root, str) or not _safe_relative_path(config.output_root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Conversion output root must be a safe relative path.")
    if config.parquet.compression != "zstd":
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Conversion Parquet compression is unsupported.")
    if (
        isinstance(config.parquet.row_group_size, bool)
        or not isinstance(config.parquet.row_group_size, int)
        or not 1 <= config.parquet.row_group_size <= 1_048_576
    ):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Conversion Parquet row-group size is invalid.")
    if tuple(config.parquet.partition_keys) != _PARTITION_KEYS:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Conversion partition keys are unsupported.")


def event_schema() -> pa.Schema:
    """Return the exact logical event Parquet schema version 1."""
    return pa.schema(
        cast(
            Any,
            [
                pa.field("trading_date", pa.date32(), nullable=False),
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("message_index", pa.uint64(), nullable=False),
                pa.field("timestamp_ns", pa.uint64(), nullable=False),
                pa.field("symbol_id", pa.uint16(), nullable=False),
                pa.field("event_kind", pa.string(), nullable=False),
                pa.field("source_type", pa.string(), nullable=False),
                pa.field("primary_reference", pa.uint64(), nullable=True),
                pa.field("secondary_reference", pa.uint64(), nullable=True),
                pa.field("side", pa.int8(), nullable=True),
                pa.field("price4", pa.uint32(), nullable=True),
                pa.field("quantity", pa.uint64(), nullable=True),
                pa.field("remaining_quantity", pa.uint64(), nullable=True),
                pa.field("execution_price4", pa.uint32(), nullable=True),
                pa.field("aux_code", pa.string(), nullable=True),
                pa.field("event_subtype", pa.string(), nullable=True),
                pa.field("in_session", pa.bool_(), nullable=False),
                pa.field("flags", pa.uint16(), nullable=False),
            ],
        )
    )


def snapshot_schema(depth: int) -> pa.Schema:
    """Return the exact logical snapshot Parquet schema version 1 for one depth."""
    fields = cast(
        list[Any],
        [
            pa.field("trading_date", pa.date32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("message_index", pa.uint64(), nullable=False),
            pa.field("timestamp_ns", pa.uint64(), nullable=False),
            pa.field("symbol_id", pa.uint16(), nullable=False),
            pa.field("event_kind", pa.string(), nullable=False),
            pa.field("event_price4", pa.uint32(), nullable=True),
            pa.field("event_quantity", pa.uint64(), nullable=True),
            pa.field("last_trade_price4", pa.uint32(), nullable=True),
            pa.field("last_trade_quantity", pa.uint64(), nullable=True),
            pa.field("top_n_changed", pa.bool_(), nullable=False),
            pa.field("trading_state", pa.string(), nullable=False),
            pa.field("flags", pa.uint8(), nullable=False),
        ],
    )
    for index in range(1, depth + 1):
        fields.extend(
            [
                pa.field(f"bid_price4_{index}", pa.uint32(), nullable=True),
                pa.field(f"bid_quantity_{index}", pa.uint64(), nullable=True),
                pa.field(f"ask_price4_{index}", pa.uint32(), nullable=True),
                pa.field(f"ask_quantity_{index}", pa.uint64(), nullable=True),
            ]
        )
    return pa.schema(fields)


def _schema_descriptor(schema: pa.Schema) -> dict[str, Any]:
    fields_value = [
        {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
    return {
        "fields": fields_value,
        "sha256": hashlib.sha256(canonical_json_bytes(fields_value)).hexdigest(),
    }


def _group_events(batch: EventBatch) -> Iterator[tuple[str, list[EventRecord]]]:
    names = {item.symbol_id: item.symbol for item in batch.metadata.symbols}
    grouped: dict[int, list[EventRecord]] = defaultdict(list)
    for record in batch.records:
        grouped[record.symbol_id].append(record)
    for symbol_id in sorted(grouped):
        yield names[symbol_id], grouped[symbol_id]


def _event_record_batch(
    schema: pa.Schema, symbol: str, records: Sequence[EventRecord]
) -> pa.RecordBatch:
    data: dict[str, list[Any]] = {field.name: [] for field in schema}
    for record in records:
        data["trading_date"].append(record.trading_date)
        data["symbol"].append(symbol)
        data["message_index"].append(record.message_index)
        data["timestamp_ns"].append(record.timestamp_ns)
        data["symbol_id"].append(record.symbol_id)
        data["event_kind"].append(record.event_kind.value)
        data["source_type"].append(record.source_type)
        data["primary_reference"].append(record.primary_reference)
        data["secondary_reference"].append(record.secondary_reference)
        data["side"].append(record.side)
        data["price4"].append(record.price4)
        data["quantity"].append(record.quantity)
        data["remaining_quantity"].append(record.remaining_quantity)
        data["execution_price4"].append(record.execution_price4)
        data["aux_code"].append(record.aux_code)
        data["event_subtype"].append(record.event_subtype)
        data["in_session"].append(record.in_session)
        data["flags"].append(record.flags)
    return pa.RecordBatch.from_pydict(data, schema=schema)


def _group_snapshots(batch: SnapshotBatch) -> Iterator[tuple[str, list[SnapshotRecord]]]:
    names = {item.symbol_id: item.symbol for item in batch.metadata.symbols}
    grouped: dict[int, list[SnapshotRecord]] = defaultdict(list)
    for record in batch.records:
        grouped[record.symbol_id].append(record)
    for symbol_id in sorted(grouped):
        yield names[symbol_id], grouped[symbol_id]


def _snapshot_record_batch(
    schema: pa.Schema,
    symbol: str,
    records: Sequence[SnapshotRecord],
) -> pa.RecordBatch:
    data: dict[str, list[Any]] = {field.name: [] for field in schema}
    for record in records:
        data["trading_date"].append(record.trading_date)
        data["symbol"].append(symbol)
        data["message_index"].append(record.message_index)
        data["timestamp_ns"].append(record.timestamp_ns)
        data["symbol_id"].append(record.symbol_id)
        data["event_kind"].append(record.event_kind.value)
        data["event_price4"].append(record.event_price4)
        data["event_quantity"].append(record.event_quantity)
        data["last_trade_price4"].append(record.last_trade_price4)
        data["last_trade_quantity"].append(record.last_trade_quantity)
        data["top_n_changed"].append(record.top_n_changed)
        data["trading_state"].append(record.trading_state.value)
        data["flags"].append(record.flags)
        for index, level in enumerate(record.levels, start=1):
            data[f"bid_price4_{index}"].append(level.bid_price4)
            data[f"bid_quantity_{index}"].append(level.bid_quantity)
            data[f"ask_price4_{index}"].append(level.ask_price4)
            data[f"ask_quantity_{index}"].append(level.ask_quantity)
    return pa.RecordBatch.from_pydict(data, schema=schema)


def _event_batches(
    parents: Sequence[_ParentReplay],
    schema: pa.Schema,
    cancel_requested: CancelCheck,
    progress: ProgressCallback | None,
) -> Iterator[pa.RecordBatch]:
    observed = 0
    for parent in parents:
        try:
            batches = read_events(
                parent.events.path,
                expected_sha256=parent.events.sha256,
                chunk_records=_READ_CHUNK_RECORDS,
            )
            for batch in batches:
                _check_cancel(cancel_requested)
                for symbol, records in _group_events(batch):
                    yield _event_record_batch(schema, symbol, records)
                observed += len(batch)
                if progress is not None:
                    progress(ConversionProgress("events", observed, 0, 0))
        except InterchangeReadError as error:
            raise _fail(error.code, error.message, partial_exists=True) from error


def _snapshot_batches(
    parents: Sequence[_ParentReplay],
    schema: pa.Schema,
    cancel_requested: CancelCheck,
    progress: ProgressCallback | None,
) -> Iterator[pa.RecordBatch]:
    observed = 0
    for parent in parents:
        try:
            batches = read_snapshots(
                parent.snapshots.path,
                expected_sha256=parent.snapshots.sha256,
                chunk_records=_READ_CHUNK_RECORDS,
            )
            for batch in batches:
                _check_cancel(cancel_requested)
                for symbol, records in _group_snapshots(batch):
                    yield _snapshot_record_batch(schema, symbol, records)
                observed += len(batch)
                if progress is not None:
                    progress(ConversionProgress("snapshots", observed, 0, 0))
        except InterchangeReadError as error:
            raise _fail(error.code, error.message, partial_exists=True) from error


def _write_dataset(
    batches: Iterator[pa.RecordBatch],
    schema: pa.Schema,
    destination: Path,
    row_group_size: int,
) -> None:
    physical_schema = _file_schema(schema)
    physical_indices = [schema.get_field_index(field.name) for field in physical_schema]
    date_index = schema.get_field_index("trading_date")
    symbol_index = schema.get_field_index("symbol")
    writers: OrderedDict[tuple[str, str], pq.ParquetWriter] = OrderedDict()
    next_part: defaultdict[tuple[str, str], int] = defaultdict(int)

    def close_writers() -> BaseException | None:
        first_error: BaseException | None = None
        while writers:
            _, writer = writers.popitem(last=False)
            try:
                writer.close()
            except (pa.ArrowException, OSError) as error:
                if first_error is None:
                    first_error = error
        return first_error

    try:
        destination.mkdir(parents=True, exist_ok=False)
        for batch in batches:
            trading_dates = set(cast(list[object], batch.column(date_index).to_pylist()))
            symbols = set(cast(list[object], batch.column(symbol_index).to_pylist()))
            if len(trading_dates) != 1 or len(symbols) != 1:
                raise _fail(
                    ErrorCode.INVARIANT,
                    "A conversion batch crossed a Parquet partition boundary.",
                    partial_exists=True,
                )
            trading_date_value = trading_dates.pop()
            symbol_value = symbols.pop()
            if not isinstance(trading_date_value, date) or not isinstance(symbol_value, str):
                raise _fail(
                    ErrorCode.INVARIANT,
                    "A conversion batch contained invalid partition values.",
                    partial_exists=True,
                )
            partition = (trading_date_value.isoformat(), symbol_value)
            writer = writers.pop(partition, None)
            if writer is None:
                if len(writers) >= _MAX_OPEN_FILES:
                    _, evicted_writer = writers.popitem(last=False)
                    evicted_writer.close()
                trading_date, symbol = partition
                partition_directory = (
                    destination
                    / f"trading_date={quote(trading_date, safe='')}"
                    / f"symbol={quote(symbol, safe='')}"
                )
                partition_directory.mkdir(parents=True, exist_ok=True)
                part_number = next_part[partition]
                next_part[partition] += 1
                writer = pq.ParquetWriter(
                    partition_directory / f"part-{part_number}.parquet",
                    physical_schema,
                    compression="zstd",
                )
            writers[partition] = writer
            physical_batch = pa.RecordBatch.from_arrays(
                [batch.column(index) for index in physical_indices],
                schema=physical_schema,
            )
            writer.write_batch(physical_batch, row_group_size=row_group_size)
    except ConversionError:
        close_writers()
        raise
    except (pa.ArrowException, OSError) as error:
        close_writers()
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Parquet dataset could not be written and closed.",
            partial_exists=True,
        ) from error
    close_error = close_writers()
    if close_error is not None:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Parquet dataset could not be written and closed.",
            partial_exists=True,
        ) from close_error


def _file_schema(logical_schema: pa.Schema) -> pa.Schema:
    return pa.schema([field for field in logical_schema if field.name not in _PARTITION_KEYS])


def _partition_values(kind: str, root: Path, path: Path) -> tuple[str, str, str]:
    relative = path.relative_to(root)
    parts = relative.parts
    if (
        len(parts) != 4
        or parts[0] != kind
        or not parts[1].startswith("trading_date=")
        or not parts[2].startswith("symbol=")
        or not parts[3].startswith("part-")
        or not parts[3].endswith(".parquet")
    ):
        raise _fail(ErrorCode.OUTPUT_PATH, "Parquet writer produced an unexpected partition path.")
    trading_date = unquote(parts[1].partition("=")[2])
    encoded_symbol = parts[2].partition("=")[2]
    symbol = unquote(encoded_symbol)
    if quote(symbol, safe="") != encoded_symbol:
        raise _fail(ErrorCode.OUTPUT_PATH, "Parquet partition symbol encoding is not canonical.")
    try:
        date.fromisoformat(trading_date)
    except ValueError as error:
        raise _fail(ErrorCode.INVARIANT, "Parquet partition date is invalid.") from error
    return relative.as_posix(), trading_date, symbol


def _validate_sorted_indices(path: Path, previous: int | None) -> int | None:
    parquet_file = pq.ParquetFile(path)
    for batch in parquet_file.iter_batches(batch_size=65_536, columns=["message_index"]):
        values = cast(list[int], batch.column(0).to_pylist())
        for value in values:
            if previous is not None and value <= previous:
                raise _fail(ErrorCode.INVARIANT, "Parquet partition sort order is invalid.")
            previous = value
    return previous


def _validate_parquet_outputs(
    staging: Path,
    schemas: Mapping[str, pa.Schema],
    cancel_requested: CancelCheck,
) -> tuple[list[_ParquetArtefact], list[dict[str, Any]]]:
    artefacts: list[_ParquetArtefact] = []
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"events": 0, "snapshots": 0}
    )
    last_by_partition: dict[tuple[str, str, str], int | None] = {}
    for kind in ("events", "snapshots"):
        expected_schema = _file_schema(schemas[kind])
        kind_root = staging / kind
        paths = (
            sorted(
                kind_root.rglob("*.parquet"),
                key=lambda path: (
                    path.parent.as_posix(),
                    int(path.stem.partition("-")[2]),
                ),
            )
            if kind_root.exists()
            else []
        )
        for path in paths:
            _check_cancel(cancel_requested)
            relative, trading_date, symbol = _partition_values(kind, staging, path)
            if pq.read_schema(path) != expected_schema:
                raise _fail(ErrorCode.SCHEMA_VERSION, "Parquet file schema is inconsistent.")
            metadata = pq.ParquetFile(path).metadata
            row_count = metadata.num_rows
            key = (kind, trading_date, symbol)
            last_by_partition[key] = _validate_sorted_indices(path, last_by_partition.get(key))
            digest, size = _sha256_file(path, cancel_requested)
            artefacts.append(
                _ParquetArtefact(
                    kind=kind,
                    path=relative,
                    sha256=digest,
                    size_bytes=size,
                    row_count=row_count,
                    trading_date=trading_date,
                    symbol=symbol,
                )
            )
            counts[(trading_date, symbol)][kind] += row_count
    count_rows = [
        {
            "trading_date": trading_date,
            "symbol": symbol,
            "events": values["events"],
            "snapshots": values["snapshots"],
        }
        for (trading_date, symbol), values in sorted(counts.items())
    ]
    return artefacts, count_rows


def _inspect_parquet_outputs(
    root: Path,
    schemas: Mapping[str, pa.Schema],
    cancel_requested: CancelCheck,
    *,
    existing: bool,
) -> tuple[list[_ParquetArtefact], list[dict[str, Any]]]:
    try:
        return _validate_parquet_outputs(root, schemas, cancel_requested)
    except ConversionError as error:
        if existing and error.code is not ErrorCode.CANCELLED:
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "Existing conversion Parquet validation failed.",
            ) from error
        raise
    except (pa.ArrowException, OSError, ValueError) as error:
        code = ErrorCode.HASH_MISMATCH if existing else ErrorCode.DISK_WRITE
        message = (
            "Existing conversion Parquet validation failed."
            if existing
            else "Written Parquet output could not be validated."
        )
        raise _fail(code, message, partial_exists=not existing) from error


def _timestamp(value_ns: int) -> str:
    seconds, nanoseconds = divmod(value_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{prefix}.{nanoseconds:09d}Z"


def _run_id(identity_sha256: str, now_ns: int) -> str:
    seconds, nanoseconds = divmod(now_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}.{nanoseconds:09d}Z-{identity_sha256[:12]}"


def _remove_lock(path: Path) -> None:
    try:
        path.rmdir()
    except OSError as error:
        raise _fail(
            ErrorCode.DISK_WRITE, "Conversion identity lock could not be removed."
        ) from error


def _identity_marker_matches(path: Path, identity_sha256: str) -> bool:
    if _path_has_symlink(path):
        return False
    try:
        stream = path.open("rb")
    except OSError:
        return False
    with stream:
        try:
            before = os.fstat(stream.fileno())
            content = stream.read(66)
            after = os.fstat(stream.fileno())
        except OSError:
            return False
    return (
        stat.S_ISREG(before.st_mode)
        and before.st_size == 65
        and _identity(before) == _identity(after)
        and content == (identity_sha256 + "\n").encode("ascii")
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _safe_output_root(
    base_directory: Path,
    locator: str,
    parents: Sequence[_ParentReplay],
) -> tuple[Path, Path]:
    base = base_directory.resolve()
    requested = base / locator
    if _path_has_symlink(requested):
        raise _fail(ErrorCode.OUTPUT_PATH, "A symlinked conversion output root is not accepted.")
    prospective_root = requested.resolve(strict=False)
    prospective_conversion_root = prospective_root / "conversion"
    if any(
        _paths_overlap(prospective_conversion_root, parent.manifest_path.parent)
        for parent in parents
    ):
        raise _fail(
            ErrorCode.OUTPUT_PATH,
            "Conversion output must not overlap an immutable replay directory.",
        )
    try:
        requested.mkdir(parents=True, exist_ok=True)
        output_root = requested.resolve(strict=True)
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Conversion output root could not be created."
        ) from error
    if output_root == Path(output_root.anchor) or output_root == base or not output_root.is_dir():
        raise _fail(ErrorCode.OUTPUT_PATH, "Conversion output root is unsafe or too broad.")
    conversion_root = output_root / "conversion"
    if conversion_root.exists() and _path_has_symlink(conversion_root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Conversion run root is symlinked.")
    try:
        conversion_root.mkdir(exist_ok=True)
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Conversion run root could not be created.") from error
    if not conversion_root.is_dir():
        raise _fail(ErrorCode.OUTPUT_PATH, "Conversion run root is not a directory.")
    return output_root, conversion_root


def _parent_descriptor(parent: _ParentReplay) -> dict[str, Any]:
    return {
        "replay_id": parent.replay_id,
        "manifest_sha256": parent.manifest_sha256,
        "status": parent.status,
        "trading_date": parent.trading_date.isoformat(),
        "config_sha256": parent.config_sha256,
        "identity_sha256": parent.identity_sha256,
        "source_sha256": parent.source_sha256,
        "events_sha256": parent.events.sha256,
        "snapshots_sha256": parent.snapshots.sha256,
        "snapshot_depth": parent.snapshot_metadata.depth,
    }


def _artefact_descriptor(item: _ParquetArtefact) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "path": item.path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "row_count": item.row_count,
        "trading_date": item.trading_date,
        "symbol": item.symbol,
    }


def _verify_existing(
    directory: Path,
    identity_sha256: str,
    config: ConversionConfig,
    parents: Sequence[_ParentReplay],
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> ConversionResult | None:
    if not directory.name.endswith(identity_sha256[:12]):
        return None
    manifest_path = directory / _MANIFEST_NAME
    if not manifest_path.exists():
        return None
    content, _ = _read_regular_file(manifest_path, _MAX_MANIFEST_BYTES)
    document = _strict_json(content)
    _validate_manifest_document(document, "conversion")
    if document["identity_sha256"] != identity_sha256:
        return None
    hashes = config_hashes(config)
    expected_status = (
        "degraded" if any(parent.status == "degraded" for parent in parents) else "completed"
    )
    depth = parents[0].snapshot_metadata.depth
    schemas = {"events": event_schema(), "snapshots": snapshot_schema(depth)}
    if (
        document["conversion_id"] != directory.name
        or document["config"] != config_document(config)
        or document["config_sha256"] != hashes.config_sha256
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["tool"]["sha256"] != tool_sha256
        or document["status"] != expected_status
        or document["parents"] != [_parent_descriptor(parent) for parent in parents]
        or document["schemas"]
        != {
            "events": _schema_descriptor(schemas["events"]),
            "snapshots": _schema_descriptor(schemas["snapshots"]),
        }
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Existing conversion lineage is inconsistent.")
    artefacts, by_partition = _inspect_parquet_outputs(
        directory,
        schemas,
        cancel_requested,
        existing=True,
    )
    artefact_documents = [_artefact_descriptor(item) for item in artefacts]
    counts: dict[str, Any] = {
        "events": sum(item.row_count for item in artefacts if item.kind == "events"),
        "snapshots": sum(item.row_count for item in artefacts if item.kind == "snapshots"),
        "parquet_files": len(artefacts),
        "by_partition": by_partition,
    }
    if document["artefacts"] != artefact_documents or document["counts"] != counts:
        raise _fail(
            ErrorCode.HASH_MISMATCH, "Existing conversion manifest disagrees with Parquet output."
        )
    return ConversionResult(
        conversion_id=cast(str, document["conversion_id"]),
        status=cast(str, document["status"]),
        manifest_path=manifest_path,
        event_rows=counts["events"],
        snapshot_rows=counts["snapshots"],
        parquet_files=counts["parquet_files"],
        parent_replay_ids=tuple(parent.replay_id for parent in parents),
        partitions=len(by_partition),
        reused=True,
    )


def _prepare_run(
    output_root: Path,
    conversion_root: Path,
    identity_sha256: str,
    force_new_run: bool,
    config: ConversionConfig,
    parents: Sequence[_ParentReplay],
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> ConversionResult | _RunPaths:
    lock_path = conversion_root / f".{identity_sha256}.lock"
    staging_created = False
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise _fail(
            ErrorCode.RUN_EXISTS, "A conversion with this identity is already locked."
        ) from error
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Conversion identity lock could not be created."
        ) from error

    try:
        if not force_new_run:
            for directory in sorted(conversion_root.iterdir()):
                _check_cancel(cancel_requested)
                if not directory.is_dir() or directory == lock_path:
                    continue
                if directory.name.endswith(".partial"):
                    marker = directory / _IDENTITY_MARKER
                    if _identity_marker_matches(marker, identity_sha256):
                        raise _fail(
                            ErrorCode.RUN_EXISTS,
                            "A partial conversion with this identity already exists.",
                        )
                    continue
                existing = _verify_existing(
                    directory,
                    identity_sha256,
                    config,
                    parents,
                    tool_sha256,
                    cancel_requested,
                )
                if existing is not None:
                    _remove_lock(lock_path)
                    return existing

        now_ns = time.time_ns()
        conversion_id = _run_id(identity_sha256, now_ns)
        final_directory = conversion_root / conversion_id
        staging_directory = conversion_root / f"{conversion_id}.partial"
        if final_directory.exists():
            raise _fail(ErrorCode.RUN_EXISTS, "Conversion run ID already exists.")
        staging_directory.mkdir()
        staging_created = True
        (staging_directory / _IDENTITY_MARKER).write_text(identity_sha256 + "\n", encoding="ascii")
        return _RunPaths(
            output_root=output_root,
            conversion_root=conversion_root,
            lock_path=lock_path,
            staging_directory=staging_directory,
            final_directory=final_directory,
        )
    except ConversionError:
        if lock_path.exists() and not staging_created:
            _remove_lock(lock_path)
        raise
    except OSError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Conversion staging directory could not be prepared."
        ) from error


def _recheck_parent_manifests(parents: Sequence[_ParentReplay]) -> None:
    for parent in parents:
        content, identity_value = _read_regular_file(parent.manifest_path, _MAX_MANIFEST_BYTES)
        if (
            identity_value != parent.manifest_identity
            or hashlib.sha256(content).hexdigest() != parent.manifest_sha256
        ):
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "A replay manifest changed during conversion.",
                partial_exists=True,
            )


def _build_manifest(
    config: ConversionConfig,
    parents: Sequence[_ParentReplay],
    identity_sha256: str,
    tool_sha256: str,
    conversion_id: str,
    started_at_ns: int,
    completed_at_ns: int,
    schemas: Mapping[str, pa.Schema],
    artefacts: Sequence[_ParquetArtefact],
    by_partition: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    hashes = config_hashes(config)
    status = "degraded" if any(parent.status == "degraded" for parent in parents) else "completed"
    event_rows = sum(item.row_count for item in artefacts if item.kind == "events")
    snapshot_rows = sum(item.row_count for item in artefacts if item.kind == "snapshots")
    return {
        "schema_version": _SCHEMA_VERSION,
        "conversion_id": conversion_id,
        "status": status,
        "started_at": _timestamp(started_at_ns),
        "completed_at": _timestamp(completed_at_ns),
        "config": config_document(config),
        "config_sha256": hashes.config_sha256,
        "identity_config_sha256": hashes.identity_config_sha256,
        "identity_sha256": identity_sha256,
        "tool": {
            "application_version": __version__,
            "content_digest_kind": "python-package-content-v1",
            "sha256": tool_sha256,
            "python_version": platform.python_version(),
            "pyarrow_version": pa.__version__,
        },
        "parents": [_parent_descriptor(parent) for parent in parents],
        "partition_keys": list(_PARTITION_KEYS),
        "sort_keys": list(_SORT_KEYS),
        "schemas": {
            "events": _schema_descriptor(schemas["events"]),
            "snapshots": _schema_descriptor(schemas["snapshots"]),
        },
        "counts": {
            "events": event_rows,
            "snapshots": snapshot_rows,
            "parquet_files": len(artefacts),
            "by_partition": list(by_partition),
        },
        "artefacts": [_artefact_descriptor(item) for item in artefacts],
    }


def _publish(paths: _RunPaths, document: Mapping[str, Any]) -> None:
    manifest_partial = paths.staging_directory / f"{_MANIFEST_NAME}.partial"
    manifest_final = paths.staging_directory / _MANIFEST_NAME
    try:
        encoded = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise _fail(
                ErrorCode.DISK_WRITE,
                "Conversion manifest exceeds its size bound.",
                partial_exists=True,
            )
        with manifest_partial.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        manifest_partial.rename(manifest_final)
        _remove_lock(paths.lock_path)
        paths.staging_directory.rename(paths.final_directory)
        try:
            (paths.final_directory / _IDENTITY_MARKER).unlink()
        except OSError:
            pass
    except ConversionError:
        raise
    except OSError as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Completed conversion could not be atomically published.",
            partial_exists=True,
        ) from error


def convert_replays(
    config: ConversionConfig,
    *,
    base_directory: Path | None = None,
    force_new_run: bool = False,
    cancel_requested: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> ConversionResult:
    """Convert validated replay parents into one immutable partitioned Parquet run."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    _check_cancel(cancellation)
    _validate_conversion_config(config)
    if not pa.Codec.is_available(config.parquet.compression):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Configured Parquet compression is unavailable.")

    parents = [_load_parent(locator, base) for locator in config.replay_manifests]
    _validate_parent_set(parents, config.allow_degraded)
    _check_cancel(cancellation)

    hashes = config_hashes(config)
    tool_sha256 = _package_content_sha256()
    identity_sha256 = _stage_identity(
        b"itchlab-conversion-v1",
        [parent.manifest_sha256 for parent in parents],
        hashes.identity_config_sha256,
        tool_sha256,
    )
    output_root, conversion_root = _safe_output_root(base, config.output_root, parents)
    prepared = _prepare_run(
        output_root,
        conversion_root,
        identity_sha256,
        force_new_run,
        config,
        parents,
        tool_sha256,
        cancellation,
    )
    if isinstance(prepared, ConversionResult):
        return prepared
    paths = prepared
    started_at_ns = time.time_ns()

    depth = parents[0].snapshot_metadata.depth
    schemas = {"events": event_schema(), "snapshots": snapshot_schema(depth)}
    try:
        _write_dataset(
            _event_batches(parents, schemas["events"], cancellation, progress),
            schemas["events"],
            paths.staging_directory / "events",
            config.parquet.row_group_size,
        )
        _check_cancel(cancellation)
        _write_dataset(
            _snapshot_batches(parents, schemas["snapshots"], cancellation, progress),
            schemas["snapshots"],
            paths.staging_directory / "snapshots",
            config.parquet.row_group_size,
        )
        artefacts, by_partition = _inspect_parquet_outputs(
            paths.staging_directory,
            schemas,
            cancellation,
            existing=False,
        )
        if sum(item.row_count for item in artefacts if item.kind == "events") != sum(
            parent.events.record_count for parent in parents
        ) or sum(item.row_count for item in artefacts if item.kind == "snapshots") != sum(
            parent.snapshots.record_count for parent in parents
        ):
            raise _fail(
                ErrorCode.INVARIANT,
                "Parquet row counts do not match authenticated parent counts.",
                partial_exists=True,
            )
        _recheck_parent_manifests(parents)
        completed_at_ns = time.time_ns()
        document = _build_manifest(
            config,
            parents,
            identity_sha256,
            tool_sha256,
            paths.final_directory.name,
            started_at_ns,
            completed_at_ns,
            schemas,
            artefacts,
            by_partition,
        )
        _validate_manifest_document(document, "conversion")
        _publish(paths, document)
    except ConversionError as error:
        if error.partial_exists:
            raise
        raise _fail(error.code, error.message, partial_exists=True) from error
    except (pa.ArrowException, OSError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Parquet conversion failed while writing staged output.",
            partial_exists=True,
        ) from error

    return ConversionResult(
        conversion_id=paths.final_directory.name,
        status=cast(str, document["status"]),
        manifest_path=paths.final_directory / _MANIFEST_NAME,
        event_rows=cast(int, document["counts"]["events"]),
        snapshot_rows=cast(int, document["counts"]["snapshots"]),
        parquet_files=cast(int, document["counts"]["parquet_files"]),
        parent_replay_ids=tuple(parent.replay_id for parent in parents),
        partitions=len(cast(list[object], document["counts"]["by_partition"])),
        reused=False,
    )


__all__ = ["convert_replays", "event_schema", "snapshot_schema"]
