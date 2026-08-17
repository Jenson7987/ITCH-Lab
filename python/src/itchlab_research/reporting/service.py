"""Authenticated lineage loading and atomic predictive-report publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, TypeAlias, cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from itchlab_research.canonical_json import config_document, config_hashes
from itchlab_research.config import ConversionConfig, ReplayConfig, parse_config
from itchlab_research.errors import (
    ConfigValidationError,
    ErrorCode,
    ModelTrainingError,
    ReportGenerationError,
)
from itchlab_research.models import load_completed_experiment
from itchlab_research.reporting.models import (
    AuthenticatedLineageManifest,
    ReportEvidence,
    ReportFormat,
    ReportResult,
    SimulationReportEvidence,
)
from itchlab_research.reporting.renderers import (
    render_report_bundle,
    render_simulation_report_bundle,
    report_warnings,
    simulation_report_warnings,
)

_SCHEMA_VERSION: Final = 1
_MAX_JSON_BYTES: Final = 16 << 20
_MAX_REPORT_FILE_BYTES: Final = 32 << 20
_MAX_REPORT_BUNDLE_BYTES: Final = 128 << 20
_REPORT_ROOT: Final = Path("runs") / "report"
_RUN_ID_PATTERN: Final = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-[0-9a-f]{12}$")
_REPORT_FORMATS: Final = {"markdown", "html", "both"}

CancelCheck: TypeAlias = Callable[[], bool]
FileIdentity: TypeAlias = tuple[int, int, int, int, int]


def _fail(code: ErrorCode, message: str, *, partial_exists: bool = False) -> ReportGenerationError:
    return ReportGenerationError(code, message, partial_exists=partial_exists)


def _identity(status_result: os.stat_result) -> FileIdentity:
    return (
        status_result.st_dev,
        status_result.st_ino,
        status_result.st_size,
        status_result.st_mtime_ns,
        status_result.st_ctime_ns,
    )


def _check_cancel(cancel_requested: CancelCheck, *, partial_exists: bool = False) -> None:
    if cancel_requested():
        raise _fail(
            ErrorCode.CANCELLED,
            "Predictive report generation was cancelled at a safe boundary.",
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


def _read_regular_file(
    path: Path,
    maximum_bytes: int,
    *,
    allow_partial: bool = False,
) -> tuple[bytes, FileIdentity]:
    if (
        not allow_partial and any(part.endswith(".partial") for part in path.parts)
    ) or _path_has_symlink(path):
        raise _fail(ErrorCode.PARTIAL_ARTEFACT, "A partial or symlinked input is not accepted.")
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _fail(ErrorCode.INPUT_PATH, "A required report input is not readable.") from error
    with stream:
        try:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
                raise _fail(
                    ErrorCode.INPUT_PATH,
                    "A required report input is not a bounded regular file.",
                )
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
        except OSError as error:
            raise _fail(ErrorCode.INPUT_PATH, "A report input could not be read safely.") from error
    if (
        len(content) != before.st_size
        or len(content) > maximum_bytes
        or _identity(before) != _identity(after)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "A report input changed or exceeded its size bound.")
    return content, _identity(after)


def _reject_duplicate_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _strict_json(content: bytes, description: str) -> dict[str, Any]:
    try:
        document = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_names,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _fail(
            ErrorCode.SCHEMA_VERSION, f"{description} is not strict JSON/I-JSON."
        ) from error
    if not isinstance(document, dict):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{description} root is not an object.")
    return cast(dict[str, Any], document)


@lru_cache(maxsize=2)
def _manifest_validator(kind: str) -> Draft202012Validator:
    schema_names = (
        "conversion-config.schema.json",
        "conversion-manifest.schema.json",
        "replay-config.schema.json",
        "replay-manifest.schema.json",
    )
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
        documents[f"{kind}-manifest.schema.json"],
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _validate_manifest(document: Mapping[str, Any], kind: str) -> None:
    if list(_manifest_validator(kind).iter_errors(document)):
        raise _fail(ErrorCode.SCHEMA_VERSION, f"{kind.capitalize()} manifest violates schema v1.")


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
            ErrorCode.HASH_MISMATCH, "A lineage identity contains invalid SHA-256."
        ) from error
    digest.update(_SCHEMA_VERSION.to_bytes(2, "big"))
    return digest.hexdigest()


def _read_manifest(
    base: Path,
    locator: str,
    *,
    kind: str,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not _safe_relative_path(locator):
        raise _fail(ErrorCode.INPUT_PATH, "Report lineage contains an unsafe manifest locator.")
    path = base / locator
    content, _file_identity = _read_regular_file(path, _MAX_JSON_BYTES)
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected_sha256:
        raise _fail(ErrorCode.HASH_MISMATCH, "Report lineage manifest hash does not match.")
    document = _strict_json(content, f"{kind.capitalize()} manifest")
    _validate_manifest(document, kind)
    return document, digest


def _validate_conversion(
    document: Mapping[str, Any],
    expected: Mapping[str, Any],
    locator: str,
) -> ConversionConfig:
    try:
        parsed = parse_config(
            json.dumps(document["config"], ensure_ascii=False, allow_nan=False),
            "conversion",
        )
    except (ConfigValidationError, KeyError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion lineage config is invalid.") from error
    if not isinstance(parsed, ConversionConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Conversion lineage config type is invalid.")
    hashes = config_hashes(parsed)
    parents = cast(list[dict[str, Any]], document["parents"])
    expected_identity = _stage_identity(
        b"itchlab-conversion-v1",
        [cast(str, item["manifest_sha256"]) for item in parents],
        hashes.identity_config_sha256,
        cast(str, cast(dict[str, Any], document["tool"])["sha256"]),
    )
    trading_dates = sorted({cast(str, item["trading_date"]) for item in parents})
    if (
        document["conversion_id"] != Path(locator).parent.name
        or document["conversion_id"] != expected["conversion_id"]
        or document["config"] != config_document(parsed)
        or document["config_sha256"] != hashes.config_sha256
        or document["config_sha256"] != expected["config_sha256"]
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["identity_sha256"] != expected_identity
        or document["identity_sha256"] != expected["identity_sha256"]
        or trading_dates != expected["trading_dates"]
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Conversion report lineage is inconsistent.")
    return parsed


def _replay_descriptor(document: Mapping[str, Any], manifest_sha256: str) -> dict[str, Any]:
    artefacts = cast(list[dict[str, Any]], document["artefacts"])
    return {
        "replay_id": document["replay_id"],
        "manifest_sha256": manifest_sha256,
        "status": document["status"],
        "trading_date": document["source"]["trading_date"],
        "config_sha256": document["config_sha256"],
        "identity_sha256": document["identity_sha256"],
        "source_sha256": document["source"]["sha256"],
        "events_sha256": artefacts[0]["sha256"],
        "snapshots_sha256": artefacts[1]["sha256"],
        "snapshot_depth": artefacts[1]["depth"],
    }


def _validate_replay(
    document: Mapping[str, Any],
    expected: Mapping[str, Any],
    locator: str,
    manifest_sha256: str,
) -> None:
    try:
        parsed = parse_config(
            json.dumps(document["config"], ensure_ascii=False, allow_nan=False),
            "replay",
        )
    except (ConfigValidationError, KeyError, TypeError, ValueError) as error:
        raise _fail(ErrorCode.SCHEMA_VERSION, "Replay lineage config is invalid.") from error
    if not isinstance(parsed, ReplayConfig):
        raise _fail(ErrorCode.SCHEMA_VERSION, "Replay lineage config type is invalid.")
    hashes = config_hashes(parsed)
    expected_identity = _stage_identity(
        b"itchlab-replay-v1",
        [cast(str, cast(dict[str, Any], document["source"])["sha256"])],
        hashes.identity_config_sha256,
        cast(str, document["executable_sha256"]),
    )
    if (
        document["replay_id"] != Path(locator).parent.name
        or document["config"] != config_document(parsed)
        or document["config_sha256"] != hashes.config_sha256
        or document["identity_config_sha256"] != hashes.identity_config_sha256
        or document["identity_sha256"] != expected_identity
        or _replay_descriptor(document, manifest_sha256) != dict(expected)
    ):
        raise _fail(ErrorCode.HASH_MISMATCH, "Replay report lineage is inconsistent.")


def _load_lineage(
    base: Path,
    experiment: Any,
    cancel_requested: CancelCheck,
) -> tuple[tuple[AuthenticatedLineageManifest, ...], tuple[AuthenticatedLineageManifest, ...]]:
    dataset_manifest = cast(dict[str, Any], experiment.dataset.manifest)
    conversion_locators = cast(list[str], dataset_manifest["config"]["conversion_manifests"])
    expected_conversions = {
        cast(str, item["conversion_id"]): item
        for item in cast(list[dict[str, Any]], dataset_manifest["parents"])
    }
    conversions: list[AuthenticatedLineageManifest] = []
    replay_requests: dict[str, tuple[str, dict[str, Any]]] = {}
    for locator in conversion_locators:
        _check_cancel(cancel_requested)
        run_id = Path(locator).parent.name
        expected = expected_conversions.get(run_id)
        if expected is None:
            raise _fail(ErrorCode.HASH_MISMATCH, "Dataset lineage names an unexpected conversion.")
        document, digest = _read_manifest(
            base,
            locator,
            kind="conversion",
            expected_sha256=cast(str, expected["manifest_sha256"]),
        )
        config = _validate_conversion(document, expected, locator)
        conversions.append(
            AuthenticatedLineageManifest("conversion", run_id, locator, digest, document)
        )
        expected_replays = {
            cast(str, item["replay_id"]): item
            for item in cast(list[dict[str, Any]], document["parents"])
        }
        for replay_locator in config.replay_manifests:
            replay_id = Path(replay_locator).parent.name
            replay_expected = expected_replays.get(replay_id)
            if replay_expected is None:
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "Conversion lineage names an unexpected replay.",
                )
            previous = replay_requests.get(replay_id)
            request = (replay_locator, replay_expected)
            if previous is not None and previous != request:
                raise _fail(ErrorCode.HASH_MISMATCH, "Replay lineage is contradictory.")
            replay_requests[replay_id] = request
        if set(expected_replays) != {Path(value).parent.name for value in config.replay_manifests}:
            raise _fail(ErrorCode.HASH_MISMATCH, "Conversion replay lineage is incomplete.")
    if set(expected_conversions) != {item.run_id for item in conversions}:
        raise _fail(ErrorCode.HASH_MISMATCH, "Dataset conversion lineage is incomplete.")

    replays: list[AuthenticatedLineageManifest] = []
    for replay_id, (locator, expected) in sorted(replay_requests.items()):
        _check_cancel(cancel_requested)
        document, digest = _read_manifest(
            base,
            locator,
            kind="replay",
            expected_sha256=cast(str, expected["manifest_sha256"]),
        )
        _validate_replay(document, expected, locator, digest)
        replays.append(AuthenticatedLineageManifest("replay", replay_id, locator, digest, document))
    return tuple(sorted(conversions, key=lambda item: item.run_id)), tuple(replays)


def load_authenticated_lineage(
    experiment: Any,
    *,
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> tuple[tuple[AuthenticatedLineageManifest, ...], tuple[AuthenticatedLineageManifest, ...]]:
    """Authenticate conversion and replay parents for another read-only stage."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    return _load_lineage(base, experiment, cancellation)


def _safe_output_parent(base: Path, run_id: str) -> Path:
    report_root = base / _REPORT_ROOT
    parent = report_root / run_id
    if report_root.exists() and _path_has_symlink(report_root):
        raise _fail(ErrorCode.OUTPUT_PATH, "Report output root may not contain symlinks.")
    try:
        parent.mkdir(parents=True, exist_ok=True)
        resolved = parent.resolve(strict=True)
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Report output root could not be prepared.") from error
    if _path_has_symlink(resolved):
        raise _fail(ErrorCode.OUTPUT_PATH, "Report output path may not contain symlinks.")
    return resolved


def _bundle_paths(directory: Path) -> set[str]:
    paths: set[str] = set()
    try:
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise _fail(ErrorCode.HASH_MISMATCH, "Existing report bundle contains a symlink.")
            if path.is_file():
                paths.add(path.relative_to(directory).as_posix())
            elif not path.is_dir():
                raise _fail(ErrorCode.HASH_MISMATCH, "Existing report bundle is invalid.")
    except OSError as error:
        raise _fail(
            ErrorCode.HASH_MISMATCH, "Existing report bundle cannot be inspected."
        ) from error
    return paths


def _verify_existing(
    directory: Path,
    expected: Mapping[str, bytes],
    *,
    allow_partial: bool = False,
) -> bool:
    if not directory.exists():
        return False
    if not directory.is_dir() or _path_has_symlink(directory):
        raise _fail(ErrorCode.HASH_MISMATCH, "Existing report output is not a safe directory.")
    if _bundle_paths(directory) != set(expected):
        raise _fail(ErrorCode.HASH_MISMATCH, "Existing report bundle file set is inconsistent.")
    for relative, expected_content in expected.items():
        content, _file_identity = _read_regular_file(
            directory / relative,
            _MAX_REPORT_FILE_BYTES,
            allow_partial=allow_partial,
        )
        if content != expected_content:
            raise _fail(ErrorCode.HASH_MISMATCH, "Existing report bundle content is inconsistent.")
    return True


def _remove_lock(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Report output lock could not be removed.") from error


def _write_bundle_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_bundle(
    parent: Path,
    output_format: ReportFormat,
    rendered: Mapping[str, bytes],
    cancel_requested: CancelCheck,
) -> tuple[Path, bool]:
    final_directory = parent / output_format
    staging_directory = parent / f"{output_format}.partial"
    lock_path = parent / f".{output_format}.lock"
    try:
        lock_path.mkdir()
    except FileExistsError as error:
        raise _fail(ErrorCode.RUN_EXISTS, "A matching report bundle is already locked.") from error
    except OSError as error:
        raise _fail(ErrorCode.OUTPUT_PATH, "Report output lock could not be created.") from error
    staging_created = False
    try:
        if staging_directory.exists():
            raise _fail(ErrorCode.RUN_EXISTS, "A partial matching report bundle already exists.")
        if _verify_existing(final_directory, rendered):
            _remove_lock(lock_path)
            return final_directory, True
        staging_directory.mkdir()
        staging_created = True
        for relative, content in rendered.items():
            _check_cancel(cancel_requested, partial_exists=True)
            try:
                _write_bundle_file(staging_directory / relative, content)
            except OSError as error:
                raise _fail(
                    ErrorCode.DISK_WRITE,
                    "A staged report artefact could not be written.",
                    partial_exists=True,
                ) from error
        if not _verify_existing(staging_directory, rendered, allow_partial=True):
            raise _fail(
                ErrorCode.INTERNAL,
                "Staged report verification did not complete.",
                partial_exists=True,
            )
        _check_cancel(cancel_requested, partial_exists=True)
        _remove_lock(lock_path)
        staging_directory.rename(final_directory)
        return final_directory, False
    except ReportGenerationError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        if staging_created and not error.partial_exists:
            raise _fail(error.code, error.message, partial_exists=True) from error
        raise
    except OSError as error:
        if lock_path.exists():
            _remove_lock(lock_path)
        raise _fail(
            ErrorCode.DISK_WRITE,
            "Report bundle could not be atomically published.",
            partial_exists=staging_created,
        ) from error


def _validate_rendered_files(base: Path, rendered: Mapping[str, bytes]) -> None:
    if not rendered or any(not _safe_relative_path(path) for path in rendered):
        raise _fail(ErrorCode.INTERNAL, "Rendered report file names are invalid.")
    total = sum(len(content) for content in rendered.values())
    if total > _MAX_REPORT_BUNDLE_BYTES or any(
        len(content) > _MAX_REPORT_FILE_BYTES for content in rendered.values()
    ):
        raise _fail(ErrorCode.DISK_WRITE, "Rendered report exceeds its size bound.")
    private_values = {str(base), str(Path.home())}
    for content in rendered.values():
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise _fail(ErrorCode.INTERNAL, "Rendered report content is not UTF-8.") from error
        if any(value and value in text for value in private_values):
            raise _fail(ErrorCode.INTERNAL, "Rendered report contains a private absolute path.")


def generate_report(
    run_id: str,
    *,
    output_format: ReportFormat = "markdown",
    base_directory: Path | None = None,
    cancel_requested: CancelCheck | None = None,
) -> ReportResult:
    """Authenticate one predictive run and atomically publish its accessible report bundle."""
    base = (Path.cwd() if base_directory is None else base_directory).resolve()
    cancellation = cancel_requested or (lambda: False)
    if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise _fail(ErrorCode.INPUT_PATH, "The report run ID is invalid.")
    if output_format not in _REPORT_FORMATS:
        raise _fail(ErrorCode.CONFIG_SCHEMA, "The report output format is invalid.")
    _check_cancel(cancellation)
    output_locator = (_REPORT_ROOT / run_id / output_format).as_posix()
    simulation_manifest = base / "runs" / "simulation" / run_id / "simulation-manifest.json"
    if simulation_manifest.exists():
        from itchlab_research.errors import SimulationError
        from itchlab_research.simulation.service import load_completed_simulation

        try:
            simulation = load_completed_simulation(
                run_id,
                base_directory=base,
                cancel_requested=cancellation,
            )
        except SimulationError as error:
            raise _fail(error.code, error.message, partial_exists=False) from error
        predictive: ReportEvidence | None = None
        experiment_parent = cast(
            dict[str, Any] | None, simulation.manifest["parents"]["experiment"]
        )
        if experiment_parent is not None:
            try:
                experiment = load_completed_experiment(
                    cast(str, experiment_parent["run_id"]),
                    base_directory=base,
                    cancel_requested=cancellation,
                )
            except ModelTrainingError as error:
                raise _fail(error.code, error.message, partial_exists=False) from error
            if (
                experiment.manifest_sha256 != experiment_parent["manifest_sha256"]
                or experiment.manifest["config_sha256"] != experiment_parent["config_sha256"]
                or experiment.manifest["identity_sha256"] != experiment_parent["identity_sha256"]
            ):
                raise _fail(
                    ErrorCode.HASH_MISMATCH,
                    "Simulation report experiment lineage is inconsistent.",
                )
            conversions, replays = _load_lineage(base, experiment, cancellation)
            predictive = ReportEvidence(
                experiment=experiment,
                conversions=conversions,
                replays=replays,
                output_format=output_format,
                output_locator=output_locator,
            )
        evidence_value = SimulationReportEvidence(
            simulation=simulation,
            predictive=predictive,
            output_format=output_format,
            output_locator=output_locator,
        )
        try:
            rendered = render_simulation_report_bundle(evidence_value)
        except (KeyError, TypeError, ValueError) as error:
            raise _fail(
                ErrorCode.SCHEMA_VERSION,
                "Authenticated simulation report evidence cannot be rendered.",
            ) from error
        warnings = simulation_report_warnings(evidence_value)
    else:
        try:
            experiment = load_completed_experiment(
                run_id,
                base_directory=base,
                cancel_requested=cancellation,
            )
        except ModelTrainingError as error:
            raise _fail(error.code, error.message, partial_exists=False) from error
        conversions, replays = _load_lineage(base, experiment, cancellation)
        evidence = ReportEvidence(
            experiment=experiment,
            conversions=conversions,
            replays=replays,
            output_format=output_format,
            output_locator=output_locator,
        )
        try:
            rendered = render_report_bundle(evidence)
        except (KeyError, TypeError, ValueError) as error:
            raise _fail(
                ErrorCode.SCHEMA_VERSION, "Authenticated report evidence cannot be rendered."
            ) from error
        warnings = report_warnings(evidence)
    _validate_rendered_files(base, rendered)
    parent = _safe_output_parent(base, run_id)
    output_directory, reused = _publish_bundle(
        parent,
        output_format,
        rendered,
        cancellation,
    )
    return ReportResult(
        experiment_id=run_id,
        status="completed",
        output_directory=output_directory,
        output_format=output_format,
        artefacts=tuple(rendered),
        warnings=warnings,
        reused=reused,
    )


__all__ = ["generate_report", "load_authenticated_lineage"]
