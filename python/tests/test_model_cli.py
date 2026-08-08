"""TASK-020 public train command tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.canonical_json import config_document
from itchlab_research.cli import main
from itchlab_research.datasets import build_dataset
from test_dataset import _config as dataset_config
from test_models import _experiment_config


def test_task_020_train_help_version_and_duplicate_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["train", "--help"]) == 0
    assert "required predictive baselines" in capsys.readouterr().out

    assert main(["train", "--version"]) == 0
    assert "itchlab-research 0.1.0" in capsys.readouterr().out

    assert main(["train", "--config", "one", "--config", "two"]) == 2
    assert "duplicate option --config" in capsys.readouterr().err


def test_task_020_train_cli_json_success_has_clean_channels(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conversion_manifest = dataset_conversion_factory()
    dataset = build_dataset(dataset_config(tmp_path, conversion_manifest), base_directory=tmp_path)
    experiment = _experiment_config(tmp_path, dataset.manifest_path)
    config_path = tmp_path / "experiment.json"
    config_path.write_text(json.dumps(config_document(experiment)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "train",
                "--config",
                config_path.name,
                "--format",
                "json",
                "--quiet",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "train"
    assert result["status"] == "completed"
    assert result["summary"]["prediction_rows"] == 18
    assert result["summary"]["reused"] is False
    assert set(result["summary"]["selected_parameters"]) == {
        "prior",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert set(result["summary"]["test_metrics"]) == {
        "prior",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert not Path(result["summary"]["manifest_path"]).is_absolute()


def test_task_020_train_cli_reports_input_failure_stably(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _experiment_config(tmp_path, tmp_path / "missing-dataset-manifest.json")
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(config_document(config)), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["train", "--config", path.name, "--format", "json", "--quiet"]) == 3

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "train"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ERR_INPUT_PATH"
    assert result["error"]["context"]["partial_exists"] is False
