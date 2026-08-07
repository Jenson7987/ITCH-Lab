"""TASK-017 public convert command tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.cli import main

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _write_config(tmp_path: Path, parent: Path, *, output_root: str = "output") -> Path:
    config = {
        "schema_version": 1,
        "replay_manifests": [parent.relative_to(tmp_path).as_posix()],
        "output_root": output_root,
        "parquet": {
            "compression": "zstd",
            "row_group_size": 64,
            "partition_keys": ["trading_date", "symbol"],
        },
        "allow_degraded": False,
    }
    path = tmp_path / "conversion.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_task_017_convert_help_and_duplicate_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["convert", "--help"]) == 0
    assert "partitioned Parquet" in capsys.readouterr().out

    assert main(["convert", "--version"]) == 0
    assert "itchlab-research 0.1.0" in capsys.readouterr().out

    assert main(["convert", "--config", "one", "--config", "two"]) == 2
    captured = capsys.readouterr()
    assert "duplicate option --config" in captured.err


def test_task_017_convert_cli_json_success_has_clean_channels(
    tmp_path: Path,
    replay_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = replay_factory()
    config = _write_config(tmp_path, parent)
    monkeypatch.chdir(tmp_path)

    assert main(["convert", "--config", config.name, "--format", "json", "--quiet"]) == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["command"] == "convert"
    assert result["status"] == "completed"
    assert result["summary"]["event_rows"] == 10
    assert len(result["summary"]["parent_replay_ids"]) == 1
    assert result["summary"]["partitions"] == 2
    assert not Path(result["summary"]["manifest_path"]).is_absolute()


def test_task_017_convert_cli_reports_degraded_policy_stably(
    tmp_path: Path,
    replay_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = replay_factory(degraded=True)
    config = _write_config(tmp_path, parent)
    monkeypatch.chdir(tmp_path)

    assert main(["convert", "--config", config.name, "--format", "json"]) == 7
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "ERR_INVARIANT"
    assert error["error"]["context"]["partial_exists"] is False

    assert (
        main(
            [
                "convert",
                "--config",
                config.name,
                "--allow-degraded",
                "--format",
                "json",
                "--quiet",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "degraded"
    assert result["warnings"]


def test_task_017_real_sigint_exits_130_without_completed_manifest(
    tmp_path: Path,
    replay_factory: Any,
) -> None:
    parent = replay_factory(large_event_count=250_000)
    config = _write_config(tmp_path, parent)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "python" / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "itchlab_research",
            "convert",
            "--config",
            config.name,
            "--quiet",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    conversion_root = tmp_path / "output" / "conversion"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and process.poll() is None:
        if conversion_root.exists() and list(conversion_root.glob("*.partial")):
            process.send_signal(signal.SIGINT)
            break
        time.sleep(0.01)
    else:
        process.kill()
        pytest.fail("conversion did not create a cancellable partial run")

    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 130
    assert stdout == ""
    assert "ERR_CANCELLED" in stderr
    assert list(conversion_root.glob("*.partial"))
    assert not list(conversion_root.glob("*/conversion-manifest.json"))
