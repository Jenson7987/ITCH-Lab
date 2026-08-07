"""Authenticated causal-dataset construction and immutable publication."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path, PurePosixPath
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
from itchlab_research.config import (
    ConversionConfig,
    DatasetConfig,
    ReplayConfig,
    parse_config,
)
from itchlab_research.conversion import event_schema, snapshot_schema
from itchlab_research.datasets.features import (
    build_feature_batches,
    feature_catalogue_document,
)
from itchlab_research.datasets.labels import build_label_batches, label_horizons
from itchlab_research.datasets.models import (
    DatasetProgress,
    DatasetResult,
    FeaturePartitionContext,
)
from itchlab_research.datasets.splits import (
    PartitionJoinCounts,
    dataset_schema,
    join_feature_label_batches,
    partition_mapping,
)
from itchlab_research.errors import (
    ConfigValidationError,
    DatasetBuildError,
    ErrorCode,
    FeatureComputationError,
    LabelComputationError,
)

_SCHEMA_VERSION: Final = 1
_MANIFEST_NAME: Final = "dataset-manifest.json"
_IDENTITY_MARKER: Final = "identity.sha256"
_DATASET_RUN_ROOT: Final = Path("runs") / "dataset"
_MAX_MANIFEST_BYTES: Final = 4 << 20
_HASH_CHUNK_BYTES: Final = 1 << 20
_INPUT_BATCH_ROWS: Final = 65_536
_ROW_GROUP_ROWS: Final = 65_536
_PARTITION_KEYS: Final = ("partition", "trading_date", "symbol")
_SORT_KEYS: Final = ("message_index",)
_CLASS_VALUE: Final = {"down": -1, "flat": 0, "up": 1}

CancelCheck: TypeAlias = Callable[[], bool]
ProgressCallback: TypeAlias = Callable[[DatasetProgress], None]
_FileIdentity: TypeAlias = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _ReplayContext:
    manifest_path: Path
    manifest_sha256: str
    manifest_identity: _FileIdentity
    replay_id: str
    trading_date: date
    session_start_ns: int
    session_end_ns: int
    snapshot_depth: int
    instruments: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _InputArtefact:
    kind: str
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    row_count: int
    trading_date: date
    symbol: str
    identity: _FileIdentity


@dataclass(frozen=True, slots=True)
class _ConversionParent:
    locator: str
    manifest_path: Path
    manifest_sha256: str
    manifest_identity: _FileIdentity
    conversion_id: str
    config_sha256: str
    identity_sha256: str
    snapshot_depth: int
    contexts: tuple[_ReplayContext, ...]
    artefacts: tuple[_InputArtefact, ...]


@dataclass(frozen=True, slots=True)
class _OutputArtefact:
    path: str
    sha256: str
    size_bytes: int
    row_count: int
    partition: str
    trading_date: str
    symbol: str


@dataclass(frozen=True, slots=True)
class _SupportingArtefact:
    kind: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _RunPaths:
    dataset_root: Path
    lock_path: Path
    staging_directory: Path
    final_directory: Path


@dataclass(frozen=True, slots=True)
class _PartitionSummary:
    partition: str
    trading_date: str
    symbol: str
    counts: PartitionJoinCounts


def _fail(code: ErrorCode, message: str, *, partial_exists: bool = False) -> DatasetBuildError:
    return DatasetBuildError(code, message, partial_exists=partial_exists)


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
        raise _fail(ErrorCode.CANCELLED, "Dataset construction was cancelled at a batch boundary.")


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


def _strict_json(content: bytes, *, description: str = "Manifest") -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        document = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_names,
            parse_constant=reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{description} is not strict JSON/I-JSON."
        ) from error
    if not isinstance(document, dict):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{description} root is not an object.")
    return cast(dict[str, Any], document)


@lru_cache(maxsize=3)
def _manifest_validator(kind: str) -> Draft202012Validator:
    schema_names = [
        "replay-config.schema.json",
        "replay-manifest.schema.json",
        "conversion-config.schema.json",
        "conversion-manifest.schema.json",
        "dataset-config.schema.json",
        "dataset-manifest.schema.json",
    ]
    resources: list[tuple[str, Resource[Any]]] = []
    documents: dict[str, dict[str, Any]] = {}
    for name in schema_names:
        resource = files("itchlab_research._schemas").joinpath(name)
        document = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
        documents[name] = document
        resources.append((cast(str, document["$id"]), Resource.from_contents(document)))
    registry = Registry().with_resources(resources)
    root_name = f"{kind}-manifest.schema.json"
    return Draft202012Validator(
        documents[root_name],
        registry=registry,
        format_checker=FormatChecker(),
    )


def _validate_manifest_document(document: Mapping[str, Any], kind: str) -> None:
    if list(_manifest_validator(kind).iter_errors(document)):
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} manifest violates schema version 1."
        )


def _sha256_file(path: Path, cancel_requested: CancelCheck) -> tuple[str, int, _FileIdentity]:
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
            raise _fail(ErrorCode.HASH_MISMATCH, "Artefact changed while hashing.") from error
        if observed != before.st_size or _identity(before) != _identity(after):
            raise _fail(ErrorCode.HASH_MISMATCH, "Artefact changed while hashing.")
    return digest.hexdigest(), observed, _identity(after)


def _stage_identity(
    domain: bytes,
    parent_hashes: Sequence[str],
    identity_config_sha256: str,
    tool_sha256: str,
) -> str:
    try:
        digest = hashlib.sha256(domain + b"\0")
        for value in parent_hashes:
            digest.update(bytes.fromhex(value))
        digest.update(bytes.fromhex(identity_config_sha256))
        digest.update(bytes.fromhex(tool_sha256))
    except ValueError as error:
        raise _fail(
            ErrorCode.HASH_MISMATCH, "A stage identity input is not valid SHA-256."
        ) from error
    digest.update(_SCHEMA_VERSION.to_bytes(2, "big"))
    return digest.hexdigest()


def _package_content_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    package_files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix in {".py", ".json"} or path.name == "py.typed")
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


def _schema_descriptor(schema: pa.Schema) -> dict[str, Any]:
    fields_value = [
        {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
    return {
        "fields": fields_value,
        "sha256": hashlib.sha256(canonical_json_bytes(fields_value)).hexdigest(),
    }


def _parse_manifest_config(
    document: Mapping[str, Any], kind: str
) -> ReplayConfig | ConversionConfig:
    try:
        parsed = parse_config(
            json.dumps(document["config"], ensure_ascii=False, allow_nan=False), cast(Any, kind)
        )
    except (ConfigValidationError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION,
            f"{kind.capitalize()} manifest config is semantically invalid.",
        ) from error
    if not isinstance(parsed, (ReplayConfig, ConversionConfig)):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Manifest config has an unexpected domain type.")
    return parsed


def _load_replay_context(
    locator: str,
    descriptor: Mapping[str, Any],
    base_directory: Path,
) -> _ReplayContext:
    if not _safe_relative_path(locator):
        raise _fail(ErrorCode.INPUT_PATH, "Conversion replay locator is not a safe relative path.")
    path = base_directory / locator
    content, manifest_identity = _read_regular_file(path, _MAX_MANIFEST_BYTES)
    manifest_sha256 = hashlib.sha256(content).hexdigest()
    if manifest_sha256 != descriptor["manifest_sha256"]:
        raise _fail(
            ErrorCode.HASH_MISMATCH, "Replay manifest hash disagrees with conversion lineage."
        )
    document = _strict_json(content, description="Replay manifest")
    _validate_manifest_document(document, "replay")
    parsed = _parse_manifest_config(document, "replay")
    if not isinstance(parsed, ReplayConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Replay manifest config type is invalid.")
    hashes = config_hashes(parsed)
    expected_identity = _stage_identity(
        b"itchlab-replay-v1",
        [cast(str, document["source"]["sha256"])],
        cast(str, document["identity_config_sha256"]),
        cast(str, document["executable_sha256"]),
    )
    artefacts = cast(list[dict[str, Any]], document["artefacts"])
    snapshots = next(item for item in artefacts if item["kind"] == "snapshots")
    expected_descriptor = {
        "replay_id": document["replay_id"],
        "manifest_sha256": manifest_sha256,
        "status": document["status"],
        "trading_date": document["source"]["trading_date"],
        "config_sha256": document["config_sha256"],
        "identity_sha256": document["identity_sha256"],
        "source_sha256": document["source"]["sha256"],
        "events_sha256": next(item for item in artefacts if item["kind"] == "events")["sha256"],
        "snapshots_sha256": snapshots["sha256"],
        "snapshot_depth": snapshots["depth"],
    }
    if (
        dict(descriptor) != expected_descriptor
        or document["status"] != "completed"
        or hashes.config_sha256 != document["config_sha256"]
        or hashes.identity_config_sha256 != document["identity_config_sha256"]
        or expected_identity != document["identity_sha256"]
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Replay context lineage is inconsistent.")
    instruments = cast(list[dict[str, Any]], document["instruments"])
    instrument_pairs = tuple(
        (cast(str, item["symbol"]), cast(int, item["symbol_id"])) for item in instruments
    )
    if tuple(symbol for symbol, _symbol_id in instrument_pairs) != parsed.selection.symbols:
        raise _fail(ErrorCode.INVARIANT, "Replay instrument order disagrees with its config.")
    return _ReplayContext(
        manifest_path=path,
        manifest_sha256=manifest_sha256,
        manifest_identity=manifest_identity,
        replay_id=cast(str, document["replay_id"]),
        trading_date=date.fromisoformat(parsed.input.trading_date),
        session_start_ns=parsed.selection.session_start_ns,
        session_end_ns=parsed.selection.session_end_ns,
        snapshot_depth=cast(int, snapshots["depth"]),
        instruments=instrument_pairs,
    )


def _physical_conversion_schema(schema: pa.Schema) -> pa.Schema:
    return pa.schema([field for field in schema if field.name not in {"trading_date", "symbol"}])


def _parse_conversion_path(relative: str, kind: str) -> tuple[date, str, int]:
    if not _safe_relative_path(relative):
        raise _fail(ErrorCode.INPUT_PATH, "Conversion manifest names an unsafe Parquet path.")
    parts = PurePosixPath(relative).parts
    if len(parts) != 4 or parts[0] != kind:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion Parquet path shape is invalid.")
    date_text = parts[1].removeprefix("trading_date=")
    encoded_symbol = parts[2].removeprefix("symbol=")
    filename = parts[3]
    if parts[1] != f"trading_date={date_text}" or parts[2] != f"symbol={encoded_symbol}":
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion partition keys are invalid.")
    symbol = unquote(encoded_symbol)
    if quote(symbol, safe="A-Za-z0-9._~-") != encoded_symbol:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion symbol encoding is not canonical.")
    prefix, separator, suffix = filename.removeprefix("part-").partition(".parquet")
    if not separator or suffix or not prefix.isdigit():
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion Parquet part name is invalid.")
    try:
        trading_date = date.fromisoformat(date_text)
    except ValueError as error:
        raise _fail(ErrorCode.TRADING_DATE, "Conversion partition date is invalid.") from error
    return trading_date, symbol, int(prefix)


def _validate_message_indices(path: Path, previous: int | None = None) -> int | None:
    try:
        parquet_file = pq.ParquetFile(path)
        for batch in parquet_file.iter_batches(
            batch_size=_INPUT_BATCH_ROWS, columns=["message_index"], use_threads=False
        ):
            values = cast(list[int], batch.column(0).to_pylist())
            for value in values:
                if previous is not None and value <= previous:
                    raise _fail(ErrorCode.INVARIANT, "Parquet message indices are not increasing.")
                previous = value
    except DatasetBuildError:
        raise
    except (OSError, pa.ArrowException) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Parquet message indices are unreadable.") from error
    return previous


def _load_conversion_artefacts(
    directory: Path,
    document: Mapping[str, Any],
    depth: int,
    config: ConversionConfig,
    cancel_requested: CancelCheck,
) -> tuple[_InputArtefact, ...]:
    schemas = {"events": event_schema(), "snapshots": snapshot_schema(depth)}
    expected_descriptors = {kind: _schema_descriptor(schema) for kind, schema in schemas.items()}
    if (
        document["partition_keys"] != ["trading_date", "symbol"]
        or document["sort_keys"] != ["message_index"]
        or document["schemas"] != expected_descriptors
    ):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion table contracts are unsupported.")

    artefacts: list[_InputArtefact] = []
    seen_paths: set[str] = set()
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"events": 0, "snapshots": 0}
    )
    last_by_partition: dict[tuple[str, date, str], int | None] = {}
    entries = cast(list[dict[str, Any]], document["artefacts"])
    parsed_entries: list[tuple[str, date, str, int, dict[str, Any]]] = []
    for entry in entries:
        kind = cast(str, entry["kind"])
        trading_date, symbol, part_number = _parse_conversion_path(cast(str, entry["path"]), kind)
        parsed_entries.append((kind, trading_date, symbol, part_number, entry))

    for kind, trading_date, symbol, _part_number, entry in sorted(parsed_entries):
        _check_cancel(cancel_requested)
        relative = cast(str, entry["path"])
        if relative in seen_paths:
            raise _fail(ErrorCode.INVARIANT, "Conversion manifest repeats a Parquet path.")
        seen_paths.add(relative)
        if entry["trading_date"] != trading_date.isoformat() or entry["symbol"] != symbol:
            raise _fail(ErrorCode.INVARIANT, "Conversion Parquet path metadata is inconsistent.")
        path = directory / relative
        digest, size, identity_value = _sha256_file(path, cancel_requested)
        if digest != entry["sha256"] or size != entry["size_bytes"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Conversion Parquet hash or size is invalid.")
        try:
            parquet_file = pq.ParquetFile(path)
            if parquet_file.schema_arrow != _physical_conversion_schema(schemas[kind]):
                raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion Parquet schema is invalid.")
            if parquet_file.metadata.num_rows != entry["row_count"]:
                raise _fail(ErrorCode.HASH_MISMATCH, "Conversion Parquet row count is invalid.")
            if any(
                parquet_file.metadata.row_group(index).num_rows > config.parquet.row_group_size
                for index in range(parquet_file.metadata.num_row_groups)
            ):
                raise _fail(ErrorCode.INVARIANT, "Conversion Parquet row group exceeds its bound.")
        except DatasetBuildError:
            raise
        except (OSError, pa.ArrowException) as error:
            raise _fail(
                ErrorCode.SCHEMA_VERSION, "Conversion Parquet metadata is invalid."
            ) from error
        key = (kind, trading_date, symbol)
        last_by_partition[key] = _validate_message_indices(path, last_by_partition.get(key))
        row_count = cast(int, entry["row_count"])
        counts[(trading_date.isoformat(), symbol)][kind] += row_count
        artefacts.append(
            _InputArtefact(
                kind=kind,
                path=path,
                relative_path=relative,
                sha256=digest,
                size_bytes=size,
                row_count=row_count,
                trading_date=trading_date,
                symbol=symbol,
                identity=identity_value,
            )
        )
    by_partition = [
        {
            "trading_date": trading_date,
            "symbol": symbol,
            "events": values["events"],
            "snapshots": values["snapshots"],
        }
        for (trading_date, symbol), values in sorted(counts.items())
    ]
    expected_counts = {
        "events": sum(item.row_count for item in artefacts if item.kind == "events"),
        "snapshots": sum(item.row_count for item in artefacts if item.kind == "snapshots"),
        "parquet_files": len(artefacts),
        "by_partition": by_partition,
    }
    if document["counts"] != expected_counts:
        raise _fail(ErrorCode.HASH_MISMATCH, "Conversion manifest counts are inconsistent.")
    return tuple(artefacts)


def _load_conversion_parent(
    locator: str,
    base_directory: Path,
    cancel_requested: CancelCheck,
) -> _ConversionParent:
    if not _safe_relative_path(locator):
        raise _fail(ErrorCode.INPUT_PATH, "Dataset conversion locator is not a safe relative path.")
    path = base_directory / locator
    content, manifest_identity = _read_regular_file(path, _MAX_MANIFEST_BYTES)
    document = _strict_json(content, description="Conversion manifest")
    _validate_manifest_document(document, "conversion")
    if document["status"] != "completed":
        raise _fail(ErrorCode.INVARIANT, "Dataset construction rejects degraded conversions.")
    parsed = _parse_manifest_config(document, "conversion")
    if not isinstance(parsed, ConversionConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion manifest config type is invalid.")
    hashes = config_hashes(parsed)
    parent_descriptors = cast(list[dict[str, Any]], document["parents"])
    expected_identity = _stage_identity(
        b"itchlab-conversion-v1",
        [cast(str, item["manifest_sha256"]) for item in parent_descriptors],
        cast(str, document["identity_config_sha256"]),
        cast(str, document["tool"]["sha256"]),
    )
    if (
        hashes.config_sha256 != document["config_sha256"]
        or hashes.identity_config_sha256 != document["identity_config_sha256"]
        or expected_identity != document["identity_sha256"]
        or len(parsed.replay_manifests) != len(parent_descriptors)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Conversion manifest lineage is inconsistent.")
    contexts = tuple(
        _load_replay_context(replay_locator, descriptor, base_directory)
        for replay_locator, descriptor in zip(
            parsed.replay_manifests, parent_descriptors, strict=True
        )
    )
    depths = {context.snapshot_depth for context in contexts}
    if len(depths) != 1:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion replay contexts use different depths.")
    depth = next(iter(depths))
    artefacts = _load_conversion_artefacts(path.parent, document, depth, parsed, cancel_requested)
    return _ConversionParent(
        locator=locator,
        manifest_path=path,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
        manifest_identity=manifest_identity,
        conversion_id=cast(str, document["conversion_id"]),
        config_sha256=cast(str, document["config_sha256"]),
        identity_sha256=cast(str, document["identity_sha256"]),
        snapshot_depth=depth,
        contexts=contexts,
        artefacts=artefacts,
    )


def _validate_dataset_config(config: DatasetConfig) -> dict[date, str]:
    if not isinstance(config, DatasetConfig) or config.schema_version != _SCHEMA_VERSION:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Dataset config schema version is unsupported.")
    if (
        not config.conversion_manifests
        or len(set(config.conversion_manifests)) != len(config.conversion_manifests)
        or any(not _safe_relative_path(value) for value in config.conversion_manifests)
    ):
        raise _fail(ErrorCode.INPUT_PATH, "Dataset conversion locators must be safe and unique.")
    if not config.symbols or len(set(config.symbols)) != len(config.symbols):
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Dataset symbols must be non-empty and unique.")
    tick_sizes = dict(config.tick_size4_by_symbol)
    if set(tick_sizes) != set(config.symbols) or any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFF_FFFF
        for value in tick_sizes.values()
    ):
        raise _fail(ErrorCode.PRICE, "Dataset tick sizes do not match configured symbols.")
    if config.labels.primary_event_horizon != 100 or config.labels.secondary_event_horizons != (
        20,
        500,
    ):
        raise _fail(ErrorCode.HORIZON, "Dataset label horizons do not match version 1.")
    if isinstance(config.sampling.row_stride, bool) or config.sampling.row_stride <= 0:
        raise _fail(ErrorCode.ROW_STRIDE, "Dataset row stride is invalid.")
    # These deterministic constructors also enforce the exact version-1 feature/label catalogues.
    dataset_schema(config.features, config.labels)
    return {value: name for value, name in partition_mapping(config.partitions).items()}


def _validate_parent_set(
    parents: Sequence[_ConversionParent],
    config: DatasetConfig,
    requested_dates: Mapping[date, str],
) -> dict[date, tuple[_ConversionParent, _ReplayContext]]:
    if len({parent.conversion_id for parent in parents}) != len(parents):
        raise _fail(ErrorCode.INVARIANT, "Dataset parents repeat a conversion ID.")
    by_date: dict[date, tuple[_ConversionParent, _ReplayContext]] = {}
    for parent in parents:
        if parent.snapshot_depth < max(config.features.depth_levels):
            raise _fail(
                ErrorCode.DEPTH, "Conversion snapshot depth cannot satisfy the feature set."
            )
        for context in parent.contexts:
            if context.trading_date in by_date:
                raise _fail(ErrorCode.PARTITION, "Conversion parents repeat a trading day.")
            by_date[context.trading_date] = (parent, context)
    missing_dates = set(requested_dates) - set(by_date)
    if missing_dates:
        raise _fail(
            ErrorCode.PARTITION, "A configured partition day is absent from conversion input."
        )
    for trading_date in requested_dates:
        parent, context = by_date[trading_date]
        instruments = dict(context.instruments)
        if any(symbol not in instruments for symbol in config.symbols):
            raise _fail(
                ErrorCode.UNKNOWN_SYMBOL,
                "A configured symbol is absent from a requested replay day.",
            )
        for symbol in config.symbols:
            for kind in ("events", "snapshots"):
                if not any(
                    item.kind == kind
                    and item.trading_date == trading_date
                    and item.symbol == symbol
                    for item in parent.artefacts
                ):
                    raise _fail(
                        ErrorCode.EMPTY_DATASET,
                        "A requested day/symbol lacks required converted rows.",
                    )
    return by_date


def _input_batches(
    parent: _ConversionParent,
    kind: str,
    trading_date: date,
    symbol: str,
    cancel_requested: CancelCheck,
) -> Iterator[pa.RecordBatch]:
    logical_schema = event_schema() if kind == "events" else snapshot_schema(parent.snapshot_depth)
    artefacts = sorted(
        (
            item
            for item in parent.artefacts
            if item.kind == kind and item.trading_date == trading_date and item.symbol == symbol
        ),
        key=lambda item: item.relative_path,
    )
    for artefact in artefacts:
        _check_cancel(cancel_requested)
        try:
            parquet_file = pq.ParquetFile(artefact.path)
            for batch in parquet_file.iter_batches(batch_size=_INPUT_BATCH_ROWS, use_threads=False):
                _check_cancel(cancel_requested)
                data = batch.to_pydict()
                data["trading_date"] = [trading_date] * batch.num_rows
                data["symbol"] = [symbol] * batch.num_rows
                yield pa.RecordBatch.from_pydict(data, schema=logical_schema)
        except DatasetBuildError:
            raise
        except (OSError, pa.ArrowException, OverflowError, TypeError, ValueError) as error:
            raise _fail(ErrorCode.SCHEMA_VERSION, "Converted Parquet rows are invalid.") from error


def _physical_dataset_schema(schema: pa.Schema) -> pa.Schema:
    return pa.schema([field for field in schema if field.name not in _PARTITION_KEYS])


def _write_partition(
    batches: Iterator[pa.RecordBatch],
    schema: pa.Schema,
    staging_directory: Path,
    partition: str,
    trading_date: date,
    symbol: str,
    cancel_requested: CancelCheck,
) -> _OutputArtefact | None:
    encoded_symbol = quote(symbol, safe="A-Za-z0-9._~-")
    relative = (
        Path("dataset")
        / f"partition={partition}"
        / f"trading_date={trading_date.isoformat()}"
        / f"symbol={encoded_symbol}"
        / "part-0.parquet"
    )
    path = staging_directory / relative
    physical_schema = _physical_dataset_schema(schema)
    physical_names = physical_schema.names
    writer: pq.ParquetWriter | None = None
    rows = 0
    try:
        for batch in batches:
            _check_cancel(cancel_requested)
            if not batch.schema.equals(schema, check_metadata=False):
                raise _fail(ErrorCode.SCHEMA_VERSION, "Joined dataset batch schema changed.")
            values = batch.to_pydict()
            if (
                set(cast(list[str], values["partition"])) != {partition}
                or set(cast(list[date], values["trading_date"])) != {trading_date}
                or set(cast(list[str], values["symbol"])) != {symbol}
            ):
                raise _fail(ErrorCode.PARTITION, "Joined rows crossed an output partition.")
            if writer is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(
                    path,
                    physical_schema,
                    compression="zstd",
                    use_dictionary=False,
                    write_statistics=True,
                )
            writer.write_batch(batch.select(physical_names), row_group_size=_ROW_GROUP_ROWS)
            rows += batch.num_rows
        if writer is not None:
            writer.close()
            writer = None
    except DatasetBuildError:
        if writer is not None:
            writer.close()
        raise
    except (OSError, pa.ArrowException) as error:
        if writer is not None:
            writer.close()
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Dataset Parquet partition could not be written.",
            partial_exists=True,
        ) from error
    if rows == 0:
        return None
    digest, size, _identity_value = _sha256_file(path, cancel_requested)
    try:
        parquet_file = pq.ParquetFile(path)
        if parquet_file.schema_arrow != physical_schema or parquet_file.metadata.num_rows != rows:
            raise _fail(ErrorCode.INVARIANT, "Written dataset Parquet metadata is inconsistent.")
        if any(
            parquet_file.metadata.row_group(index).num_rows > _ROW_GROUP_ROWS
            for index in range(parquet_file.metadata.num_row_groups)
        ):
            raise _fail(ErrorCode.INVARIANT, "Dataset Parquet row group exceeds its bound.")
    except DatasetBuildError:
        raise
    except (OSError, pa.ArrowException) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Written dataset Parquet is invalid.") from error
    _validate_message_indices(path)
    return _OutputArtefact(
        path=relative.as_posix(),
        sha256=digest,
        size_bytes=size,
        row_count=rows,
        partition=partition,
        trading_date=trading_date.isoformat(),
        symbol=symbol,
    )


def _counts_document(summaries: Sequence[_PartitionSummary]) -> dict[str, Any]:
    horizons = sorted(
        {horizon for summary in summaries for horizon in summary.counts.label_available}
    )
    row_names = (
        "qualifying_rows",
        "dropped_incomplete_history",
        "dropped_unavailable_primary_label",
        "dropped_by_row_stride",
        "retained_rows",
    )

    def empty_rows() -> dict[str, int]:
        return {name: 0 for name in row_names}

    totals = empty_rows()
    total_classes = {name: 0 for name in _CLASS_VALUE}
    available = {horizon: 0 for horizon in horizons}
    unavailable = {horizon: 0 for horizon in horizons}
    partition_values: dict[str, dict[str, Any]] = {
        name: {"rows": empty_rows(), "classes": {item: 0 for item in _CLASS_VALUE}}
        for name in ("train", "validation", "test")
    }
    by_day_symbol: list[dict[str, Any]] = []
    for summary in summaries:
        counts = summary.counts
        rows = {name: cast(int, getattr(counts, name)) for name in row_names}
        for name, value in rows.items():
            totals[name] += value
            partition_values[summary.partition]["rows"][name] += value
        for name, value in counts.class_counts.items():
            total_classes[name] += value
            partition_values[summary.partition]["classes"][name] += value
        for horizon in horizons:
            available[horizon] += counts.label_available[horizon]
            unavailable[horizon] += counts.label_unavailable[horizon]
        by_day_symbol.append(
            {
                "partition": summary.partition,
                "trading_date": summary.trading_date,
                "symbol": summary.symbol,
                "rows": rows,
                "classes": dict(counts.class_counts),
                "label_availability": [
                    {
                        "horizon": horizon,
                        "available": counts.label_available[horizon],
                        "unavailable": counts.label_unavailable[horizon],
                    }
                    for horizon in horizons
                ],
            }
        )
    return {
        "rows": totals,
        "classes": total_classes,
        "label_availability": [
            {
                "horizon": horizon,
                "available": available[horizon],
                "unavailable": unavailable[horizon],
            }
            for horizon in horizons
        ],
        "by_partition": [
            {
                "partition": name,
                "rows": partition_values[name]["rows"],
                "classes": partition_values[name]["classes"],
            }
            for name in ("train", "validation", "test")
        ],
        "by_day_symbol": by_day_symbol,
    }


def _write_supporting_json(
    staging_directory: Path,
    kind: str,
    filename: str,
    document: Mapping[str, Any],
) -> _SupportingArtefact:
    partial = staging_directory / f"{filename}.partial"
    final = staging_directory / filename
    try:
        encoded = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        with partial.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(final)
    except OSError as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Dataset supporting metadata could not be written.",
            partial_exists=True,
        ) from error
    return _SupportingArtefact(
        kind=kind,
        path=filename,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _safe_dataset_root(base_directory: Path, parents: Sequence[_ConversionParent]) -> Path:
    base = base_directory.resolve()
    runs_root = base / _DATASET_RUN_ROOT.parent
    dataset_root = base / _DATASET_RUN_ROOT
    if _path_has_symlink(runs_root) or _path_has_symlink(dataset_root):
        raise _fail(ErrorCode.OUTPUT_PATH, "The dataset run root may not be symlinked.")
    if any(_paths_overlap(dataset_root, parent.manifest_path.parent) for parent in parents):
        raise _fail(ErrorCode.OUTPUT_PATH, "Dataset output overlaps an immutable conversion.")
    try:
        dataset_root.mkdir(parents=True, exist_ok=True)
        resolved = dataset_root.resolve(strict=True)
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Dataset run root could not be created.") from error
    if resolved == Path(resolved.anchor) or resolved == base or not resolved.is_dir():
        raise _fail(ErrorCode.OUTPUT_PATH, "Dataset run root is unsafe or too broad.")
    return resolved


def _run_id(identity_sha256: str, now_ns: int) -> str:
    seconds, nanoseconds = divmod(now_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}.{nanoseconds:09d}Z-{identity_sha256[:12]}"


def _remove_lock(path: Path) -> None:
    try:
        path.rmdir()
    except OSError as error:
        raise _fail(ErrorCode.DISK_WRITE, "Dataset identity lock could not be removed.") from error


def _identity_marker_matches(path: Path, identity_sha256: str) -> bool:
    if _path_has_symlink(path):
        return False
    try:
        content = path.read_bytes()
        status_result = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(status_result.st_mode)
        and len(content) == 65
        and content == (identity_sha256 + "\n").encode("ascii")
    )


def _parent_descriptor(parent: _ConversionParent) -> dict[str, Any]:
    return {
        "conversion_id": parent.conversion_id,
        "manifest_sha256": parent.manifest_sha256,
        "config_sha256": parent.config_sha256,
        "identity_sha256": parent.identity_sha256,
        "trading_dates": [context.trading_date.isoformat() for context in parent.contexts],
    }


def _output_descriptor(item: _OutputArtefact) -> dict[str, Any]:
    return {
        "kind": "dataset",
        "path": item.path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "row_count": item.row_count,
        "partition": item.partition,
        "trading_date": item.trading_date,
        "symbol": item.symbol,
    }


def _supporting_descriptor(item: _SupportingArtefact) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "path": item.path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
    }


def _validate_counts_invariants(counts: Mapping[str, Any]) -> None:
    row_names = (
        "qualifying_rows",
        "dropped_incomplete_history",
        "dropped_unavailable_primary_label",
        "dropped_by_row_stride",
        "retained_rows",
    )

    def validate_rows(rows: Mapping[str, int]) -> None:
        if rows["qualifying_rows"] != (
            rows["dropped_incomplete_history"]
            + rows["dropped_unavailable_primary_label"]
            + rows["dropped_by_row_stride"]
            + rows["retained_rows"]
        ):
            raise _fail(ErrorCode.INVARIANT, "Dataset manifest row counts do not reconcile.")

    def availability_values(items: object, qualifying_rows: int) -> dict[int, tuple[int, int]]:
        entries = cast(list[Mapping[str, int]], items)
        if [item["horizon"] for item in entries] != [20, 100, 500]:
            raise _fail(ErrorCode.INVARIANT, "Dataset label horizons are not canonical.")
        values = {item["horizon"]: (item["available"], item["unavailable"]) for item in entries}
        if any(
            available + unavailable != qualifying_rows for available, unavailable in values.values()
        ):
            raise _fail(ErrorCode.INVARIANT, "Dataset label-availability counts do not reconcile.")
        return values

    rows = cast(Mapping[str, int], counts["rows"])
    validate_rows(rows)
    classes = cast(Mapping[str, int], counts["classes"])
    if sum(classes.values()) != rows["retained_rows"]:
        raise _fail(ErrorCode.INVARIANT, "Dataset manifest class counts do not reconcile.")
    availability = availability_values(counts["label_availability"], rows["qualifying_rows"])
    by_partition = cast(list[Mapping[str, Any]], counts["by_partition"])
    if [item["partition"] for item in by_partition] != ["train", "validation", "test"]:
        raise _fail(ErrorCode.INVARIANT, "Dataset partition counts are not canonical.")
    for item in by_partition:
        partition_rows = cast(Mapping[str, int], item["rows"])
        partition_classes = cast(Mapping[str, int], item["classes"])
        validate_rows(partition_rows)
        if sum(partition_classes.values()) != partition_rows["retained_rows"]:
            raise _fail(ErrorCode.INVARIANT, "Dataset partition classes do not reconcile.")
    if any(
        sum(cast(Mapping[str, int], item["rows"])[name] for item in by_partition) != rows[name]
        for name in row_names
    ) or any(
        sum(cast(Mapping[str, int], item["classes"])[name] for item in by_partition)
        != classes[name]
        for name in _CLASS_VALUE
    ):
        raise _fail(ErrorCode.INVARIANT, "Dataset partition counts do not reconcile.")

    by_day_symbol = cast(list[Mapping[str, Any]], counts["by_day_symbol"])
    seen_keys: set[tuple[str, str, str]] = set()
    day_availability = {horizon: [0, 0] for horizon in availability}
    for item in by_day_symbol:
        key = (
            cast(str, item["partition"]),
            cast(str, item["trading_date"]),
            cast(str, item["symbol"]),
        )
        if key in seen_keys:
            raise _fail(ErrorCode.INVARIANT, "Dataset day/symbol counts repeat a partition key.")
        seen_keys.add(key)
        day_rows = cast(Mapping[str, int], item["rows"])
        day_classes = cast(Mapping[str, int], item["classes"])
        validate_rows(day_rows)
        if sum(day_classes.values()) != day_rows["retained_rows"]:
            raise _fail(ErrorCode.INVARIANT, "Dataset day/symbol classes do not reconcile.")
        values = availability_values(item["label_availability"], day_rows["qualifying_rows"])
        for horizon, (available, unavailable) in values.items():
            day_availability[horizon][0] += available
            day_availability[horizon][1] += unavailable
    if (
        any(
            sum(cast(Mapping[str, int], item["rows"])[name] for item in by_day_symbol) != rows[name]
            for name in row_names
        )
        or any(
            sum(cast(Mapping[str, int], item["classes"])[name] for item in by_day_symbol)
            != classes[name]
            for name in _CLASS_VALUE
        )
        or any(
            tuple(day_availability[horizon]) != availability[horizon] for horizon in availability
        )
    ):
        raise _fail(ErrorCode.INVARIANT, "Dataset day/symbol counts do not reconcile.")


def _validate_existing_outputs(
    directory: Path,
    document: Mapping[str, Any],
    schema: pa.Schema,
    cancel_requested: CancelCheck,
) -> tuple[int, dict[str, int], dict[str, int]]:
    physical_schema = _physical_dataset_schema(schema)
    primary_name = f"label_horizon_{document['config']['labels']['primary_event_horizon']}"
    total_rows = 0
    class_counts = {name: 0 for name in _CLASS_VALUE}
    partition_rows = {name: 0 for name in ("train", "validation", "test")}
    seen_paths: set[str] = set()
    for entry in cast(list[dict[str, Any]], document["artefacts"]):
        relative = cast(str, entry["path"])
        if not _safe_relative_path(relative) or relative in seen_paths:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest repeats an unsafe child path.")
        seen_paths.add(relative)
        parts = PurePosixPath(relative).parts
        if len(parts) != 5 or parts[0] != "dataset" or parts[4] != "part-0.parquet":
            raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet path shape is invalid.")
        partition = parts[1].removeprefix("partition=")
        date_text = parts[2].removeprefix("trading_date=")
        encoded_symbol = parts[3].removeprefix("symbol=")
        symbol = unquote(encoded_symbol)
        if (
            partition not in partition_rows
            or parts[1] != f"partition={partition}"
            or parts[2] != f"trading_date={date_text}"
            or parts[3] != f"symbol={encoded_symbol}"
            or quote(symbol, safe="A-Za-z0-9._~-") != encoded_symbol
            or entry["partition"] != partition
            or entry["trading_date"] != date_text
            or entry["symbol"] != symbol
        ):
            raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet partition metadata is invalid.")
        try:
            date.fromisoformat(date_text)
        except ValueError as error:
            raise _fail(ErrorCode.TRADING_DATE, "Dataset Parquet date is invalid.") from error
        path = directory / relative
        digest, size, _identity_value = _sha256_file(path, cancel_requested)
        if digest != entry["sha256"] or size != entry["size_bytes"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset Parquet hash or size is invalid.")
        try:
            parquet_file = pq.ParquetFile(path)
            if parquet_file.schema_arrow != physical_schema:
                raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet schema is invalid.")
            row_count = parquet_file.metadata.num_rows
            if row_count != entry["row_count"]:
                raise _fail(ErrorCode.HASH_MISMATCH, "Dataset Parquet row count is invalid.")
            for batch in parquet_file.iter_batches(
                batch_size=_INPUT_BATCH_ROWS,
                columns=[primary_name],
                use_threads=False,
            ):
                for value in cast(list[int | None], batch.column(0).to_pylist()):
                    if value is None or value not in {-1, 0, 1}:
                        raise _fail(ErrorCode.INVARIANT, "Dataset primary label is invalid.")
                    class_counts[
                        next(name for name, number in _CLASS_VALUE.items() if number == value)
                    ] += 1
        except DatasetBuildError:
            raise
        except (OSError, pa.ArrowException) as error:
            raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset Parquet content is invalid.") from error
        _validate_message_indices(path)
        total_rows += row_count
        partition_rows[partition] += row_count
    return total_rows, class_counts, partition_rows


def _verify_existing(
    directory: Path,
    identity_sha256: str,
    config: DatasetConfig,
    parents: Sequence[_ConversionParent],
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> DatasetResult | None:
    if not directory.name.endswith(identity_sha256[:12]):
        return None
    manifest_path = directory / _MANIFEST_NAME
    if not manifest_path.exists():
        return None
    content, _manifest_identity = _read_regular_file(manifest_path, _MAX_MANIFEST_BYTES)
    document = _strict_json(content, description="Dataset manifest")
    _validate_manifest_document(document, "dataset")
    if document["identity_sha256"] != identity_sha256:
        return None
    hashes = config_hashes(config)
    schema = dataset_schema(config.features, config.labels)
    if (
        document["dataset_id"] != directory.name
        or document["status"] != "completed"
        or document["config"] != config_document(config)
        or document["config_sha256"] != hashes.config_sha256
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["tool"]["sha256"] != tool_sha256
        or document["parents"] != [_parent_descriptor(parent) for parent in parents]
        or document["partition_keys"] != list(_PARTITION_KEYS)
        or document["sort_keys"] != list(_SORT_KEYS)
        or document["schema"] != _schema_descriptor(schema)
        or document["feature_catalogue"] != feature_catalogue_document(config.features)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Existing dataset lineage is inconsistent.")
    supporting = cast(list[dict[str, Any]], document["supporting_artefacts"])
    expected_names = {
        "feature_catalogue": "feature-catalogue.json",
        "data_quality": "data-quality.json",
    }
    support_documents: dict[str, dict[str, Any]] = {}
    for entry in supporting:
        if entry["path"] != expected_names.get(cast(str, entry["kind"])):
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset supporting artefact path is invalid.")
        path = directory / cast(str, entry["path"])
        data, _identity_value = _read_regular_file(path, _MAX_MANIFEST_BYTES)
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["size_bytes"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset supporting artefact hash is invalid.")
        support_documents[cast(str, entry["kind"])] = _strict_json(
            data, description="Dataset supporting artefact"
        )
    if support_documents.get("feature_catalogue") != feature_catalogue_document(
        config.features
    ) or support_documents.get("data_quality") != {
        "schema_version": 1,
        "filter_order": [
            "history_complete",
            "primary_label_available",
            "qualifying_ordinal_mod_row_stride",
        ],
        "counts": document["counts"],
    }:
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset supporting metadata is inconsistent.")
    _validate_counts_invariants(cast(Mapping[str, Any], document["counts"]))
    total_rows, class_counts, partition_rows = _validate_existing_outputs(
        directory, document, schema, cancel_requested
    )
    counts = cast(Mapping[str, Any], document["counts"])
    if (
        total_rows != counts["rows"]["retained_rows"]
        or class_counts != counts["classes"]
        or partition_rows
        != {item["partition"]: item["rows"]["retained_rows"] for item in counts["by_partition"]}
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset manifest disagrees with Parquet output.")
    return DatasetResult(
        dataset_id=cast(str, document["dataset_id"]),
        status="completed",
        manifest_path=manifest_path,
        retained_rows=total_rows,
        parquet_files=len(cast(list[object], document["artefacts"])),
        parent_conversion_ids=tuple(parent.conversion_id for parent in parents),
        partition_rows=tuple(
            (name, partition_rows[name]) for name in ("train", "validation", "test")
        ),
        class_counts=tuple((name, class_counts[name]) for name in ("down", "flat", "up")),
        reused=True,
    )


def _prepare_run(
    dataset_root: Path,
    identity_sha256: str,
    force_new_run: bool,
    config: DatasetConfig,
    parents: Sequence[_ConversionParent],
    tool_sha256: str,
    cancel_requested: CancelCheck,
) -> DatasetResult | _RunPaths:
    lock_path = dataset_root / f".{identity_sha256}.lock"
    staging_created = False
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise _fail(
            ErrorCode.RUN_EXISTS, "A dataset with this identity is already locked."
        ) from error
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Dataset identity lock could not be created.") from error
    try:
        if not force_new_run:
            for directory in sorted(dataset_root.iterdir()):
                _check_cancel(cancel_requested)
                if not directory.is_dir() or directory == lock_path:
                    continue
                if directory.name.endswith(".partial"):
                    if _identity_marker_matches(directory / _IDENTITY_MARKER, identity_sha256):
                        raise _fail(
                            ErrorCode.RUN_EXISTS,
                            "A partial dataset with this identity already exists.",
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
        dataset_id = _run_id(identity_sha256, time.time_ns())
        final_directory = dataset_root / dataset_id
        staging_directory = dataset_root / f"{dataset_id}.partial"
        if final_directory.exists():
            raise _fail(ErrorCode.RUN_EXISTS, "Dataset run ID already exists.")
        staging_directory.mkdir()
        staging_created = True
        (staging_directory / _IDENTITY_MARKER).write_text(identity_sha256 + "\n", encoding="ascii")
        return _RunPaths(dataset_root, lock_path, staging_directory, final_directory)
    except DatasetBuildError:
        if lock_path.exists() and not staging_created:
            _remove_lock(lock_path)
        raise
    except OSError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Dataset staging directory could not be prepared."
        ) from error


def _recheck_inputs(parents: Sequence[_ConversionParent]) -> None:
    for parent in parents:
        content, identity_value = _read_regular_file(parent.manifest_path, _MAX_MANIFEST_BYTES)
        if (
            identity_value != parent.manifest_identity
            or hashlib.sha256(content).hexdigest() != parent.manifest_sha256
        ):
            raise _fail(
                ErrorCode.HASH_MISMATCH,
                "A conversion manifest changed during dataset construction.",
                partial_exists=True,
            )
        for context in parent.contexts:
            replay_content, replay_identity = _read_regular_file(
                context.manifest_path, _MAX_MANIFEST_BYTES
            )
            if (
                replay_identity != context.manifest_identity
                or hashlib.sha256(replay_content).hexdigest() != context.manifest_sha256
            ):
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "A replay manifest changed during dataset construction.",
                    partial_exists=True,
                )
        for artefact in parent.artefacts:
            try:
                status_result = artefact.path.stat()
            except OSError as error:
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "A conversion artefact disappeared during dataset construction.",
                    partial_exists=True,
                ) from error
            if _identity(status_result) != artefact.identity:
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "A conversion artefact changed during dataset construction.",
                    partial_exists=True,
                )


def _timestamp(value_ns: int) -> str:
    seconds, nanoseconds = divmod(value_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{prefix}.{nanoseconds:09d}Z"


def _build_manifest(
    config: DatasetConfig,
    parents: Sequence[_ConversionParent],
    identity_sha256: str,
    tool_sha256: str,
    dataset_id: str,
    started_at_ns: int,
    completed_at_ns: int,
    schema: pa.Schema,
    counts: Mapping[str, Any],
    artefacts: Sequence[_OutputArtefact],
    supporting: Sequence[_SupportingArtefact],
) -> dict[str, Any]:
    hashes = config_hashes(config)
    return {
        "schema_version": _SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "status": "completed",
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
        "partitions": {
            "train_dates": list(config.partitions.train_dates),
            "validation_dates": list(config.partitions.validation_dates),
            "test_dates": list(config.partitions.test_dates),
        },
        "feature_catalogue": feature_catalogue_document(config.features),
        "labels": {
            "primary_horizon": config.labels.primary_event_horizon,
            "horizons": list(label_horizons(config.labels)),
            "dtype": "int8",
            "classes": [{"name": name, "value": value} for name, value in _CLASS_VALUE.items()],
            "tail_policy": "exclude_unavailable_primary_retain_nullable_secondary",
        },
        "schema": _schema_descriptor(schema),
        "counts": dict(counts),
        "artefacts": [_output_descriptor(item) for item in artefacts],
        "supporting_artefacts": [_supporting_descriptor(item) for item in supporting],
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
                "Dataset manifest exceeds its size bound.",
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
    except DatasetBuildError:
        raise
    except OSError as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Completed dataset could not be atomically published.",
            partial_exists=True,
        ) from error


def build_dataset(
    config: DatasetConfig,
    *,
    base_directory: Path | None = None,
    force_new_run: bool = False,
    cancel_requested: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> DatasetResult:
    """Build and atomically publish one authenticated frozen causal dataset."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    _check_cancel(cancellation)
    requested_dates = _validate_dataset_config(config)
    if not pa.Codec.is_available("zstd"):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Zstandard Parquet compression is unavailable.")
    parents = tuple(
        _load_conversion_parent(locator, base, cancellation)
        for locator in config.conversion_manifests
    )
    by_date = _validate_parent_set(parents, config, requested_dates)
    hashes = config_hashes(config)
    tool_sha256 = _package_content_sha256()
    identity_sha256 = _stage_identity(
        b"itchlab-dataset-v1",
        [parent.manifest_sha256 for parent in parents],
        hashes.identity_config_sha256,
        tool_sha256,
    )
    dataset_root = _safe_dataset_root(base, parents)
    prepared = _prepare_run(
        dataset_root,
        identity_sha256,
        force_new_run,
        config,
        parents,
        tool_sha256,
        cancellation,
    )
    if isinstance(prepared, DatasetResult):
        return prepared
    paths = prepared
    started_at_ns = time.time_ns()
    schema = dataset_schema(config.features, config.labels)
    summaries: list[_PartitionSummary] = []
    output_artefacts: list[_OutputArtefact] = []
    rows_processed = 0
    output_bytes = 0
    ordered_dates = [
        date.fromisoformat(value)
        for values in (
            config.partitions.train_dates,
            config.partitions.validation_dates,
            config.partitions.test_dates,
        )
        for value in values
    ]
    try:
        for trading_date in ordered_dates:
            parent, replay_context = by_date[trading_date]
            symbol_ids = dict(replay_context.instruments)
            for symbol in config.symbols:
                _check_cancel(cancellation)
                context = FeaturePartitionContext(
                    trading_date=trading_date,
                    symbol=symbol,
                    symbol_id=symbol_ids[symbol],
                    tick_size4=dict(config.tick_size4_by_symbol)[symbol],
                    session_start_ns=replay_context.session_start_ns,
                    session_end_ns=replay_context.session_end_ns,
                )
                feature_batches = build_feature_batches(
                    _input_batches(parent, "events", trading_date, symbol, cancellation),
                    _input_batches(parent, "snapshots", trading_date, symbol, cancellation),
                    config.features,
                    context,
                )
                label_batches = build_label_batches(
                    _input_batches(parent, "snapshots", trading_date, symbol, cancellation),
                    config.labels,
                    context,
                )
                join_counts = PartitionJoinCounts()
                joined = join_feature_label_batches(
                    feature_batches,
                    label_batches,
                    config.features,
                    config.labels,
                    config.sampling,
                    config.partitions,
                    trading_date,
                    join_counts,
                )
                partition = requested_dates[trading_date]
                artefact = _write_partition(
                    joined,
                    schema,
                    paths.staging_directory,
                    partition,
                    trading_date,
                    symbol,
                    cancellation,
                )
                summaries.append(
                    _PartitionSummary(
                        partition=partition,
                        trading_date=trading_date.isoformat(),
                        symbol=symbol,
                        counts=join_counts,
                    )
                )
                rows_processed += join_counts.qualifying_rows
                if artefact is not None:
                    output_artefacts.append(artefact)
                    output_bytes += artefact.size_bytes
                if progress is not None:
                    progress(
                        DatasetProgress(
                            stage="build",
                            partitions_completed=len(summaries),
                            rows_processed=rows_processed,
                            parquet_files=len(output_artefacts),
                            output_bytes=output_bytes,
                        )
                    )
        counts = _counts_document(summaries)
        _validate_counts_invariants(counts)
        if any(
            cast(int, item["rows"]["retained_rows"]) == 0
            for item in cast(list[dict[str, Any]], counts["by_partition"])
        ):
            raise _fail(
                ErrorCode.EMPTY_DATASET, "Every frozen partition must retain at least one row."
            )
        if any(cast(int, counts["classes"][name]) == 0 for name in _CLASS_VALUE):
            raise _fail(ErrorCode.EMPTY_DATASET, "The primary label must retain all three classes.")
        feature_support = _write_supporting_json(
            paths.staging_directory,
            "feature_catalogue",
            "feature-catalogue.json",
            feature_catalogue_document(config.features),
        )
        quality_document = {
            "schema_version": 1,
            "filter_order": [
                "history_complete",
                "primary_label_available",
                "qualifying_ordinal_mod_row_stride",
            ],
            "counts": counts,
        }
        quality_support = _write_supporting_json(
            paths.staging_directory,
            "data_quality",
            "data-quality.json",
            quality_document,
        )
        _recheck_inputs(parents)
        completed_at_ns = time.time_ns()
        document = _build_manifest(
            config,
            parents,
            identity_sha256,
            tool_sha256,
            paths.final_directory.name,
            started_at_ns,
            completed_at_ns,
            schema,
            counts,
            output_artefacts,
            (feature_support, quality_support),
        )
        _validate_manifest_document(document, "dataset")
        _publish(paths, document)
    except DatasetBuildError as error:
        if error.partial_exists:
            raise
        raise _fail(error.code, error.message, partial_exists=True) from error
    except (FeatureComputationError, LabelComputationError) as error:
        raise _fail(error.code, error.message, partial_exists=True) from error
    except (OSError, pa.ArrowException) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Dataset construction failed while writing staged output.",
            partial_exists=True,
        ) from error

    partition_rows = {
        cast(str, item["partition"]): cast(int, item["rows"]["retained_rows"])
        for item in cast(list[dict[str, Any]], counts["by_partition"])
    }
    class_counts = cast(dict[str, int], counts["classes"])
    return DatasetResult(
        dataset_id=paths.final_directory.name,
        status="completed",
        manifest_path=paths.final_directory / _MANIFEST_NAME,
        retained_rows=cast(int, counts["rows"]["retained_rows"]),
        parquet_files=len(output_artefacts),
        parent_conversion_ids=tuple(parent.conversion_id for parent in parents),
        partition_rows=tuple(
            (name, partition_rows[name]) for name in ("train", "validation", "test")
        ),
        class_counts=tuple((name, class_counts[name]) for name in ("down", "flat", "up")),
        reused=False,
    )


__all__ = ["build_dataset"]
