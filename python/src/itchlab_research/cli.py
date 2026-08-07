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
from itchlab_research.config import ConversionConfig, load_config
from itchlab_research.conversion import ConversionProgress, convert_replays
from itchlab_research.errors import ConfigValidationError, ConversionError, ErrorCode

_PROGRAM_NAME = "itchlab-research"
_GLOBAL_HELP = f"""Offline research package for ITCH-Lab

Usage: {_PROGRAM_NAME} <command> [options]

Commands:
  convert      Convert authenticated replay artefacts to partitioned Parquet.

Global options:
  --help       Show this help text.
  --version    Show the application version.

Later research commands are implemented by subsequent tasks.
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
    return 7


def _safe_display_path(path: Path) -> str:
    try:
        return Path(os.path.relpath(path, Path.cwd())).as_posix()
    except ValueError:
        return path.name


def _write_error(
    code: ErrorCode,
    message: str,
    *,
    result_format: str,
    partial_exists: bool,
) -> None:
    if result_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "convert",
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
            result_format=result_format,
            partial_exists=error.partial_exists,
        )
        return _exit_code(error.code)
    except KeyboardInterrupt:
        _write_error(
            ErrorCode.CANCELLED,
            "Conversion was interrupted before completion.",
            result_format=result_format,
            partial_exists=True,
        )
        return 130
    except Exception:
        _write_error(
            ErrorCode.INTERNAL,
            "Unexpected conversion failure.",
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
    print(f"{_PROGRAM_NAME}: unrecognised command or argument.", file=sys.stderr)
    print(f"Try '{_PROGRAM_NAME} --help' for usage.", file=sys.stderr)
    return 2


__all__ = ["main"]
