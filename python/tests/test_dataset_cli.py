"""TASK-019 public build-dataset command tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.canonical_json import config_document
from itchlab_research.cli import main
from test_dataset import _config


def _write_config(tmp_path: Path, parent: Path) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(config_document(_config(tmp_path, parent))), encoding="utf-8")
    return path


def test_task_019_build_dataset_help_version_and_duplicate_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["build-dataset", "--help"]) == 0
    assert "frozen chronological day partitions" in capsys.readouterr().out

    assert main(["build-dataset", "--version"]) == 0
    assert "itchlab-research 0.1.0" in capsys.readouterr().out

    assert (
        main(
            [
                "build-dataset",
                "--config",
                "one",
                "--config",
                "two",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "duplicate option --config" in captured.err


def test_task_019_build_dataset_cli_json_success_has_clean_channels(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = dataset_conversion_factory()
    config = _write_config(tmp_path, parent)
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "build-dataset",
                "--config",
                config.name,
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
    assert result["command"] == "build-dataset"
    assert result["status"] == "completed"
    assert result["summary"]["retained_rows"] == 9
    assert result["summary"]["parquet_files"] == 3
    assert result["summary"]["partition_rows"] == {
        "train": 3,
        "validation": 3,
        "test": 3,
    }
    assert result["summary"]["class_counts"] == {"down": 3, "flat": 3, "up": 3}
    assert len(result["summary"]["parent_conversion_ids"]) == 1
    assert not Path(result["summary"]["manifest_path"]).is_absolute()


def test_task_019_build_dataset_cli_reports_partition_failures_stably(
    tmp_path: Path,
    dataset_conversion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = dataset_conversion_factory()
    document = config_document(_config(tmp_path, parent))
    document["partitions"]["test_dates"] = ["2019-02-02"]
    path = tmp_path / "missing-day.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "build-dataset",
                "--config",
                path.name,
                "--format",
                "json",
                "--quiet",
            ]
        )
        == 8
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "build-dataset"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ERR_PARTITION"
    assert result["error"]["context"]["partial_exists"] is False
