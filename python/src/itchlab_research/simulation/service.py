"""Authenticated inputs and immutable publication for conservative simulations."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, TypeAlias, cast

import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from itchlab_research import __version__
from itchlab_research.canonical_json import canonical_json_bytes, config_document, config_hashes
from itchlab_research.config import DatasetConfig, SimulationConfig, parse_config
from itchlab_research.errors import (
    ConfigValidationError,
    ErrorCode,
    ModelTrainingError,
    ReportGenerationError,
    SimulationError,
)
from itchlab_research.models import (
    AuthenticatedExperiment,
    PartitionedDataset,
    load_completed_dataset,
    load_completed_experiment,
)
from itchlab_research.reporting.service import load_authenticated_lineage
from itchlab_research.simulation.models import (
    REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE,
    AuthenticatedSimulation,
    ExecutionScenario,
    ScenarioResult,
    SimulationDayInput,
    SimulationResult,
    SimulationSymbol,
    StrategyName,
    required_scenarios,
)
from itchlab_research.simulation.runner import run_scenario
from itchlab_research.strategies import (
    SIGNAL_SELECTION_LATENCY_NS,
    SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE,
    SIGNAL_WEIGHT_CANDIDATES,
    CausalIntensityCalibrator,
    IntensityBucket,
    IntensityCalibration,
    ModelValidationMetric,
    PredictionKey,
    PredictionModelName,
    SignalPrediction,
    ValidationSignalPnl,
    select_signal_model,
    select_signal_weight,
)

_SCHEMA_VERSION: Final = 1
_RUN_ROOT: Final = Path("runs") / "simulation"
_MANIFEST_NAME: Final = "simulation-manifest.json"
_IDENTITY_MARKER: Final = "identity.sha256"
_MAX_JSON_BYTES: Final = 64 << 20
_HASH_CHUNK_BYTES: Final = 1 << 20
_ROW_GROUP_ROWS: Final = 65_536
_RUN_ID_PATTERN: Final = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-[0-9a-f]{12}$")

CancelCheck: TypeAlias = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class _Artefact:
    kind: str
    path: str
    sha256: str
    size_bytes: int
    row_count: int


@dataclass(frozen=True, slots=True)
class _RunPaths:
    lock_path: Path
    staging_directory: Path
    final_directory: Path


def _fail(code: ErrorCode, message: str, *, partial_exists: bool = False) -> SimulationError:
    return SimulationError(code, message, partial_exists=partial_exists)


def _check_cancel(cancel_requested: CancelCheck, *, partial_exists: bool = False) -> None:
    if cancel_requested():
        raise _fail(
            ErrorCode.CANCELLED,
            "Simulation was cancelled at a safe boundary.",
            partial_exists=partial_exists,
        )


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
    parts = normalised.split("/")
    return bool(
        normalised
        and not normalised.startswith("/")
        and not (len(normalised) >= 2 and normalised[1] == ":")
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} and not part.endswith(".partial") for part in parts)
    )


def _sha256_file(path: Path, cancel_requested: CancelCheck) -> tuple[str, int]:
    if _path_has_symlink(path):
        raise _fail(ErrorCode.INPUT_PATH, "A symlinked simulation input is not accepted.")
    digest = hashlib.sha256()
    observed = 0
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A simulation input is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _fail(ErrorCode.INPUT_PATH, "A simulation input is not a regular file.")
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                _check_cancel(cancel_requested)
                digest.update(chunk)
                observed += len(chunk)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(
                ErrorCode.HASH_MISMATCH, "A simulation input changed while hashing."
            ) from error
    if (
        observed != before.st_size
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "A simulation input changed while hashing.")
    return digest.hexdigest(), observed


def _strict_json(content: bytes, description: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        document = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{description} is not strict JSON/I-JSON."
        ) from error
    if not isinstance(document, dict):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{description} root is not an object.")
    return cast(dict[str, Any], document)


@lru_cache(maxsize=1)
def _manifest_validator() -> Draft202012Validator:
    schema_names = ("simulation-config.schema.json", "simulation-manifest.schema.json")
    documents: dict[str, dict[str, Any]] = {}
    resources: list[tuple[str, Resource[Any]]] = []
    for name in schema_names:
        document = cast(
            dict[str, Any],
            json.loads(files("itchlab_research._schemas").joinpath(name).read_text("utf-8")),
        )
        documents[name] = document
        resources.append((cast(str, document["$id"]), Resource.from_contents(document)))
    return Draft202012Validator(
        documents["simulation-manifest.schema.json"],
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _validate_manifest(document: Mapping[str, Any]) -> None:
    if list(_manifest_validator().iter_errors(document)):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Simulation manifest violates schema v1.")


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


def _stage_identity(
    parent_hashes: Sequence[str], identity_config_sha256: str, tool_sha256: str
) -> str:
    try:
        digest = hashlib.sha256(b"itchlab-simulation-v1\0")
        for value in parent_hashes:
            digest.update(bytes.fromhex(value))
        digest.update(bytes.fromhex(identity_config_sha256))
        digest.update(bytes.fromhex(tool_sha256))
    except ValueError as error:
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation identity input is invalid.") from error
    digest.update(_SCHEMA_VERSION.to_bytes(2, "big"))
    return digest.hexdigest()


def _timestamp(value_ns: int) -> str:
    seconds, nanoseconds = divmod(value_ns, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{prefix}.{nanoseconds:09d}Z"


def _run_id(identity_sha256: str, now_ns: int) -> str:
    return f"{_timestamp(now_ns).replace('-', '').replace(':', '')}-{identity_sha256[:12]}"


def _schema_descriptor(schema: pa.Schema) -> dict[str, Any]:
    fields_value = [
        {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
    return {
        "fields": fields_value,
        "sha256": hashlib.sha256(canonical_json_bytes(fields_value)).hexdigest(),
    }


def orders_schema() -> pa.Schema:
    """Return the exact version-1 final simulated-order output schema."""
    return pa.schema(
        cast(
            Any,
            [
                pa.field("scenario_id", pa.string(), nullable=False),
                pa.field("strategy_name", pa.string(), nullable=False),
                pa.field("trading_date", pa.date32(), nullable=False),
                pa.field("simulated_order_id", pa.uint64(), nullable=False),
                pa.field("decision_message_index", pa.uint64(), nullable=False),
                pa.field("prediction_message_index", pa.uint64(), nullable=True),
                pa.field("requested_timestamp_ns", pa.uint64(), nullable=False),
                pa.field("effective_timestamp_ns", pa.uint64(), nullable=False),
                pa.field("symbol_id", pa.uint16(), nullable=False),
                pa.field("side", pa.int8(), nullable=False),
                pa.field("price4", pa.uint32(), nullable=False),
                pa.field("original_quantity", pa.uint64(), nullable=False),
                pa.field("remaining_quantity", pa.uint64(), nullable=False),
                pa.field("queue_ahead_initial", pa.uint64(), nullable=True),
                pa.field("state", pa.string(), nullable=False),
                pa.field("cancel_requested_ns", pa.uint64(), nullable=True),
                pa.field("terminal_timestamp_ns", pa.uint64(), nullable=True),
                pa.field("rejection_reason", pa.string(), nullable=True),
            ],
        )
    )


def fills_schema() -> pa.Schema:
    """Return the exact version-1 passive-fill output schema."""
    return pa.schema(
        cast(
            Any,
            [
                pa.field("scenario_id", pa.string(), nullable=False),
                pa.field("strategy_name", pa.string(), nullable=False),
                pa.field("trading_date", pa.date32(), nullable=False),
                pa.field("fill_id", pa.uint64(), nullable=False),
                pa.field("simulated_order_id", pa.uint64(), nullable=False),
                pa.field("market_message_index", pa.uint64(), nullable=False),
                pa.field("timestamp_ns", pa.uint64(), nullable=False),
                pa.field("symbol_id", pa.uint16(), nullable=False),
                pa.field("side", pa.int8(), nullable=False),
                pa.field("price4", pa.uint32(), nullable=False),
                pa.field("quantity", pa.uint64(), nullable=False),
                pa.field("fee_microusd", pa.int64(), nullable=False),
                pa.field("cash_delta_microusd", pa.int64(), nullable=False),
                pa.field("inventory_after", pa.int64(), nullable=False),
                pa.field("fill_mid2", pa.uint64(), nullable=False),
                pa.field("future_mid2", pa.uint64(), nullable=True),
                pa.field("adverse_selection_100ms_microusd", pa.int64(), nullable=True),
            ],
        )
    )


def liquidations_schema() -> pa.Schema:
    """Return the exact version-1 terminal-liquidation output schema."""
    return pa.schema(
        cast(
            Any,
            [
                pa.field("scenario_id", pa.string(), nullable=False),
                pa.field("strategy_name", pa.string(), nullable=False),
                pa.field("trading_date", pa.date32(), nullable=False),
                pa.field("liquidation_id", pa.uint64(), nullable=False),
                pa.field("timestamp_ns", pa.uint64(), nullable=False),
                pa.field("symbol_id", pa.uint16(), nullable=False),
                pa.field("side", pa.int8(), nullable=False),
                pa.field("price4", pa.uint32(), nullable=False),
                pa.field("quantity", pa.uint64(), nullable=False),
                pa.field("fee_microusd", pa.int64(), nullable=False),
                pa.field("cash_delta_microusd", pa.int64(), nullable=False),
                pa.field("inventory_before", pa.int64(), nullable=False),
                pa.field("inventory_after", pa.int64(), nullable=False),
                pa.field("mark_mid2", pa.uint64(), nullable=False),
                pa.field("slippage_microusd", pa.int64(), nullable=False),
            ],
        )
    )


def equity_schema() -> pa.Schema:
    """Return the exact version-1 marked-equity output schema."""
    return pa.schema(
        cast(
            Any,
            [
                pa.field("scenario_id", pa.string(), nullable=False),
                pa.field("strategy_name", pa.string(), nullable=False),
                pa.field("trading_date", pa.date32(), nullable=False),
                pa.field("message_index", pa.uint64(), nullable=True),
                pa.field("timestamp_ns", pa.uint64(), nullable=False),
                pa.field("marked_pnl_microusd", pa.int64(), nullable=False),
                pa.field("cash_microusd", pa.int64(), nullable=False),
                pa.field("marked_inventory_value_microusd", pa.int64(), nullable=False),
            ],
        )
    )


_OUTPUT_SCHEMAS: Final[dict[str, pa.Schema]] = {
    "orders": orders_schema(),
    "fills": fills_schema(),
    "liquidations": liquidations_schema(),
    "equity": equity_schema(),
}


def _read_conversion_rows(
    base: Path,
    lineage: Sequence[Any],
    dataset_config: DatasetConfig,
    cancel_requested: CancelCheck,
    *,
    selected_dates: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    declared_dates = {
        *dataset_config.partitions.train_dates,
        *dataset_config.partitions.validation_dates,
        *dataset_config.partitions.test_dates,
    }
    if not selected_dates or not selected_dates <= declared_dates:
        raise _fail(ErrorCode.PARTITION, "Simulation date selection is invalid.")
    allowed_dates = selected_dates
    allowed_symbols = set(dataset_config.symbols)
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for parent in lineage:
        directory = (base / parent.locator).parent
        for entry in cast(list[dict[str, Any]], parent.document["artefacts"]):
            _check_cancel(cancel_requested)
            trading_date = cast(str, entry["trading_date"])
            symbol = cast(str, entry["symbol"])
            kind = cast(str, entry["kind"])
            if trading_date not in allowed_dates or symbol not in allowed_symbols:
                continue
            relative = cast(str, entry["path"])
            if not _safe_relative_path(relative):
                raise _fail(ErrorCode.INPUT_PATH, "Conversion names an unsafe Parquet path.")
            key = (kind, trading_date, symbol)
            if key in seen:
                raise _fail(ErrorCode.HASH_MISMATCH, "Conversion partitions overlap.")
            seen.add(key)
            path = directory / relative
            digest, size = _sha256_file(path, cancel_requested)
            if digest != entry["sha256"] or size != entry["size_bytes"]:
                raise _fail(ErrorCode.HASH_MISMATCH, "Conversion Parquet hash is inconsistent.")
            try:
                table = pq.ParquetFile(path).read(use_threads=False)
                if table.num_rows != entry["row_count"]:
                    raise _fail(
                        ErrorCode.HASH_MISMATCH, "Conversion Parquet count is inconsistent."
                    )
                rows = table.to_pylist()
            except SimulationError:
                raise
            except (OSError, pa.ArrowException, MemoryError) as error:
                raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion Parquet is invalid.") from error
            for row in rows:
                row["trading_date"] = date.fromisoformat(trading_date)
                row["symbol"] = symbol
            (events if kind == "events" else snapshots)[trading_date].extend(rows)
    expected = {(day, symbol) for day in allowed_dates for symbol in allowed_symbols}
    for source, name in ((events, "event"), (snapshots, "snapshot")):
        available = {
            (day, cast(str, row["symbol"])) for day, rows in source.items() for row in rows if rows
        }
        if not expected <= available:
            raise _fail(ErrorCode.PARTITION, f"Simulation {name} partitions are incomplete.")
    return events, snapshots


def _dataset_config(dataset: PartitionedDataset) -> DatasetConfig:
    try:
        parsed = parse_config(json.dumps(dataset.manifest["config"]), "dataset")
    except (ConfigValidationError, KeyError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset simulation config is invalid.") from error
    if not isinstance(parsed, DatasetConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Dataset simulation config type is invalid.")
    return parsed


def _session_windows(replays: Sequence[Any]) -> dict[str, tuple[int, int]]:
    windows: dict[str, tuple[int, int]] = {}
    for replay in replays:
        config = cast(dict[str, Any], replay.document["config"])
        trading_date = cast(str, cast(dict[str, Any], config["input"])["trading_date"])
        selection = cast(dict[str, Any], config["selection"])
        window = (
            cast(int, selection["session_start_ns"]),
            cast(int, selection["session_end_ns"]),
        )
        if trading_date in windows and windows[trading_date] != window:
            raise _fail(ErrorCode.HASH_MISMATCH, "Replay session windows are contradictory.")
        windows[trading_date] = window
    return windows


def _build_days(
    dataset_config: DatasetConfig,
    events: Mapping[str, list[dict[str, Any]]],
    snapshots: Mapping[str, list[dict[str, Any]]],
    session_windows: Mapping[str, tuple[int, int]],
) -> dict[str, SimulationDayInput]:
    tick_sizes = dict(dataset_config.tick_size4_by_symbol)
    result: dict[str, SimulationDayInput] = {}
    for trading_date in sorted(events):
        event_rows = sorted(events[trading_date], key=lambda row: cast(int, row["message_index"]))
        snapshot_rows = sorted(
            snapshots[trading_date], key=lambda row: cast(int, row["message_index"])
        )
        symbol_ids: dict[str, int] = {}
        for row in event_rows:
            symbol = cast(str, row["symbol"])
            symbol_id = cast(int, row["symbol_id"])
            previous = symbol_ids.setdefault(symbol, symbol_id)
            if previous != symbol_id:
                raise _fail(ErrorCode.HASH_MISMATCH, "Symbol IDs change within a session.")
        if set(symbol_ids) != set(dataset_config.symbols):
            raise _fail(ErrorCode.UNKNOWN_SYMBOL, "Simulation symbols are incomplete.")
        try:
            session_start_ns, session_end_ns = session_windows[trading_date]
        except KeyError as error:
            raise _fail(
                ErrorCode.PARTITION, "Simulation day has no replay session window."
            ) from error
        result[trading_date] = SimulationDayInput(
            trading_date=date.fromisoformat(trading_date),
            session_start_ns=session_start_ns,
            session_end_ns=session_end_ns,
            symbols=tuple(
                SimulationSymbol(symbol, symbol_ids[symbol], tick_sizes[symbol])
                for symbol in sorted(symbol_ids)
            ),
            events=tuple(event_rows),
            snapshots=tuple(snapshot_rows),
        )
    return result


def _visible_levels(snapshot: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    levels: list[tuple[int, int]] = []
    index = 1
    while f"bid_price4_{index}" in snapshot:
        for side, name in ((1, "bid"), (-1, "ask")):
            price = snapshot.get(f"{name}_price4_{index}")
            if price is not None:
                levels.append((side, cast(int, price)))
        index += 1
    return tuple(levels)


def _record_exposure(
    calibrator: CausalIntensityCalibrator,
    day: SimulationDayInput,
    snapshot: Mapping[str, Any],
    elapsed_ns: int,
) -> None:
    if elapsed_ns <= 0:
        return
    symbol = cast(str, snapshot["symbol"])
    symbol_definition = next(item for item in day.symbols if item.symbol == symbol)
    best_bid = cast(int | None, snapshot.get("bid_price4_1"))
    best_ask = cast(int | None, snapshot.get("ask_price4_1"))
    if best_bid is None or best_ask is None:
        return
    seen_prices: set[tuple[int, int]] = set()
    for side, price in _visible_levels(snapshot):
        if (side, price) in seen_prices:
            continue
        seen_prices.add((side, price))
        delta = best_bid - price if side == 1 else price - best_ask
        if delta < 0 or delta % symbol_definition.tick_size4:
            continue
        distance = delta // symbol_definition.tick_size4
        if 0 <= distance <= 10:
            calibrator.record_exposure(day.trading_date, symbol, distance, elapsed_ns)


def _calibrate(
    days: Sequence[SimulationDayInput], dataset_config: DatasetConfig
) -> IntensityCalibration:
    training_dates = tuple(
        date.fromisoformat(value) for value in dataset_config.partitions.train_dates
    )
    calibrator = CausalIntensityCalibrator(
        symbols=dataset_config.symbols,
        training_dates=training_dates,
    )
    for day in days:
        if day.trading_date not in training_dates:
            continue
        snapshots_by_key = {
            (cast(str, row["symbol"]), cast(int, row["message_index"])): row
            for row in day.snapshots
        }
        events_by_symbol: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in day.events:
            events_by_symbol[cast(str, row["symbol"])].append(row)
        for symbol, event_rows in events_by_symbol.items():
            definition = next(item for item in day.symbols if item.symbol == symbol)
            current: Mapping[str, Any] | None = None
            current_timestamp = day.session_start_ns
            for event in event_rows:
                timestamp_ns = cast(int, event["timestamp_ns"])
                if current is not None:
                    _record_exposure(calibrator, day, current, timestamp_ns - current_timestamp)
                if current is not None and event["event_kind"] in {"execute", "execute_price"}:
                    side = cast(int, event["side"])
                    price = cast(int, event["price4"])
                    best = cast(
                        int | None,
                        current.get("bid_price4_1" if side == 1 else "ask_price4_1"),
                    )
                    if best is not None:
                        delta = best - price if side == 1 else price - best
                        if delta >= 0 and delta % definition.tick_size4 == 0:
                            distance = delta // definition.tick_size4
                            if 0 <= distance <= 10:
                                calibrator.record_execution(day.trading_date, symbol, distance)
                updated = snapshots_by_key.get((symbol, cast(int, event["message_index"])))
                if updated is not None:
                    current = updated
                current_timestamp = timestamp_ns
            if current is not None:
                _record_exposure(
                    calibrator,
                    day,
                    current,
                    max(0, day.session_end_ns - current_timestamp),
                )
    return calibrator.finalise()


def _selected_model(
    experiment: AuthenticatedExperiment,
) -> tuple[PredictionModelName, dict[str, Any]]:
    evaluations = tuple(
        ModelValidationMetric(
            partition="validation",
            model_name=cast(PredictionModelName, item["model_name"]),
            multiclass_log_loss=float(cast(dict[str, Any], item["metrics"])["multiclass_log_loss"]),
        )
        for item in cast(list[dict[str, Any]], experiment.validation_metrics["models"])
    )
    selection = select_signal_model(evaluations)
    return selection.model_name, {
        "model_name": selection.model_name,
        "validation_log_loss": selection.validation_log_loss,
        "evaluations": [asdict(item) for item in selection.evaluations],
    }


def _calibration_document(calibration: IntensityCalibration) -> dict[str, Any]:
    """Serialise the exact training-only intensity evidence retained by the run."""

    def buckets_document(buckets: Sequence[IntensityBucket]) -> list[dict[str, Any]]:
        return [asdict(bucket) for bucket in buckets]

    return {
        "training_dates": [value.isoformat() for value in calibration.training_dates],
        "pooled": {
            "kappa": calibration.pooled_kappa,
            "intercept": calibration.pooled_intercept,
            "buckets": buckets_document(calibration.pooled_buckets),
        },
        "symbols": [
            {
                "symbol": item.symbol,
                "kappa": item.kappa,
                "intercept": item.intercept,
                "source": item.source.value,
                "buckets": buckets_document(item.buckets),
            }
            for item in calibration.symbols
        ],
    }


def _load_predictions(
    experiment: AuthenticatedExperiment,
    days: Mapping[str, SimulationDayInput],
    model_name: PredictionModelName,
    cancel_requested: CancelCheck,
) -> dict[str, tuple[SignalPrediction, ...]]:
    entry = next(
        item
        for item in cast(list[dict[str, Any]], experiment.manifest["artefacts"])
        if item["kind"] == "predictions"
    )
    path = experiment.manifest_path.parent / cast(str, entry["path"])
    digest, size = _sha256_file(path, cancel_requested)
    if digest != entry["sha256"] or size != entry["size_bytes"]:
        raise _fail(ErrorCode.HASH_MISMATCH, "Prediction artefact changed after authentication.")
    timestamps = {
        (
            day.trading_date,
            cast(int, row["symbol_id"]),
            cast(int, row["message_index"]),
        ): cast(int, row["timestamp_ns"])
        for day in days.values()
        for row in day.snapshots
    }
    result: dict[str, list[SignalPrediction]] = defaultdict(list)
    selected_dates = [day.trading_date for day in days.values()]
    if not selected_dates:
        raise _fail(ErrorCode.PARTITION, "Prediction selection has no authorised dates.")
    try:
        scanner = pads.dataset(path, format="parquet").scanner(
            batch_size=_ROW_GROUP_ROWS,
            columns=[
                "experiment_id",
                "trading_date",
                "symbol_id",
                "message_index",
                "score",
                "model_name",
            ],
            filter=(
                pads.field("trading_date").isin(selected_dates)
                & (pads.field("model_name") == model_name)
            ),
            use_threads=False,
        )
        for batch in scanner.to_batches():
            _check_cancel(cancel_requested)
            for row in batch.to_pylist():
                trading_date = cast(date, row["trading_date"])
                key = (trading_date, cast(int, row["symbol_id"]), cast(int, row["message_index"]))
                try:
                    timestamp_ns = timestamps[key]
                except KeyError as error:
                    raise _fail(
                        ErrorCode.PREDICTION_KEY,
                        "Prediction key is absent from conversion snapshots.",
                    ) from error
                result[trading_date.isoformat()].append(
                    SignalPrediction(
                        key=PredictionKey(
                            experiment_id=experiment.experiment_id,
                            trading_date=trading_date,
                            symbol_id=key[1],
                            message_index=key[2],
                            model_name=model_name,
                        ),
                        timestamp_ns=timestamp_ns,
                        score=float(row["score"]),
                    )
                )
    except SimulationError:
        raise
    except (OSError, pa.ArrowException, MemoryError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.PREDICTION_KEY, "Prediction rows could not be loaded.") from error
    return {
        day: tuple(sorted(values, key=lambda item: (item.key.symbol_id, item.key.message_index)))
        for day, values in result.items()
    }


def _with_predictions(
    day: SimulationDayInput, predictions: Mapping[str, tuple[SignalPrediction, ...]]
) -> SimulationDayInput:
    return SimulationDayInput(
        trading_date=day.trading_date,
        session_start_ns=day.session_start_ns,
        session_end_ns=day.session_end_ns,
        symbols=day.symbols,
        events=day.events,
        snapshots=day.snapshots,
        predictions=predictions.get(day.trading_date.isoformat(), ()),
    )


def _execution_scenarios(config: SimulationConfig) -> tuple[ExecutionScenario, ...]:
    scenarios = list(required_scenarios())
    configured = ExecutionScenario(
        scenario_id=(
            f"configured-latency-{config.execution.submission_latency_ns}-"
            f"cancel-{config.execution.cancellation_latency_ns}-"
            f"maker-{config.execution.maker_fee_microusd_per_share}-"
            f"taker-{config.execution.taker_fee_microusd_per_share}"
        ),
        submission_latency_ns=config.execution.submission_latency_ns,
        cancellation_latency_ns=config.execution.cancellation_latency_ns,
        maker_fee_microusd_per_share=config.execution.maker_fee_microusd_per_share,
        taker_fee_microusd_per_share=config.execution.taker_fee_microusd_per_share,
    )

    def economic_key(value: ExecutionScenario) -> tuple[int, int, int, int]:
        return (
            value.submission_latency_ns,
            value.cancellation_latency_ns,
            value.maker_fee_microusd_per_share,
            value.taker_fee_microusd_per_share,
        )

    if economic_key(configured) not in {economic_key(item) for item in scenarios}:
        scenarios.append(configured)
    return tuple(scenarios)


def _select_signal_weight(
    config: SimulationConfig,
    dataset_config: DatasetConfig,
    days: Mapping[str, SimulationDayInput],
    calibration: IntensityCalibration,
    experiment: AuthenticatedExperiment | None,
    predictions: Mapping[str, tuple[SignalPrediction, ...]],
    model_name: PredictionModelName | None,
    cancel_requested: CancelCheck,
) -> tuple[float, dict[str, Any]]:
    selection_document: dict[str, Any] = {
        "model": None,
        "signal_weight": None,
        "test_dates_accessed_after_selection": True,
    }
    selected_weight = 0.0
    if experiment is not None and model_name is not None:
        _, model_document = _selected_model(experiment)
        validation_days = tuple(
            _with_predictions(days[value], predictions)
            for value in dataset_config.partitions.validation_dates
        )
        validation_evidence: list[ValidationSignalPnl] = []
        fixed = ExecutionScenario(
            scenario_id="validation-selection",
            submission_latency_ns=SIGNAL_SELECTION_LATENCY_NS,
            cancellation_latency_ns=SIGNAL_SELECTION_LATENCY_NS,
            maker_fee_microusd_per_share=SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE,
            taker_fee_microusd_per_share=REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE,
        )
        for weight in SIGNAL_WEIGHT_CANDIDATES:
            _check_cancel(cancel_requested)
            result = run_scenario(
                validation_days,
                config,
                calibration,
                fixed,
                strategy_name="signal_adjusted_avellaneda_stoikov",
                signal_weight_ticks=weight,
                experiment_id=experiment.experiment_id,
                model_name=model_name,
            )
            validation_evidence.extend(
                ValidationSignalPnl(
                    partition="validation",
                    trading_date=date.fromisoformat(cast(str, item["trading_date"])),
                    signal_weight_ticks=weight,
                    submission_latency_ns=SIGNAL_SELECTION_LATENCY_NS,
                    cancellation_latency_ns=SIGNAL_SELECTION_LATENCY_NS,
                    maker_fee_microusd_per_share=(SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE),
                    net_pnl_microusd=cast(int, item["marked_pnl_microusd"]),
                )
                for item in result.daily_metrics
            )
        weight_selection = select_signal_weight(validation_evidence)
        selected_weight = weight_selection.signal_weight_ticks
        if (
            config.strategy.signal_weight_ticks is not None
            and float(config.strategy.signal_weight_ticks) != selected_weight
        ):
            raise _fail(
                ErrorCode.LEAKAGE_GUARD,
                "Configured signal weight does not match validation-only selection.",
            )
        selection_document = {
            "model": model_document,
            "signal_weight": {
                "selected": selected_weight,
                "fixed_submission_latency_ns": SIGNAL_SELECTION_LATENCY_NS,
                "fixed_cancellation_latency_ns": SIGNAL_SELECTION_LATENCY_NS,
                "fixed_maker_fee_microusd_per_share": (
                    SIGNAL_SELECTION_MAKER_FEE_MICROUSD_PER_SHARE
                ),
                "fixed_taker_fee_microusd_per_share": (REQUIRED_TAKER_FEE_MICROUSD_PER_SHARE),
                "evaluations": [asdict(item) for item in weight_selection.evaluations],
                "daily_evidence": [
                    {**asdict(item), "trading_date": item.trading_date.isoformat()}
                    for item in validation_evidence
                ],
            },
            "test_dates_accessed_after_selection": True,
        }

    return selected_weight, selection_document


def _run_test_scenarios(
    config: SimulationConfig,
    dataset_config: DatasetConfig,
    days: Mapping[str, SimulationDayInput],
    calibration: IntensityCalibration,
    experiment: AuthenticatedExperiment | None,
    predictions: Mapping[str, tuple[SignalPrediction, ...]],
    model_name: PredictionModelName | None,
    selected_weight: float,
    cancel_requested: CancelCheck,
) -> tuple[ScenarioResult, ...]:
    test_days = tuple(
        _with_predictions(days[value], predictions)
        for value in dataset_config.partitions.test_dates
    )

    strategies: tuple[StrategyName, ...] = (
        ("inventory_aware_avellaneda_stoikov", "signal_adjusted_avellaneda_stoikov")
        if experiment is not None
        else ("inventory_aware_avellaneda_stoikov",)
    )
    results: list[ScenarioResult] = []
    for scenario in _execution_scenarios(config):
        for strategy_name in strategies:
            _check_cancel(cancel_requested)
            results.append(
                run_scenario(
                    test_days,
                    config,
                    calibration,
                    scenario,
                    strategy_name=strategy_name,
                    signal_weight_ticks=(
                        selected_weight
                        if strategy_name == "signal_adjusted_avellaneda_stoikov"
                        else 0.0
                    ),
                    experiment_id=None if experiment is None else experiment.experiment_id,
                    model_name="prior" if model_name is None else model_name,
                )
            )
    return tuple(results)


def _write_parquet(
    directory: Path,
    kind: str,
    rows: Sequence[Mapping[str, Any]],
    cancel_requested: CancelCheck,
) -> _Artefact:
    schema = _OUTPUT_SCHEMAS[kind]
    filename = f"{kind}.parquet"
    partial = directory / f"{filename}.partial"
    final = directory / filename
    try:
        table = pa.Table.from_pylist(list(rows), schema=schema)
        pq.write_table(
            table,
            partial,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            row_group_size=_ROW_GROUP_ROWS,
        )
        partial.rename(final)
    except (OSError, pa.ArrowException, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            f"Simulation {kind} Parquet could not be written.",
            partial_exists=True,
        ) from error
    digest, size = _sha256_file(final, cancel_requested)
    try:
        parquet = pq.ParquetFile(final)
        if parquet.schema_arrow != schema or parquet.metadata.num_rows != len(rows):
            raise _fail(
                ErrorCode.INVARIANT,
                f"Written simulation {kind} Parquet is inconsistent.",
                partial_exists=True,
            )
    except (OSError, pa.ArrowException) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION,
            f"Written simulation {kind} Parquet is invalid.",
            partial_exists=True,
        ) from error
    return _Artefact(kind, filename, digest, size, len(rows))


def _write_json(
    directory: Path,
    kind: str,
    filename: str,
    document: Mapping[str, Any],
    row_count: int,
    cancel_requested: CancelCheck,
) -> _Artefact:
    partial = directory / f"{filename}.partial"
    final = directory / filename
    try:
        content = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(content) > _MAX_JSON_BYTES:
            raise _fail(ErrorCode.DISK_WRITE, "Simulation JSON artefact exceeds its size bound.")
        with partial.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(final)
    except SimulationError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Simulation JSON artefact could not be written.",
            partial_exists=True,
        ) from error
    digest, size = _sha256_file(final, cancel_requested)
    return _Artefact(kind, filename, digest, size, row_count)


def _parent_document(
    dataset: PartitionedDataset, experiment: AuthenticatedExperiment | None
) -> dict[str, Any]:
    return {
        "dataset": {
            "run_id": dataset.dataset_id,
            "manifest_sha256": dataset.manifest_sha256,
            "config_sha256": dataset.config_sha256,
            "identity_sha256": dataset.identity_sha256,
        },
        "experiment": (
            None
            if experiment is None
            else {
                "run_id": experiment.experiment_id,
                "manifest_sha256": experiment.manifest_sha256,
                "config_sha256": experiment.manifest["config_sha256"],
                "identity_sha256": experiment.manifest["identity_sha256"],
            }
        ),
    }


def _artefact_document(item: _Artefact) -> dict[str, Any]:
    return asdict(item)


def _remove_lock(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Simulation identity lock could not be removed."
        ) from error


def _safe_root(base: Path) -> Path:
    root = base / _RUN_ROOT
    if root.exists() and _path_has_symlink(root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Simulation output root may not contain symlinks.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve(strict=True)
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Simulation output root could not be prepared."
        ) from error


def _result_from_authenticated(value: AuthenticatedSimulation, *, reused: bool) -> SimulationResult:
    scenarios = cast(list[dict[str, Any]], value.metrics["scenarios"])
    return SimulationResult(
        simulation_id=value.simulation_id,
        status="completed",
        manifest_path=value.manifest_path,
        experiment_id=(
            None
            if value.manifest["parents"]["experiment"] is None
            else cast(str, value.manifest["parents"]["experiment"]["run_id"])
        ),
        scenario_count=len({cast(str, item["scenario_id"]) for item in scenarios}),
        strategy_count=len({cast(str, item["strategy_name"]) for item in scenarios}),
        order_rows=next(
            cast(int, item["row_count"])
            for item in value.manifest["artefacts"]
            if item["kind"] == "orders"
        ),
        fill_rows=next(
            cast(int, item["row_count"])
            for item in value.manifest["artefacts"]
            if item["kind"] == "fills"
        ),
        warnings=tuple(cast(list[str], value.manifest["warnings"])),
        reused=reused,
    )


def load_completed_simulation(
    simulation_id: str,
    *,
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> AuthenticatedSimulation:
    """Authenticate a completed simulation manifest and every published child."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    if not isinstance(simulation_id, str) or _RUN_ID_PATTERN.fullmatch(simulation_id) is None:
        raise _fail(ErrorCode.INPUT_PATH, "Simulation run ID is invalid.")
    directory = base / _RUN_ROOT / simulation_id
    manifest_path = directory / _MANIFEST_NAME
    try:
        content = manifest_path.read_bytes()
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "Simulation manifest is not readable.") from error
    if len(content) > _MAX_JSON_BYTES or _path_has_symlink(manifest_path):
        raise _fail(ErrorCode.INPUT_PATH, "Simulation manifest is not a bounded regular file.")
    document = _strict_json(content, "Simulation manifest")
    _validate_manifest(document)
    if document["simulation_id"] != simulation_id or directory.name != simulation_id:
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation manifest identity is inconsistent.")
    try:
        parsed_config = parse_config(
            json.dumps(document["config"], ensure_ascii=False, allow_nan=False),
            "simulation",
        )
    except (ConfigValidationError, KeyError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Simulation manifest config is invalid.") from error
    if not isinstance(parsed_config, SimulationConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Simulation manifest config type is invalid.")
    hashes = config_hashes(parsed_config)
    try:
        dataset = load_completed_dataset(
            parsed_config.dataset_manifest,
            base_directory=base,
            cancel_requested=cancellation,
        )
    except ModelTrainingError as error:
        raise _fail(error.code, error.message) from error
    dataset_parent = cast(dict[str, Any], document["parents"]["dataset"])
    expected_dataset_parent = {
        "run_id": dataset.dataset_id,
        "manifest_sha256": dataset.manifest_sha256,
        "config_sha256": dataset.config_sha256,
        "identity_sha256": dataset.identity_sha256,
    }
    experiment_parent = cast(dict[str, Any] | None, document["parents"]["experiment"])
    experiment: AuthenticatedExperiment | None = None
    if parsed_config.prediction_manifest is not None:
        try:
            experiment = load_completed_experiment(
                Path(parsed_config.prediction_manifest).parent.name,
                base_directory=base,
                cancel_requested=cancellation,
            )
        except ModelTrainingError as error:
            raise _fail(error.code, error.message) from error
    expected_experiment_parent = (
        None
        if experiment is None
        else {
            "run_id": experiment.experiment_id,
            "manifest_sha256": experiment.manifest_sha256,
            "config_sha256": experiment.manifest["config_sha256"],
            "identity_sha256": experiment.manifest["identity_sha256"],
        }
    )
    parent_hashes = [dataset.manifest_sha256]
    if experiment is not None:
        parent_hashes.append(experiment.manifest_sha256)
    tool = cast(dict[str, Any], document["tool"])
    expected_identity = _stage_identity(
        parent_hashes,
        hashes.identity_config_sha256,
        cast(str, tool["sha256"]),
    )
    if (
        document["config"] != config_document(parsed_config)
        or document["config_sha256"] != hashes.config_sha256
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["identity_sha256"] != expected_identity
        or dataset_parent != expected_dataset_parent
        or experiment_parent != expected_experiment_parent
        or document["schemas"]
        != {kind: _schema_descriptor(schema) for kind, schema in _OUTPUT_SCHEMAS.items()}
        or document["scenarios"] != [asdict(item) for item in _execution_scenarios(parsed_config)]
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation manifest lineage is inconsistent.")
    evidence: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for entry in cast(list[dict[str, Any]], document["artefacts"]):
        _check_cancel(cancellation)
        kind = cast(str, entry["kind"])
        relative = cast(str, entry["path"])
        if kind in seen or not _safe_relative_path(relative) or Path(relative).name != relative:
            raise _fail(ErrorCode.HASH_MISMATCH, "Simulation artefact set is invalid.")
        seen.add(kind)
        path = directory / relative
        digest, size = _sha256_file(path, cancellation)
        if digest != entry["sha256"] or size != entry["size_bytes"]:
            raise _fail(ErrorCode.HASH_MISMATCH, "Simulation artefact hash is inconsistent.")
        if kind in _OUTPUT_SCHEMAS:
            try:
                parquet = pq.ParquetFile(path)
                if (
                    parquet.schema_arrow != _OUTPUT_SCHEMAS[kind]
                    or parquet.metadata.num_rows != entry["row_count"]
                ):
                    raise _fail(ErrorCode.HASH_MISMATCH, "Simulation Parquet is inconsistent.")
            except (OSError, pa.ArrowException) as error:
                raise _fail(ErrorCode.SCHEMA_VERSION, "Simulation Parquet is invalid.") from error
        else:
            child = _strict_json(path.read_bytes(), f"Simulation {kind}")
            if child.get("simulation_id") != simulation_id:
                raise _fail(ErrorCode.HASH_MISMATCH, "Simulation JSON lineage is inconsistent.")
            if (
                kind == "metrics"
                and len(cast(list[Any], child.get("scenarios"))) != entry["row_count"]
            ):
                raise _fail(ErrorCode.HASH_MISMATCH, "Simulation metric count is inconsistent.")
            if (
                kind == "diagnostics"
                and len(cast(list[Any], child.get("records"))) != entry["row_count"]
            ):
                raise _fail(ErrorCode.HASH_MISMATCH, "Simulation diagnostic count is inconsistent.")
            evidence[kind] = child
    if seen != {"orders", "fills", "liquidations", "equity", "metrics", "diagnostics"}:
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation artefacts are incomplete.")
    dataset_config = _dataset_config(dataset)
    calibration_document = cast(dict[str, Any], document["calibration"])
    calibration_symbols = cast(list[dict[str, Any]], calibration_document["symbols"])
    all_bucket_sets = [
        cast(list[dict[str, Any]], cast(dict[str, Any], calibration_document["pooled"])["buckets"]),
        *(cast(list[dict[str, Any]], item["buckets"]) for item in calibration_symbols),
    ]
    if (
        calibration_document["training_dates"] != list(dataset_config.partitions.train_dates)
        or [cast(str, item["symbol"]) for item in calibration_symbols]
        != sorted(dataset_config.symbols)
        or any(
            [cast(int, bucket["distance_ticks"]) for bucket in buckets] != list(range(11))
            for buckets in all_bucket_sets
        )
        or evidence["metrics"].get("selection") != document["selection"]
        or evidence["diagnostics"].get("queue_anomaly_budget")
        != parsed_config.execution.max_queue_anomalies
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation evidence is internally inconsistent.")
    selection_document = cast(dict[str, Any], document["selection"])
    if experiment is None:
        if (
            selection_document["model"] is not None
            or selection_document["signal_weight"] is not None
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Baseline selection evidence is inconsistent.")
    else:
        _selected_model_name, expected_model_document = _selected_model(experiment)
        signal_weight_document = cast(dict[str, Any], selection_document["signal_weight"])
        if selection_document["model"] != expected_model_document or (
            parsed_config.strategy.signal_weight_ticks is not None
            and signal_weight_document["selected"] != parsed_config.strategy.signal_weight_ticks
        ):
            raise _fail(ErrorCode.HASH_MISMATCH, "Signal selection evidence is inconsistent.")
    expected_strategy_names = {"inventory_aware_avellaneda_stoikov"}
    if experiment is not None:
        expected_strategy_names.add("signal_adjusted_avellaneda_stoikov")
    metric_rows = cast(list[dict[str, Any]], evidence["metrics"]["scenarios"])
    expected_cells = {
        (scenario.scenario_id, strategy)
        for scenario in _execution_scenarios(parsed_config)
        for strategy in expected_strategy_names
    }
    if {
        (cast(str, item["scenario_id"]), cast(str, item["strategy_name"])) for item in metric_rows
    } != expected_cells:
        raise _fail(ErrorCode.HASH_MISMATCH, "Simulation scenario evidence is incomplete.")
    return AuthenticatedSimulation(
        simulation_id=simulation_id,
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(content).hexdigest(),
        manifest=document,
        metrics=evidence["metrics"],
        diagnostics=evidence["diagnostics"],
    )


def _prepare_run(
    root: Path,
    identity_sha256: str,
    force_new_run: bool,
    cancel_requested: CancelCheck,
) -> SimulationResult | _RunPaths:
    lock_path = root / f".{identity_sha256}.lock"
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise _fail(
            ErrorCode.RUN_EXISTS, "A simulation with this identity is already locked."
        ) from error
    except OSError as error:
        raise _fail(
            ErrorCode.OUTPUT_PATH, "Simulation identity lock could not be created."
        ) from error
    staging_created = False
    try:
        if not force_new_run:
            for directory in sorted(root.iterdir()):
                _check_cancel(cancel_requested)
                if not directory.is_dir() or directory == lock_path:
                    continue
                marker = directory / _IDENTITY_MARKER
                if directory.name.endswith(".partial"):
                    if marker.exists() and marker.read_text("ascii") == identity_sha256 + "\n":
                        raise _fail(ErrorCode.RUN_EXISTS, "A partial simulation already exists.")
                    continue
                manifest = directory / _MANIFEST_NAME
                if not manifest.exists():
                    continue
                candidate = _strict_json(manifest.read_bytes(), "Simulation manifest")
                if candidate.get("identity_sha256") != identity_sha256:
                    continue
                authenticated = load_completed_simulation(
                    directory.name,
                    base_directory=root.parents[1],
                    cancel_requested=cancel_requested,
                )
                _remove_lock(lock_path)
                return _result_from_authenticated(authenticated, reused=True)
        simulation_id = _run_id(identity_sha256, time.time_ns())
        final = root / simulation_id
        staging = root / f"{simulation_id}.partial"
        if final.exists() or staging.exists():
            raise _fail(ErrorCode.RUN_EXISTS, "Simulation run ID already exists.")
        staging.mkdir()
        staging_created = True
        (staging / _IDENTITY_MARKER).write_text(identity_sha256 + "\n", encoding="ascii")
        return _RunPaths(lock_path, staging, final)
    except SimulationError:
        if lock_path.exists() and not staging_created:
            _remove_lock(lock_path)
        raise
    except OSError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        raise _fail(ErrorCode.OUTPUT_PATH, "Simulation staging could not be prepared.") from error


def _publish(paths: _RunPaths, document: Mapping[str, Any]) -> None:
    partial = paths.staging_directory / f"{_MANIFEST_NAME}.partial"
    final = paths.staging_directory / _MANIFEST_NAME
    try:
        content = (
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        with partial.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        partial.rename(final)
        _remove_lock(paths.lock_path)
        paths.staging_directory.rename(paths.final_directory)
        (paths.final_directory / _IDENTITY_MARKER).unlink(missing_ok=True)
    except (OSError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Simulation could not be atomically published.",
            partial_exists=True,
        ) from error


def simulate(
    config: SimulationConfig,
    *,
    base_directory: Path | None = None,
    force_new_run: bool = False,
    cancel_requested: CancelCheck | None = None,
) -> SimulationResult:
    """Authenticate inputs, run the fixed grid, and atomically publish immutable evidence."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    try:
        parsed = parse_config(json.dumps(config_document(config)), "simulation")
    except ConfigValidationError as error:
        raise _fail(
            ErrorCode.CONFIG_SCHEMA, "Simulation config is not canonical version 1."
        ) from error
    if parsed != config:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "Simulation config is not canonical version 1.")
    _check_cancel(cancellation)

    experiment: AuthenticatedExperiment | None = None
    try:
        if config.prediction_manifest is None:
            dataset = load_completed_dataset(
                config.dataset_manifest,
                base_directory=base,
                cancel_requested=cancellation,
            )
        else:
            experiment_id = Path(config.prediction_manifest).parent.name
            experiment = load_completed_experiment(
                experiment_id,
                base_directory=base,
                cancel_requested=cancellation,
            )
            dataset = experiment.dataset
            if experiment.manifest_path.relative_to(base).as_posix() != config.prediction_manifest:
                raise _fail(ErrorCode.INPUT_PATH, "Prediction manifest locator is inconsistent.")
        if dataset.manifest_path.relative_to(base).as_posix() != config.dataset_manifest:
            raise _fail(ErrorCode.INPUT_PATH, "Dataset manifest locator is inconsistent.")
    except ModelTrainingError as error:
        raise _fail(error.code, error.message) from error

    dataset_config = _dataset_config(dataset)
    try:
        conversions, replays = load_authenticated_lineage(
            SimpleNamespace(dataset=dataset),
            base_directory=base,
            cancel_requested=cancellation,
        )
    except ReportGenerationError as error:
        raise _fail(error.code, error.message) from error
    windows = _session_windows(replays)
    selection_dates = {
        *dataset_config.partitions.train_dates,
        *dataset_config.partitions.validation_dates,
    }
    selection_events, selection_snapshots = _read_conversion_rows(
        base,
        conversions,
        dataset_config,
        cancellation,
        selected_dates=selection_dates,
    )
    selection_days = _build_days(dataset_config, selection_events, selection_snapshots, windows)
    calibration = _calibrate(tuple(selection_days.values()), dataset_config)

    model_name: PredictionModelName | None = None
    validation_predictions: dict[str, tuple[SignalPrediction, ...]] = {}
    if experiment is not None:
        model_name, _model_document = _selected_model(experiment)
        validation_days = {
            value: selection_days[value] for value in dataset_config.partitions.validation_dates
        }
        validation_predictions = _load_predictions(
            experiment, validation_days, model_name, cancellation
        )
    selected_weight, selection = _select_signal_weight(
        config,
        dataset_config,
        selection_days,
        calibration,
        experiment,
        validation_predictions,
        model_name,
        cancellation,
    )

    test_dates = set(dataset_config.partitions.test_dates)
    test_events, test_snapshots = _read_conversion_rows(
        base,
        conversions,
        dataset_config,
        cancellation,
        selected_dates=test_dates,
    )
    test_days = _build_days(dataset_config, test_events, test_snapshots, windows)
    test_predictions: dict[str, tuple[SignalPrediction, ...]] = {}
    if experiment is not None and model_name is not None:
        test_predictions = _load_predictions(experiment, test_days, model_name, cancellation)

    hashes = config_hashes(config)
    tool_sha256 = _package_content_sha256()
    parent_hashes = [dataset.manifest_sha256]
    if experiment is not None:
        parent_hashes.append(experiment.manifest_sha256)
    identity_sha256 = _stage_identity(parent_hashes, hashes.identity_config_sha256, tool_sha256)
    root = _safe_root(base)
    prepared = _prepare_run(root, identity_sha256, force_new_run, cancellation)
    if isinstance(prepared, SimulationResult):
        return prepared
    paths = prepared
    started_at_ns = time.time_ns()
    try:
        results = _run_test_scenarios(
            config,
            dataset_config,
            test_days,
            calibration,
            experiment,
            test_predictions,
            model_name,
            selected_weight,
            cancellation,
        )
        order_rows = tuple(row for result in results for row in result.orders)
        fill_rows = tuple(row for result in results for row in result.fills)
        liquidation_rows = tuple(row for result in results for row in result.liquidations)
        equity_rows = tuple(row for result in results for row in result.equity)
        simulation_id = paths.final_directory.name
        metrics_document = {
            "schema_version": 1,
            "simulation_id": simulation_id,
            "selection": selection,
            "scenarios": [
                {
                    **asdict(result.scenario),
                    "strategy_name": result.strategy_name,
                    "signal_weight_ticks": result.signal_weight_ticks,
                    "metrics": result.metrics,
                    "daily": list(result.daily_metrics),
                }
                for result in results
            ],
        }
        diagnostic_rows = tuple(row for result in results for row in result.diagnostics)
        diagnostic_counts = Counter(cast(str, row["code"]) for row in diagnostic_rows)
        diagnostics_document = {
            "schema_version": 1,
            "simulation_id": simulation_id,
            "queue_anomaly_budget": config.execution.max_queue_anomalies,
            "counts": dict(sorted(diagnostic_counts.items())),
            "records": list(diagnostic_rows),
        }
        artefacts = [
            _write_parquet(paths.staging_directory, "orders", order_rows, cancellation),
            _write_parquet(paths.staging_directory, "fills", fill_rows, cancellation),
            _write_parquet(paths.staging_directory, "liquidations", liquidation_rows, cancellation),
            _write_parquet(paths.staging_directory, "equity", equity_rows, cancellation),
            _write_json(
                paths.staging_directory,
                "metrics",
                "metrics.json",
                metrics_document,
                len(results),
                cancellation,
            ),
            _write_json(
                paths.staging_directory,
                "diagnostics",
                "diagnostics.json",
                diagnostics_document,
                len(diagnostic_rows),
                cancellation,
            ),
        ]
        warnings: list[str] = []
        if not fill_rows:
            warnings.append("No passive fills occurred; zero-fill metrics remain valid.")
        if experiment is None:
            warnings.append("Baseline-only run; no signal-adjusted comparison was available.")
        if force_new_run:
            warnings.append("A new immutable run was explicitly forced for this identity.")
        completed_at_ns = time.time_ns()
        manifest = {
            "schema_version": 1,
            "simulation_id": simulation_id,
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
            "parents": _parent_document(dataset, experiment),
            "calibration": _calibration_document(calibration),
            "selection": selection,
            "scenarios": [asdict(item) for item in _execution_scenarios(config)],
            "schemas": {
                kind: _schema_descriptor(schema) for kind, schema in _OUTPUT_SCHEMAS.items()
            },
            "artefacts": [_artefact_document(item) for item in artefacts],
            "assumptions": [
                "Only observed displayed E/C flow can fill simulated passive orders.",
                "Changed quotes cancel first and wait for a later decision before replacement.",
                "The adverse-selection proxy uses the first valid midpoint at or after 100 ms.",
                "Terminal inventory crosses the last valid visible spread.",
            ],
            "limitations": [
                "Historical replay cannot identify hidden liquidity or the full exchange queue.",
                "No immediate fills, price improvement, market impact or live execution are "
                "modelled.",
                "Results are conditional on selected symbols, dates, fees, latency and visible "
                "data quality.",
            ],
            "warnings": warnings,
        }
        _validate_manifest(manifest)
        _publish(paths, manifest)
    except SimulationError as error:
        if error.partial_exists:
            raise
        raise _fail(error.code, error.message, partial_exists=True) from error
    except (OSError, pa.ArrowException, MemoryError, TypeError, ValueError) as error:
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Simulation failed while creating staged output.",
            partial_exists=True,
        ) from error

    authenticated = load_completed_simulation(
        paths.final_directory.name,
        base_directory=base,
        cancel_requested=cancellation,
    )
    return _result_from_authenticated(authenticated, reused=False)


__all__ = [
    "equity_schema",
    "fills_schema",
    "liquidations_schema",
    "load_completed_simulation",
    "orders_schema",
    "simulate",
]
