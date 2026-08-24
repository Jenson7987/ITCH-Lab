"""TASK-027 public simulate command contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import itchlab_research.cli as cli
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.models import SimulationResult


def _config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_manifest": "runs/dataset/example/dataset-manifest.json",
                "prediction_manifest": None,
                "strategy": {
                    "name": "inventory_aware_avellaneda_stoikov",
                    "decision_interval_ns": 100_000_000,
                    "max_prediction_age_ns": 500_000_000,
                    "order_quantity": 100,
                    "inventory_limit": 1000,
                    "gamma": 0.1,
                    "volatility_window_ns": 60_000_000_000,
                    "risk_horizon_seconds": 10,
                    "signal_weight_ticks": 0.0,
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
            }
        ),
        encoding="utf-8",
    )


def test_simulate_help_version_and_duplicate_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["simulate", "--help"]) == 0
    assert "conservative historical market-making" in capsys.readouterr().out

    assert cli.main(["simulate", "--version"]) == 0
    assert "itchlab-research 0.1.3" in capsys.readouterr().out

    assert cli.main(["simulate", "--config", "one", "--config", "two"]) == 2
    assert "duplicate option --config" in capsys.readouterr().err


def test_simulate_json_success_has_clean_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "simulation.json"
    _config(config_path)
    manifest_path = tmp_path / "runs" / "simulation" / "run" / "simulation-manifest.json"
    monkeypatch.setattr(
        cli,
        "simulate",
        lambda *_args, **_kwargs: SimulationResult(
            simulation_id="20260817T120000.000000000Z-a1b2c3d4e5f6",
            status="completed",
            manifest_path=manifest_path,
            experiment_id=None,
            scenario_count=6,
            strategy_count=1,
            order_rows=10,
            fill_rows=2,
            warnings=("Baseline-only run.",),
            reused=False,
        ),
    )

    assert cli.main(["simulate", "--config", str(config_path), "--format", "json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "simulate"
    assert result["status"] == "completed"
    assert result["summary"]["scenario_count"] == 6
    assert result["summary"]["strategy_count"] == 1
    assert result["summary"]["fill_rows"] == 2
    assert result["summary"]["next_command"].endswith(result["run_id"])


def test_simulate_domain_failure_uses_exit_nine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "simulation.json"
    _config(config_path)

    def fail(*_args: object, **_kwargs: object) -> SimulationResult:
        raise SimulationError(ErrorCode.SIMULATION_ANOMALY, "Synthetic simulation failure.")

    monkeypatch.setattr(cli, "simulate", fail)
    assert cli.main(["simulate", "--config", str(config_path), "--format", "json"]) == 9
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["error"]["code"] == "ERR_SIMULATION_ANOMALY"
    assert result["error"]["context"]["partial_exists"] is False
