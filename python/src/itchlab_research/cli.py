"""Command-line adapter for the ITCH-Lab research package."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from itchlab_research import __version__
from itchlab_research.config import (
    ConversionConfig,
    DatasetConfig,
    ExperimentConfig,
    SimulationConfig,
    load_config,
)
from itchlab_research.conversion import ConversionProgress, convert_replays
from itchlab_research.datasets import DatasetProgress, build_dataset
from itchlab_research.errors import (
    ConfigValidationError,
    ConversionError,
    DatasetBuildError,
    ErrorCode,
    ModelTrainingError,
    ReportGenerationError,
    SimulationError,
)
from itchlab_research.models import (
    ExperimentProgress,
    load_partitioned_dataset,
    train_baselines,
)
from itchlab_research.reporting import ReportFormat, generate_report
from itchlab_research.simulation import simulate

_PROGRAM_NAME = "itchlab-research"
_MODEL_ORDER_FOR_DISPLAY = (
    "prior",
    "logistic_regression",
    "hist_gradient_boosting",
)
_GLOBAL_HELP = f"""Offline research package for ITCH-Lab

Usage: {_PROGRAM_NAME} <command> [options]

Commands:
  convert      Convert authenticated replay artefacts to partitioned Parquet.
  build-dataset Build causal labels and frozen chronological dataset partitions.
  train        Train, select and evaluate the required predictive baselines.
  simulate     Run the conservative latency/cost strategy comparison.
  report       Generate an accessible predictive or simulation research report.

Global options:
  --help       Show this help text.
  --version    Show the application version.

"""

_CONVERT_HELP = f"""Convert authenticated replay artefacts to partitioned Parquet.

Usage: {_PROGRAM_NAME} convert --config <conversion-config.json> [options]

Required:
  --config <path>             Version-1 conversion configuration.

Options:
  --allow-degraded            Permit declared degraded replay parents.
  --force-new-run             Create another immutable run for the same identity.
  --format <human|json>       Result format (default human).
  --log-format <human|jsonl>  Progress format on stderr (default human).
  --quiet                     Suppress non-error progress.
  --ascii                     Restrict presentation to ASCII.
  --no-colour                 Disable colour presentation.
  --help                      Show this help text.

Example:
  {_PROGRAM_NAME} convert --config configs/conversion.example.json

Exit categories: 0 success, 2 config, 3 input, 6 output, 7 validation, 70 internal,
130 cancellation.
"""

_DATASET_HELP = f"""Build a causal dataset with frozen chronological day partitions.

Usage: {_PROGRAM_NAME} build-dataset --config <dataset-config.json> [options]

Required:
  --config <path>             Version-1 dataset configuration.

Options:
  --force-new-run             Create another immutable run for the same identity.
  --format <human|json>       Result format (default human).
  --log-format <human|jsonl>  Progress format on stderr (default human).
  --quiet                     Suppress non-error progress.
  --ascii                     Restrict presentation to ASCII.
  --no-colour                 Disable colour presentation.
  --help                      Show this help text.

Example:
  {_PROGRAM_NAME} build-dataset --config configs/dataset.example.json

Exit categories: 0 success, 2 config, 3 input, 6 output, 7 validation, 8 dataset,
70 internal, 130 cancellation.
"""

_TRAIN_HELP = f"""Train and evaluate required predictive baselines.

Usage: {_PROGRAM_NAME} train --config <experiment-config.json> [options]

Required:
  --config <path>             Version-1 experiment configuration.

Options:
  --force-new-run             Create another immutable run for the same identity.
  --format <human|json>       Result format (default human).
  --log-format <human|jsonl>  Progress format on stderr (default human).
  --quiet                     Suppress non-error progress.
  --ascii                     Restrict presentation to ASCII.
  --no-colour                 Disable colour presentation.
  --help                      Show this help text.

Example:
  {_PROGRAM_NAME} train --config configs/experiment.example.json

Exit categories: 0 success, 2 config, 3 input, 6 output, 7 validation, 8 model,
70 internal, 130 cancellation.
"""

_SIMULATE_HELP = f"""Run conservative historical market-making scenarios.

Usage: {_PROGRAM_NAME} simulate --config <simulation-config.json> [options]

Required:
  --config <path>             Version-1 simulation configuration.

Options:
  --force-new-run             Create another immutable run for the same identity.
  --format <human|json>       Result format (default human).
  --quiet                     Suppress non-error progress.
  --ascii                     Restrict presentation to ASCII.
  --no-colour                 Disable colour presentation.
  --help                      Show this help text.

Example:
  {_PROGRAM_NAME} simulate --config configs/simulation.example.json

Exit categories: 0 success, 2 config, 3 input, 6 output, 7 validation, 9 simulation,
70 internal, 130 cancellation.
"""

_REPORT_HELP = f"""Generate an accessible report from a completed experiment or simulation.

Usage: {_PROGRAM_NAME} report --run-id <experiment-id> [options]

Required:
  --run-id <id>               Completed predictive experiment or simulation ID.

Options:
  --output-format <value>     markdown, html or both (default markdown).
  --format <human|json>       Result format (default human).
  --quiet                     Suppress non-error progress.
  --ascii                     Restrict command presentation to ASCII.
  --no-colour                 Disable colour presentation.
  --help                      Show this help text.

Example:
  {_PROGRAM_NAME} report --run-id 20260808T120000.000000000Z-a1b2c3d4e5f6 \\
      --output-format both

Exit categories: 0 success, 2 config, 3 input, 6 output, 7 validation, 8 model,
70 internal, 130 cancellation.
"""


def _convert_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{_PROGRAM_NAME} convert", add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--force-new-run", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--log-format", choices=("human", "jsonl"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    return parser


def _dataset_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{_PROGRAM_NAME} build-dataset", add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--force-new-run", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--log-format", choices=("human", "jsonl"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    return parser


def _train_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{_PROGRAM_NAME} train", add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--force-new-run", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--log-format", choices=("human", "jsonl"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    return parser


def _simulate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{_PROGRAM_NAME} simulate", add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--force-new-run", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    return parser


def _report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{_PROGRAM_NAME} report", add_help=False)
    parser.add_argument("--run-id")
    parser.add_argument("--output-format", choices=("markdown", "html", "both"), default="markdown")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    return parser


def _duplicate_option(arguments: Sequence[str]) -> str | None:
    seen: set[str] = set()
    for argument in arguments:
        if not argument.startswith("--"):
            continue
        option = argument.partition("=")[0]
        if option in seen:
            return option
        seen.add(option)
    return None


def _exit_code(code: ErrorCode) -> int:
    if code in {ErrorCode.CONFIG_SCHEMA, ErrorCode.OUTPUT_PATH}:
        return 2 if code is ErrorCode.CONFIG_SCHEMA else 6
    if code is ErrorCode.INPUT_PATH:
        return 3
    if code in {ErrorCode.DISK_WRITE}:
        return 6
    if code is ErrorCode.CANCELLED:
        return 130
    if code is ErrorCode.INTERNAL:
        return 70
    if code in {
        ErrorCode.HORIZON,
        ErrorCode.PARTITION,
        ErrorCode.ROW_STRIDE,
        ErrorCode.EMPTY_DATASET,
        ErrorCode.LEAKAGE_GUARD,
        ErrorCode.MODEL_TRAINING,
        ErrorCode.PREDICTION_KEY,
    }:
        return 8
    return 7


def _simulation_exit_code(code: ErrorCode) -> int:
    if code in {
        ErrorCode.LATENCY,
        ErrorCode.COST,
        ErrorCode.QUEUE_STATE,
        ErrorCode.INVENTORY_LIMIT,
        ErrorCode.SIMULATION_ANOMALY,
        ErrorCode.BROKEN_SIM_FILL,
        ErrorCode.BOOK_CROSSED,
        ErrorCode.PRICE,
    }:
        return 9
    return _exit_code(code)


def _safe_display_path(path: Path) -> str:
    try:
        return Path(os.path.relpath(path, Path.cwd())).as_posix()
    except ValueError:
        return path.name


def _write_error(
    code: ErrorCode,
    message: str,
    *,
    command: str,
    result_format: str,
    partial_exists: bool,
) -> None:
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": command,
                    "status": "failed" if code is not ErrorCode.CANCELLED else "cancelled",
                    "error": {
                        "code": code.value,
                        "message": message,
                        "context": {"partial_exists": partial_exists},
                        "action": (
                            "Inspect the partial run and use a fresh output root before rerunning."
                            if partial_exists
                            else "Correct the configuration or authenticated parent and rerun."
                        ),
                    },
                },
                separators=(",", ":"),
            )
        )
        return
    print(f"{code.value}: {message}", file=sys.stderr)
    if partial_exists:
        print("No completed output was published; a partial run may remain.", file=sys.stderr)


def _progress_callback(log_format: str, quiet: bool) -> Any:
    started = time.monotonic()
    last = started
    reported = False

    def report(progress: ConversionProgress) -> None:
        nonlocal last, reported
        if quiet:
            return
        now = time.monotonic()
        if not reported:
            if now - started < 5:
                return
            reported = True
        elif now - last < 30:
            return
        last = now
        if log_format == "jsonl":
            print(
                json.dumps(
                    {
                        "command": "convert",
                        "event": "progress",
                        "stage": progress.stage,
                        "records": progress.records_read,
                        "elapsed_seconds": round(now - started, 3),
                    },
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )
        else:
            print(
                f"convert: {progress.stage}: {progress.records_read:,} records "
                f"({now - started:.1f}s)",
                file=sys.stderr,
            )

    return report


def _dataset_progress_callback(log_format: str, quiet: bool) -> Any:
    started = time.monotonic()
    last = started
    reported = False

    def report(progress: DatasetProgress) -> None:
        nonlocal last, reported
        if quiet:
            return
        now = time.monotonic()
        if not reported:
            if now - started < 5:
                return
            reported = True
        elif now - last < 30:
            return
        last = now
        if log_format == "jsonl":
            print(
                json.dumps(
                    {
                        "command": "build-dataset",
                        "event": "progress",
                        "stage": progress.stage,
                        "partitions": progress.partitions_completed,
                        "rows": progress.rows_processed,
                        "elapsed_seconds": round(now - started, 3),
                    },
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )
        else:
            print(
                f"build-dataset: {progress.partitions_completed:,} partitions; "
                f"{progress.rows_processed:,} qualifying rows ({now - started:.1f}s)",
                file=sys.stderr,
            )

    return report


def _experiment_progress_callback(log_format: str, quiet: bool) -> Any:
    started = time.monotonic()
    last = started
    reported = False

    def report(progress: ExperimentProgress) -> None:
        nonlocal last, reported
        if quiet:
            return
        now = time.monotonic()
        if not reported:
            if now - started < 5:
                return
            reported = True
        elif now - last < 30:
            return
        last = now
        if log_format == "jsonl":
            print(
                json.dumps(
                    {
                        "command": "train",
                        "event": "progress",
                        "stage": progress.stage,
                        "candidates_completed": progress.candidates_completed,
                        "candidates_total": progress.candidates_total,
                        "models_completed": progress.models_completed,
                        "elapsed_seconds": round(now - started, 3),
                    },
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )
        else:
            print(
                f"train: {progress.stage}: {progress.candidates_completed}/"
                f"{progress.candidates_total} candidates; "
                f"{progress.models_completed}/3 models ({now - started:.1f}s)",
                file=sys.stderr,
            )

    return report


def _run_convert(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_CONVERT_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if not arguments:
        print(f"{_PROGRAM_NAME} convert: --config is required.", file=sys.stderr)
        return 2
    duplicate = _duplicate_option(arguments)
    if duplicate is not None:
        print(f"{_PROGRAM_NAME} convert: duplicate option {duplicate}.", file=sys.stderr)
        return 2
    parser = _convert_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    if parsed.config is None:
        print(f"{_PROGRAM_NAME} convert: --config is required.", file=sys.stderr)
        return 2

    result_format = cast(str, parsed.format)
    try:
        loaded = load_config(Path(cast(str, parsed.config)), "conversion")
        config = cast(ConversionConfig, loaded)
        if cast(bool, parsed.allow_degraded) and not config.allow_degraded:
            config = replace(config, allow_degraded=True)
    except ConfigValidationError as error:
        message = "; ".join(
            f"{issue.json_pointer or '/'} {issue.message}" for issue in error.issues
        )
        _write_error(
            error.issues[0].code,
            message,
            command="convert",
            result_format=result_format,
            partial_exists=False,
        )
        return _exit_code(error.issues[0].code)

    cancelled = threading.Event()
    signal_count = 0
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            cancelled.set()
            if not cast(bool, parsed.quiet):
                print("Cancellation requested; closing partial outputs.", file=sys.stderr)
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handle_interrupt)
        result = convert_replays(
            config,
            force_new_run=cast(bool, parsed.force_new_run),
            cancel_requested=cancelled.is_set,
            progress=_progress_callback(cast(str, parsed.log_format), cast(bool, parsed.quiet)),
        )
    except ConversionError as error:
        _write_error(
            error.code,
            error.message,
            command="convert",
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Conversion was interrupted before completion.",
            command="convert",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected conversion failure.",
            command="convert",
            result_format=result_format,
            partial_exists=True,
        )
        return 70
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    manifest = _safe_display_path(result.manifest_path)
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "convert",
                    "status": result.status,
                    "run_id": result.conversion_id,
                    "summary": {
                        "manifest_path": manifest,
                        "event_rows": result.event_rows,
                        "snapshot_rows": result.snapshot_rows,
                        "parquet_files": result.parquet_files,
                        "parent_replay_ids": list(result.parent_replay_ids),
                        "partitions": result.partitions,
                        "reused": result.reused,
                        "next_command": (
                            f"{_PROGRAM_NAME} build-dataset --config configs/dataset.example.json"
                        ),
                    },
                    "warnings": (
                        ["Degraded replay input was explicitly accepted."]
                        if result.status == "degraded"
                        else []
                    ),
                },
                separators=(",", ":"),
            )
        )
    else:
        action = "Reused" if result.reused else "Completed"
        print(f"{action} conversion {result.conversion_id} ({result.status}).")
        print(f"Manifest: {manifest}")
        print(f"Parents: {', '.join(result.parent_replay_ids)}.")
        print(f"Rows: {result.event_rows:,} events; {result.snapshot_rows:,} snapshots.")
        print(f"Partitions: {result.partitions:,}.")
        print(f"Next: {_PROGRAM_NAME} build-dataset --config configs/dataset.example.json")
    return 0


def _run_build_dataset(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_DATASET_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if not arguments:
        print(f"{_PROGRAM_NAME} build-dataset: --config is required.", file=sys.stderr)
        return 2
    duplicate = _duplicate_option(arguments)
    if duplicate is not None:
        print(
            f"{_PROGRAM_NAME} build-dataset: duplicate option {duplicate}.",
            file=sys.stderr,
        )
        return 2
    parser = _dataset_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    if parsed.config is None:
        print(f"{_PROGRAM_NAME} build-dataset: --config is required.", file=sys.stderr)
        return 2

    result_format = cast(str, parsed.format)
    try:
        loaded = load_config(Path(cast(str, parsed.config)), "dataset")
        config = cast(DatasetConfig, loaded)
    except ConfigValidationError as error:
        message = "; ".join(
            f"{issue.json_pointer or '/'} {issue.message}" for issue in error.issues
        )
        _write_error(
            error.issues[0].code,
            message,
            command="build-dataset",
            result_format=result_format,
            partial_exists=False,
        )
        return _exit_code(error.issues[0].code)

    cancelled = threading.Event()
    signal_count = 0
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            cancelled.set()
            if not cast(bool, parsed.quiet):
                print("Cancellation requested; closing partial outputs.", file=sys.stderr)
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handle_interrupt)
        result = build_dataset(
            config,
            force_new_run=cast(bool, parsed.force_new_run),
            cancel_requested=cancelled.is_set,
            progress=_dataset_progress_callback(
                cast(str, parsed.log_format), cast(bool, parsed.quiet)
            ),
        )
    except DatasetBuildError as error:
        _write_error(
            error.code,
            error.message,
            command="build-dataset",
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Dataset construction was interrupted before completion.",
            command="build-dataset",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected dataset construction failure.",
            command="build-dataset",
            result_format=result_format,
            partial_exists=True,
        )
        return 70
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    manifest = _safe_display_path(result.manifest_path)
    partition_rows = dict(result.partition_rows)
    class_counts = dict(result.class_counts)
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "build-dataset",
                    "status": result.status,
                    "run_id": result.dataset_id,
                    "summary": {
                        "manifest_path": manifest,
                        "retained_rows": result.retained_rows,
                        "parquet_files": result.parquet_files,
                        "parent_conversion_ids": list(result.parent_conversion_ids),
                        "partition_rows": partition_rows,
                        "class_counts": class_counts,
                        "reused": result.reused,
                        "next_command": (
                            f"{_PROGRAM_NAME} train --config configs/experiment.example.json"
                        ),
                    },
                    "warnings": [],
                },
                separators=(",", ":"),
            )
        )
    else:
        action = "Reused" if result.reused else "Completed"
        print(f"{action} dataset {result.dataset_id} ({result.status}).")
        print(f"Manifest: {manifest}")
        print(f"Rows: {result.retained_rows:,} retained across {result.parquet_files:,} files.")
        print(
            "Partitions: "
            + "; ".join(
                f"{name}={partition_rows[name]:,}" for name in ("train", "validation", "test")
            )
            + "."
        )
        print(
            "Classes: "
            + "; ".join(f"{name}={class_counts[name]:,}" for name in ("down", "flat", "up"))
            + "."
        )
        print(f"Next: {_PROGRAM_NAME} train --config configs/experiment.example.json")
    return 0


def _run_train(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_TRAIN_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if not arguments:
        print(f"{_PROGRAM_NAME} train: --config is required.", file=sys.stderr)
        return 2
    duplicate = _duplicate_option(arguments)
    if duplicate is not None:
        print(f"{_PROGRAM_NAME} train: duplicate option {duplicate}.", file=sys.stderr)
        return 2
    parser = _train_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    if parsed.config is None:
        print(f"{_PROGRAM_NAME} train: --config is required.", file=sys.stderr)
        return 2

    result_format = cast(str, parsed.format)
    try:
        loaded = load_config(Path(cast(str, parsed.config)), "experiment")
        config = cast(ExperimentConfig, loaded)
    except ConfigValidationError as error:
        message = "; ".join(
            f"{issue.json_pointer or '/'} {issue.message}" for issue in error.issues
        )
        _write_error(
            error.issues[0].code,
            message,
            command="train",
            result_format=result_format,
            partial_exists=False,
        )
        return _exit_code(error.issues[0].code)

    cancelled = threading.Event()
    signal_count = 0
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            cancelled.set()
            if not cast(bool, parsed.quiet):
                print("Cancellation requested; closing partial outputs.", file=sys.stderr)
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handle_interrupt)
        dataset = load_partitioned_dataset(config, cancel_requested=cancelled.is_set)
        result = train_baselines(
            dataset,
            config,
            force_new_run=cast(bool, parsed.force_new_run),
            cancel_requested=cancelled.is_set,
            progress=_experiment_progress_callback(
                cast(str, parsed.log_format), cast(bool, parsed.quiet)
            ),
        )
    except ModelTrainingError as error:
        _write_error(
            error.code,
            error.message,
            command="train",
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Predictive training was interrupted before completion.",
            command="train",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected predictive training failure.",
            command="train",
            result_format=result_format,
            partial_exists=True,
        )
        return 70
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    manifest = _safe_display_path(result.manifest_path)
    selected = dict(result.selected_parameters)
    test_metrics = dict(result.test_metrics)
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "train",
                    "status": result.status,
                    "run_id": result.experiment_id,
                    "summary": {
                        "manifest_path": manifest,
                        "dataset_id": result.dataset_id,
                        "prediction_rows": result.prediction_rows,
                        "selected_parameters": selected,
                        "test_metrics": test_metrics,
                        "reused": result.reused,
                        "next_command": (f"{_PROGRAM_NAME} report --run-id {result.experiment_id}"),
                    },
                    "warnings": list(result.warnings),
                },
                separators=(",", ":"),
            )
        )
    else:
        action = "Reused" if result.reused else "Completed"
        print(f"{action} experiment {result.experiment_id} ({result.status}).")
        print(f"Manifest: {manifest}")
        print(f"Dataset: {result.dataset_id}.")
        print(f"Predictions: {result.prediction_rows:,} rows.")
        print(
            "Selected: "
            + "; ".join(f"{name}={selected[name]}" for name in _MODEL_ORDER_FOR_DISPLAY)
            + "."
        )
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        print(f"Next: {_PROGRAM_NAME} report --run-id {result.experiment_id}")
    return 0


def _run_report(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_REPORT_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if not arguments:
        print(f"{_PROGRAM_NAME} report: --run-id is required.", file=sys.stderr)
        return 2
    duplicate = _duplicate_option(arguments)
    if duplicate is not None:
        print(f"{_PROGRAM_NAME} report: duplicate option {duplicate}.", file=sys.stderr)
        return 2
    parser = _report_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    if parsed.run_id is None:
        print(f"{_PROGRAM_NAME} report: --run-id is required.", file=sys.stderr)
        return 2

    result_format = cast(str, parsed.format)
    cancelled = threading.Event()
    signal_count = 0
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            cancelled.set()
            if not cast(bool, parsed.quiet):
                print("Cancellation requested; closing partial report output.", file=sys.stderr)
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handle_interrupt)
        result = generate_report(
            cast(str, parsed.run_id),
            output_format=cast(ReportFormat, parsed.output_format),
            cancel_requested=cancelled.is_set,
        )
    except ReportGenerationError as error:
        _write_error(
            error.code,
            error.message,
            command="report",
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Predictive report generation was interrupted before completion.",
            command="report",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected predictive report failure.",
            command="report",
            result_format=result_format,
            partial_exists=True,
        )
        return 70
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    output_directory = _safe_display_path(result.output_directory)
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "report",
                    "status": result.status,
                    "run_id": result.experiment_id,
                    "summary": {
                        "output_directory": output_directory,
                        "output_format": result.output_format,
                        "artefacts": list(result.artefacts),
                        "reused": result.reused,
                    },
                    "warnings": list(result.warnings),
                },
                separators=(",", ":"),
            )
        )
    else:
        action = "Reused" if result.reused else "Completed"
        print(f"{action} predictive report for {result.experiment_id}.")
        print(f"Output: {output_directory}")
        print(f"Format: {result.output_format}.")
        print(f"Artefacts: {len(result.artefacts):,} files.")
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
    return 0


def _run_simulate(arguments: Sequence[str]) -> int:
    if arguments in (["--help"], ["-h"]):
        print(_SIMULATE_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if not arguments:
        print(f"{_PROGRAM_NAME} simulate: --config is required.", file=sys.stderr)
        return 2
    duplicate = _duplicate_option(arguments)
    if duplicate is not None:
        print(f"{_PROGRAM_NAME} simulate: duplicate option {duplicate}.", file=sys.stderr)
        return 2
    parser = _simulate_parser()
    try:
        parsed = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    if parsed.config is None:
        print(f"{_PROGRAM_NAME} simulate: --config is required.", file=sys.stderr)
        return 2

    result_format = cast(str, parsed.format)
    try:
        loaded = load_config(Path(cast(str, parsed.config)), "simulation")
        config = cast(SimulationConfig, loaded)
    except ConfigValidationError as error:
        message = "; ".join(
            f"{issue.json_pointer or '/'} {issue.message}" for issue in error.issues
        )
        _write_error(
            error.issues[0].code,
            message,
            command="simulate",
            result_format=result_format,
            partial_exists=False,
        )
        return _simulation_exit_code(error.issues[0].code)

    cancelled = threading.Event()
    signal_count = 0
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            cancelled.set()
            if not cast(bool, parsed.quiet):
                print("Cancellation requested; closing partial simulation output.", file=sys.stderr)
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handle_interrupt)
        result = simulate(
            config,
            force_new_run=cast(bool, parsed.force_new_run),
            cancel_requested=cancelled.is_set,
        )
    except SimulationError as error:
        _write_error(
            error.code,
            error.message,
            command="simulate",
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _simulation_exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Simulation was interrupted before completion.",
            command="simulate",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected simulation failure.",
            command="simulate",
            result_format=result_format,
            partial_exists=True,
        )
        return 70
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    manifest = _safe_display_path(result.manifest_path)
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "simulate",
                    "status": result.status,
                    "run_id": result.simulation_id,
                    "summary": {
                        "manifest_path": manifest,
                        "experiment_id": result.experiment_id,
                        "scenario_count": result.scenario_count,
                        "strategy_count": result.strategy_count,
                        "order_rows": result.order_rows,
                        "fill_rows": result.fill_rows,
                        "reused": result.reused,
                        "next_command": (f"{_PROGRAM_NAME} report --run-id {result.simulation_id}"),
                    },
                    "warnings": list(result.warnings),
                },
                separators=(",", ":"),
            )
        )
    else:
        action = "Reused" if result.reused else "Completed"
        print(f"{action} simulation {result.simulation_id} ({result.status}).")
        print(f"Manifest: {manifest}")
        print(
            f"Grid: {result.scenario_count:,} scenarios across "
            f"{result.strategy_count:,} strategies."
        )
        print(f"Rows: {result.order_rows:,} orders; {result.fill_rows:,} passive fills.")
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        print(f"Next: {_PROGRAM_NAME} report --run-id {result.simulation_id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the research CLI and return a process-compatible exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments in (["--help"], ["-h"]):
        print(_GLOBAL_HELP, end="")
        return 0
    if arguments == ["--version"]:
        print(f"{_PROGRAM_NAME} {__version__}")
        return 0
    if arguments[0] == "convert":
        return _run_convert(arguments[1:])
    if arguments[0] == "build-dataset":
        return _run_build_dataset(arguments[1:])
    if arguments[0] == "train":
        return _run_train(arguments[1:])
    if arguments[0] == "simulate":
        return _run_simulate(arguments[1:])
    if arguments[0] == "report":
        return _run_report(arguments[1:])
    print(f"{_PROGRAM_NAME}: unrecognised command or argument.", file=sys.stderr)
    print(f"Try '{_PROGRAM_NAME} --help' for usage.", file=sys.stderr)
    return 2


__all__ = ["main"]
