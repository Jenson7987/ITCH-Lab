"""TASK-027 authenticated scenario-grid publication and combined-report integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import itchlab_research.simulation.service as simulation_service
from itchlab_research.config import SimulationConfig, parse_config
from itchlab_research.datasets import build_dataset
from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.models import load_partitioned_dataset, train_baselines
from itchlab_research.reporting import generate_report
from itchlab_research.simulation import load_completed_simulation, simulate
from test_dataset import _config as dataset_config
from test_models import _experiment_config
from test_reporting import _assert_accessible_html

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _completed_experiment(tmp_path: Path, dataset_conversion_factory: Any) -> tuple[str, Path]:
    conversion_manifest = dataset_conversion_factory(execution_events=True)
    dataset = build_dataset(
        dataset_config(tmp_path, conversion_manifest),
        base_directory=tmp_path,
    )
    experiment_config = _experiment_config(tmp_path, dataset.manifest_path)
    loaded = load_partitioned_dataset(experiment_config, base_directory=tmp_path)
    experiment = train_baselines(loaded, experiment_config, base_directory=tmp_path)
    return experiment.experiment_id, dataset.manifest_path


def _simulation_config(
    tmp_path: Path, experiment_id: str, dataset_manifest: Path
) -> SimulationConfig:
    value = {
        "schema_version": 1,
        "dataset_manifest": dataset_manifest.relative_to(tmp_path).as_posix(),
        "prediction_manifest": (
            Path("runs") / "experiment" / experiment_id / "experiment-manifest.json"
        ).as_posix(),
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
    }
    parsed = parse_config(json.dumps(value), "simulation")
    assert isinstance(parsed, SimulationConfig)
    return parsed


def test_e2e_001_simulation_grid_is_immutable_valid_and_reportable(
    tmp_path: Path, dataset_conversion_factory: Any
) -> None:
    experiment_id, dataset_manifest = _completed_experiment(tmp_path, dataset_conversion_factory)
    config = _simulation_config(tmp_path, experiment_id, dataset_manifest)

    result = simulate(config, base_directory=tmp_path)
    authenticated = load_completed_simulation(result.simulation_id, base_directory=tmp_path)

    assert result.scenario_count == 6
    assert result.strategy_count == 2
    assert len(authenticated.metrics["scenarios"]) == 12
    assert authenticated.manifest["calibration"]["training_dates"] == ["2019-01-30"]
    assert len(authenticated.manifest["calibration"]["pooled"]["buckets"]) == 11
    assert authenticated.manifest["calibration"]["symbols"][0]["source"] in {
        "symbol",
        "pooled",
    }
    assert (
        authenticated.manifest["selection"]["signal_weight"]["fixed_taker_fee_microusd_per_share"]
        == 3000
    )
    assert {
        (item["submission_latency_ns"], item["maker_fee_microusd_per_share"])
        for item in authenticated.manifest["scenarios"]
    } == {
        (0, -2000),
        (0, 3000),
        (100_000, -2000),
        (100_000, 3000),
        (1_000_000, -2000),
        (1_000_000, 3000),
    }
    for item in authenticated.metrics["scenarios"]:
        metrics = item["metrics"]
        assert metrics["reconciled"] is True
        assert metrics["settled"] is True
        assert {
            "passive_fill_count",
            "max_abs_inventory_by_symbol",
            "marked_pnl_microusd",
            "passive_spread_capture_microusd",
            "inventory_mark_to_market_microusd",
            "terminal_liquidation_slippage_microusd",
            "signed_fee_microusd",
            "max_drawdown_microusd",
            "turnover_microusd",
            "adverse_selection_100ms_microusd",
        } <= set(metrics)

    reused = simulate(config, base_directory=tmp_path)
    assert reused.simulation_id == result.simulation_id
    assert reused.reused is True

    forced = simulate(config, base_directory=tmp_path, force_new_run=True)
    assert forced.simulation_id != result.simulation_id
    assert forced.reused is False
    assert any("forced" in warning for warning in forced.warnings)

    report = generate_report(result.simulation_id, base_directory=tmp_path, output_format="both")
    markdown = (report.output_directory / "report.md").read_text(encoding="utf-8")
    html = (report.output_directory / "report.html").read_text(encoding="utf-8")
    assert "Conservative simulation comparison" in markdown
    assert "Test latency and cost sensitivity" in markdown
    assert "Assumptions, anomalies and limitations" in markdown
    assert "Maximum drawdown" in markdown
    assert "Submission latency" in markdown
    assert "Maker cost" in markdown
    assert "python -m itchlab_research simulate --config" in markdown
    assert "<caption>Metrics by test scenario and strategy</caption>" in html
    assert "<strong>Queue/prediction diagnostics:</strong>" in html
    assert html.endswith("</main></body></html>\n")
    _assert_accessible_html(report.output_directory / "report.html")
    assert (report.output_directory / "configs" / "simulation.json").is_file()
    assert (report.output_directory / "simulation-metrics.json").is_file()

    original_manifest = result.manifest_path.read_bytes()
    altered_manifest = json.loads(original_manifest)
    altered_manifest["calibration"]["training_dates"] = ["2019-01-29"]
    result.manifest_path.write_text(json.dumps(altered_manifest), encoding="utf-8")
    with pytest.raises(SimulationError) as failure:
        load_completed_simulation(result.simulation_id, base_directory=tmp_path)
    assert failure.value.code is ErrorCode.HASH_MISMATCH
    result.manifest_path.write_bytes(original_manifest)

    metrics_path = result.manifest_path.parent / "metrics.json"
    metrics_path.write_bytes(metrics_path.read_bytes() + b" ")
    with pytest.raises(SimulationError) as failure:
        load_completed_simulation(result.simulation_id, base_directory=tmp_path)
    assert failure.value.code is ErrorCode.HASH_MISMATCH


def test_signal_weight_override_must_match_validation_selection(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id, dataset_manifest = _completed_experiment(tmp_path, dataset_conversion_factory)
    config = _simulation_config(tmp_path, experiment_id, dataset_manifest)
    selected = simulate(config, base_directory=tmp_path)
    manifest = json.loads(selected.manifest_path.read_text(encoding="utf-8"))
    chosen = manifest["selection"]["signal_weight"]["selected"]
    wrong = next(value for value in (0.0, 0.5, 1.0, 2.0) if value != chosen)
    config_value = json.loads(json.dumps(manifest["config"]))
    config_value["strategy"]["signal_weight_ticks"] = wrong
    parsed = parse_config(json.dumps(config_value), "simulation")
    assert isinstance(parsed, SimulationConfig)

    selected_date_sets: list[set[str]] = []
    prediction_date_sets: list[set[str]] = []
    original_read = simulation_service._read_conversion_rows
    original_predictions = simulation_service._load_predictions

    def tracked_read(*args: Any, **kwargs: Any) -> Any:
        selected_date_sets.append(set(kwargs["selected_dates"]))
        return original_read(*args, **kwargs)

    monkeypatch.setattr(simulation_service, "_read_conversion_rows", tracked_read)

    def tracked_predictions(*args: Any, **kwargs: Any) -> Any:
        prediction_date_sets.append(set(args[1]))
        return original_predictions(*args, **kwargs)

    monkeypatch.setattr(simulation_service, "_load_predictions", tracked_predictions)

    with pytest.raises(SimulationError) as failure:
        simulate(parsed, base_directory=tmp_path)
    assert failure.value.code is ErrorCode.LEAKAGE_GUARD
    assert selected_date_sets == [{"2019-01-30", "2019-01-31"}]
    assert prediction_date_sets == [{"2019-01-31"}]


def test_simulation_config_identity_includes_anomaly_budget() -> None:
    from itchlab_research.canonical_json import config_hashes

    base_value = {
        "schema_version": 1,
        "dataset_manifest": "runs/dataset/example/dataset-manifest.json",
        "prediction_manifest": None,
        "strategy": {
            "name": "inventory_aware_avellaneda_stoikov",
            "decision_interval_ns": 1,
            "max_prediction_age_ns": 0,
            "order_quantity": 1,
            "inventory_limit": 1,
            "gamma": 0.1,
            "volatility_window_ns": 1,
            "risk_horizon_seconds": 1,
            "signal_weight_ticks": 0.0,
            "max_signal_ticks": 0.0,
        },
        "execution": {
            "passive_only": True,
            "submission_latency_ns": 0,
            "cancellation_latency_ns": 0,
            "maker_fee_microusd_per_share": 0,
            "taker_fee_microusd_per_share": 0,
            "queue_policy": "known_orders_conservative",
            "max_queue_anomalies": 0,
            "terminal_liquidation": "cross_visible_spread",
        },
        "seed": 0,
    }
    first = parse_config(json.dumps(base_value), "simulation")
    base_value["execution"]["max_queue_anomalies"] = 1
    second = parse_config(json.dumps(base_value), "simulation")
    assert isinstance(first, SimulationConfig)
    assert isinstance(second, SimulationConfig)
    assert (
        config_hashes(first).identity_config_sha256 != config_hashes(second).identity_config_sha256
    )


def test_simulation_manifest_schema_is_valid_and_packaged_identically() -> None:
    root = REPOSITORY_ROOT / "schemas" / "simulation-manifest.schema.json"
    packaged = REPOSITORY_ROOT / "python" / "src" / "itchlab_research" / "_schemas" / root.name

    Draft202012Validator.check_schema(json.loads(root.read_text(encoding="utf-8")))
    assert root.read_bytes() == packaged.read_bytes()
