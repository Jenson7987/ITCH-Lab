#!/usr/bin/env python3
"""Run the TASK-030 synthetic vertical slice through installed release artefacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.fixtures.itch50_builder import build_stream, message

_DATES: Final = ("2019-01-30", "2019-03-27", "2019-07-30")
_START_NS: Final = 34_200_000_000_000


class InstalledSmokeError(RuntimeError):
    """An installed command or release invariant failed."""


def _messages() -> tuple[Any, ...]:
    definitions: list[Any] = [
        message(
            "start_messages",
            "S",
            stock_locate=0,
            tracking_number=1,
            timestamp_ns=1_000,
            event_code="O",
        ),
        message(
            "directory_aapl",
            "R",
            stock_locate=1,
            tracking_number=2,
            timestamp_ns=2_000,
            stock="AAPL",
            market_category="Q",
            financial_status="N",
            round_lot_size=100,
            round_lots_only="N",
            issue_classification="C",
            issue_sub_type="",
            authenticity="P",
            short_sale_threshold_indicator="N",
            ipo_flag="N",
            luld_reference_price_tier="1",
            etp_flag="N",
            etp_leverage_factor=1,
            inverse_indicator="N",
        ),
        message(
            "aapl_trading",
            "H",
            stock_locate=1,
            tracking_number=3,
            timestamp_ns=_START_NS - 1,
            stock="AAPL",
            trading_state="T",
            reserved="",
            reason="",
        ),
    ]
    tracking = 4
    timestamp = _START_NS
    next_reference = 1_000
    bid_references: list[int] = []
    ask_references: list[int] = []

    def add(side: str, price4: int, name: str) -> int:
        nonlocal tracking, timestamp, next_reference
        reference = next_reference
        next_reference += 1
        definitions.append(
            message(
                name,
                "A",
                stock_locate=1,
                tracking_number=tracking,
                timestamp_ns=timestamp,
                order_reference=reference,
                side=side,
                shares=100,
                stock="AAPL",
                price4=price4,
            )
        )
        tracking += 1
        timestamp += 10_000_000
        return reference

    base_price4 = 1_000_000
    for level in range(10):
        bid_references.append(
            add("B", base_price4 - level * 100, f"initial_bid_{level}")
        )
    for level in range(10):
        ask_references.append(
            add("S", base_price4 + (level + 1) * 100, f"initial_ask_{level}")
        )

    previous_offset = 0
    for cycle in range(90):
        phase = cycle % 60
        if phase <= 20:
            offset = phase
        elif phase <= 39:
            offset = 20
        else:
            offset = 60 - phase
        new_base = base_price4 + offset * 100
        sides = ("ask", "bid") if offset >= previous_offset else ("bid", "ask")
        for side_name in sides:
            references = ask_references if side_name == "ask" else bid_references
            for level, original_reference in enumerate(tuple(references)):
                new_reference = next_reference
                next_reference += 1
                price4 = (
                    new_base + (level + 1) * 100
                    if side_name == "ask"
                    else new_base - level * 100
                )
                definitions.append(
                    message(
                        f"replace_{side_name}_{cycle}_{level}",
                        "U",
                        stock_locate=1,
                        tracking_number=tracking,
                        timestamp_ns=timestamp,
                        original_order_reference=original_reference,
                        new_order_reference=new_reference,
                        shares=100,
                        price4=price4,
                    )
                )
                references[level] = new_reference
                tracking += 1
                timestamp += 10_000_000
        for toggle in range(8):
            side_name = "bid" if toggle % 2 == 0 else "ask"
            references = bid_references if side_name == "bid" else ask_references
            new_reference = next_reference
            next_reference += 1
            price4 = new_base if side_name == "bid" else new_base + 100
            shares = 110 if (toggle // 2) % 2 == 0 else 100
            definitions.append(
                message(
                    f"resize_{side_name}_{cycle}_{toggle}",
                    "U",
                    stock_locate=1,
                    tracking_number=tracking,
                    timestamp_ns=timestamp,
                    original_order_reference=references[0],
                    new_order_reference=new_reference,
                    shares=shares,
                    price4=price4,
                )
            )
            references[0] = new_reference
            tracking += 1
            timestamp += 10_000_000
        if cycle % 10 == 9:
            for side_name, references, side in (
                ("bid", bid_references, "B"),
                ("ask", ask_references, "S"),
            ):
                definitions.append(
                    message(
                        f"execute_{side_name}_{cycle}",
                        "E",
                        stock_locate=1,
                        tracking_number=tracking,
                        timestamp_ns=timestamp,
                        order_reference=references[0],
                        executed_shares=100,
                        match_number=100_000 + tracking,
                    )
                )
                tracking += 1
                timestamp += 10_000_000
                price4 = new_base if side == "B" else new_base + 100
                references[0] = add(side, price4, f"restore_{side_name}_{cycle}")
        previous_offset = offset

    definitions.append(
        message(
            "end_messages",
            "S",
            stock_locate=0,
            tracking_number=tracking,
            timestamp_ns=timestamp,
            event_code="C",
        )
    )
    return tuple(definitions)


def synthetic_stream() -> bytes:
    """Return an independent multi-class, full-depth synthetic ITCH stream."""
    return build_stream(_messages()).framed_bytes


def _write_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise InstalledSmokeError(f"refusing to replace {path.name}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _invoke(
    arguments: Sequence[str], *, cwd: Path, environment: dict[str, str]
) -> dict[str, Any]:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stdout.strip() or completed.stderr.strip() or "no diagnostic"
        raise InstalledSmokeError(
            f"{arguments[0]} {arguments[1] if len(arguments) > 1 else ''} "
            f"failed with exit {completed.returncode}: {detail}"
        )
    if completed.stderr:
        raise InstalledSmokeError(
            f"JSON command wrote unexpected stderr: {completed.stderr.strip()}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise InstalledSmokeError("command did not return one JSON document") from error
    if not isinstance(value, dict) or value.get("status") != "completed":
        raise InstalledSmokeError("command did not report completed status")
    return value


def run_installed_e2e(*, binary: Path, python: Path, workspace: Path) -> dict[str, Any]:
    """Execute and authenticate the installed offline vertical slice."""
    binary = binary.resolve(strict=True)
    python = python.absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise InstalledSmokeError("Python interpreter must be an executable file")
    if workspace.is_symlink() or not workspace.is_dir():
        raise InstalledSmokeError("workspace must be an existing non-symlink directory")
    resolved_workspace = workspace.resolve(strict=True)
    if resolved_workspace == Path(resolved_workspace.anchor):
        raise InstalledSmokeError("workspace is too broad")
    if any(workspace.iterdir()):
        raise InstalledSmokeError("workspace must be empty")

    runs = workspace / "runs"
    derived = workspace / "data" / "derived"
    configs = workspace / "configs"
    runs.mkdir()
    derived.mkdir(parents=True)
    configs.mkdir()
    source = workspace / "synthetic-release.itch"
    source.write_bytes(synthetic_stream())
    environment = os.environ.copy()
    environment.update(
        {
            "ITCHLAB_RUNS_DIR": str(runs),
            "ITCHLAB_DATA_DIR": str(derived.parent),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    research = [str(python), "-m", "itchlab_research"]

    doctor = _invoke(
        [*research, "doctor", "--binary", str(binary), "--format", "json"],
        cwd=workspace,
        environment=environment,
    )
    inspect = _invoke(
        [
            str(binary),
            "inspect",
            "--input",
            source.name,
            "--all",
            "--symbols",
            "AAPL",
            "--format",
            "json",
        ],
        cwd=workspace,
        environment=environment,
    )

    replay_manifests: list[str] = []
    for trading_date in _DATES:
        replay_config = configs / f"replay-{trading_date}.json"
        _write_json(
            replay_config,
            {
                "schema_version": 1,
                "input": {
                    "path": source.name,
                    "sha256": None,
                    "trading_date": trading_date,
                    "exchange_timezone": "America/New_York",
                },
                "selection": {
                    "symbols": ["AAPL"],
                    "session_start_ns": _START_NS,
                    "session_end_ns": 34_230_000_000_000,
                    "require_trading_state": True,
                },
                "output": {"depth": 10, "emit_unchanged_trade_snapshots": False},
                "validation": {
                    "mode": "strict",
                    "max_skipped_messages": 0,
                    "invariant_interval": 100,
                },
            },
        )
        replay = _invoke(
            [
                str(binary),
                "replay",
                "--config",
                replay_config.relative_to(workspace).as_posix(),
                "--output-root",
                "runs",
                "--format",
                "json",
            ],
            cwd=workspace,
            environment=environment,
        )
        replay_id = str(replay["summary"]["replay_id"])
        replay_directory = Path("runs/replay") / replay_id
        _invoke(
            [
                str(binary),
                "validate",
                "--run",
                replay_directory.as_posix(),
                "--deep",
                "--format",
                "json",
            ],
            cwd=workspace,
            environment=environment,
        )
        replay_manifests.append((replay_directory / "replay-manifest.json").as_posix())

    conversion_config = configs / "conversion.json"
    _write_json(
        conversion_config,
        {
            "schema_version": 1,
            "replay_manifests": replay_manifests,
            "output_root": "runs",
            "parquet": {
                "compression": "zstd",
                "row_group_size": 512,
                "partition_keys": ["trading_date", "symbol"],
            },
            "allow_degraded": False,
        },
    )
    conversion = _invoke(
        [
            *research,
            "convert",
            "--config",
            "configs/conversion.json",
            "--format",
            "json",
            "--quiet",
        ],
        cwd=workspace,
        environment=environment,
    )

    dataset_config = configs / "dataset.json"
    _write_json(
        dataset_config,
        {
            "schema_version": 1,
            "conversion_manifests": [conversion["summary"]["manifest_path"]],
            "symbols": ["AAPL"],
            "tick_size4_by_symbol": {"AAPL": 100},
            "features": {
                "depth_levels": [1, 5, 10],
                "event_windows": [20, 100, 500],
                "clock_windows_ns": [100_000_000, 1_000_000_000],
            },
            "labels": {
                "primary_event_horizon": 100,
                "secondary_event_horizons": [20, 500],
                "flat_threshold_ticks": 0,
            },
            "sampling": {"row_stride": 10},
            "partitions": {
                "train_dates": [_DATES[0]],
                "validation_dates": [_DATES[1]],
                "test_dates": [_DATES[2]],
            },
        },
    )
    dataset = _invoke(
        [
            *research,
            "build-dataset",
            "--config",
            "configs/dataset.json",
            "--format",
            "json",
            "--quiet",
        ],
        cwd=workspace,
        environment=environment,
    )

    experiment_config = configs / "experiment.json"
    _write_json(
        experiment_config,
        {
            "schema_version": 1,
            "dataset_manifest": dataset["summary"]["manifest_path"],
            "models": {
                "prior": {"enabled": True},
                "logistic_regression": {
                    "c_values": [0.01, 0.1, 1.0, 10.0],
                    "penalty": "l2",
                    "solver": "lbfgs",
                    "max_iter": 2000,
                },
                "hist_gradient_boosting": {
                    "learning_rates": [0.05, 0.1],
                    "max_leaf_nodes": [15, 31],
                    "l2_regularization": [0.0, 1.0],
                    "max_iter": 100,
                },
            },
            "preprocessing": {
                "continuous_imputation": "median",
                "standardise_logistic": True,
                "standardise_hist_gradient_boosting": False,
                "unknown_symbol": "all_zero",
            },
            "selection_metric": "multiclass_log_loss",
            "seed": 7987,
        },
    )
    experiment = _invoke(
        [
            *research,
            "train",
            "--config",
            "configs/experiment.json",
            "--format",
            "json",
            "--quiet",
        ],
        cwd=workspace,
        environment=environment,
    )

    simulation_config = configs / "simulation.json"
    _write_json(
        simulation_config,
        {
            "schema_version": 1,
            "dataset_manifest": dataset["summary"]["manifest_path"],
            "prediction_manifest": experiment["summary"]["manifest_path"],
            "strategy": {
                "name": "signal_adjusted_avellaneda_stoikov",
                "decision_interval_ns": 100_000_000,
                "max_prediction_age_ns": 500_000_000,
                "order_quantity": 100,
                "inventory_limit": 1000,
                "gamma": 0.1,
                "volatility_window_ns": 1_000_000_000,
                "risk_horizon_seconds": 10,
                "signal_weight_ticks": None,
                "max_signal_ticks": 2.0,
            },
            "execution": {
                "passive_only": True,
                "submission_latency_ns": 100_000,
                "cancellation_latency_ns": 100_000,
                "maker_fee_microusd_per_share": -2000,
                "taker_fee_microusd_per_share": 3000,
                "queue_policy": "known_orders_conservative",
                "max_queue_anomalies": 0,
                "terminal_liquidation": "cross_visible_spread",
            },
            "seed": 7987,
        },
    )
    simulation = _invoke(
        [
            *research,
            "simulate",
            "--config",
            "configs/simulation.json",
            "--format",
            "json",
            "--quiet",
        ],
        cwd=workspace,
        environment=environment,
    )
    report = _invoke(
        [
            *research,
            "report",
            "--run-id",
            str(simulation["run_id"]),
            "--output-format",
            "both",
            "--format",
            "json",
            "--quiet",
        ],
        cwd=workspace,
        environment=environment,
    )
    report_directory = workspace / report["summary"]["output_directory"]
    markdown = (report_directory / "report.md").read_text(encoding="utf-8")
    if "Conservative simulation comparison" not in markdown:
        raise InstalledSmokeError(
            "installed report does not contain simulation evidence"
        )
    if (
        "not a live-trading system" not in markdown
        or "evidence of profitability" not in markdown
    ):
        raise InstalledSmokeError(
            "installed report omits required historical limitations"
        )
    return {
        "doctor_checks": len(doctor["summary"]["checks"]),
        "inspect_messages": inspect["summary"]["messages_examined"],
        "replays": len(replay_manifests),
        "conversion_id": conversion["run_id"],
        "dataset_id": dataset["run_id"],
        "experiment_id": experiment["run_id"],
        "simulation_id": simulation["run_id"],
        "report_directory": report["summary"]["output_directory"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        result = run_installed_e2e(
            binary=options.binary,
            python=options.python,
            workspace=options.workspace,
        )
    except (InstalledSmokeError, OSError, KeyError, TypeError) as error:
        print(f"installed E2E failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
