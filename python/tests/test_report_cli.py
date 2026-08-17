"""TASK-021 public predictive report command tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.cli import main
from test_reporting import _completed_experiment


def test_task_021_report_help_version_and_duplicate_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0
    assert "report       Generate an accessible predictive or simulation" in capsys.readouterr().out

    assert main(["report", "--help"]) == 0
    assert "completed experiment or simulation" in capsys.readouterr().out

    assert main(["report", "--version"]) == 0
    assert "itchlab-research 0.1.0" in capsys.readouterr().out

    assert main(["report", "--run-id", "one", "--run-id", "two"]) == 2
    assert "duplicate option --run-id" in capsys.readouterr().err


def test_task_021_report_cli_json_success_has_clean_channels(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    experiment_id = _completed_experiment(tmp_path, dataset_conversion_factory)
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "report",
                "--run-id",
                experiment_id,
                "--output-format",
                "both",
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
    assert result["command"] == "report"
    assert result["status"] == "completed"
    assert result["run_id"] == experiment_id
    assert result["summary"]["output_format"] == "both"
    assert result["summary"]["reused"] is False
    assert not Path(result["summary"]["output_directory"]).is_absolute()
    assert {"report.md", "report.html"} <= set(result["summary"]["artefacts"])

    assert (
        main(
            [
                "report",
                "--run-id",
                experiment_id,
                "--output-format",
                "both",
                "--format",
                "json",
                "--quiet",
            ]
        )
        == 0
    )
    reused = json.loads(capsys.readouterr().out)
    assert reused["summary"]["reused"] is True


def test_task_021_report_cli_reports_usage_and_input_failures_stably(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["report"]) == 2
    assert "--run-id is required" in capsys.readouterr().err

    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "report",
                "--run-id",
                "20260808T120000.000000000Z-a1b2c3d4e5f6",
                "--format",
                "json",
                "--quiet",
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "report"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ERR_INPUT_PATH"
    assert result["error"]["context"]["partial_exists"] is False
