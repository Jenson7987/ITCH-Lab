"""TASK-030 installed-environment doctor tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import itchlab_research.doctor as doctor_module
from itchlab_research.cli import main
from itchlab_research.doctor import DoctorCheck, DoctorReport, run_doctor

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _binary(path: Path, version: str = "0.1.0") -> Path:
    path.write_text(f"#!/bin/sh\nprintf 'itchlab {version}\\n'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    derived = tmp_path / "data" / "derived"
    runs.mkdir()
    derived.mkdir(parents=True)
    monkeypatch.setenv("ITCHLAB_RUNS_DIR", str(runs))
    monkeypatch.setenv("ITCHLAB_DATA_DIR", str(derived.parent))
    return runs, derived


def test_task_030_doctor_checks_installed_runtime_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, derived = _roots(tmp_path, monkeypatch)
    report = run_doctor(binary=_binary(tmp_path / "itchlab"))

    assert report.healthy is True
    assert report.application_version == "0.1.0"
    assert {check.name for check in report.checks} == {
        "python",
        "dependency:jsonschema",
        "dependency:numpy",
        "dependency:pyarrow",
        "dependency:rfc8785",
        "dependency:scikit-learn",
        "cpp_binary",
        "schemas",
        "runs_root",
        "derived_root",
    }
    assert not list(runs.glob(".itchlab-doctor-*"))
    assert not list(derived.glob(".itchlab-doctor-*"))


def test_task_030_doctor_aggregates_binary_dependency_and_directory_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ITCHLAB_RUNS_DIR", str(tmp_path / "missing-runs"))
    monkeypatch.setenv("ITCHLAB_DATA_DIR", str(tmp_path / "missing-data"))
    original_import = doctor_module.importlib.import_module

    def import_with_failure(name: str) -> object:
        if name == "rfc8785":
            raise ImportError("synthetic missing dependency")
        return original_import(name)

    monkeypatch.setattr(doctor_module.importlib, "import_module", import_with_failure)
    report = run_doctor(binary=tmp_path / "missing-binary")
    failures = {check.name for check in report.checks if check.status == "fail"}

    assert report.healthy is False
    assert failures == {
        "dependency:rfc8785",
        "cpp_binary",
        "runs_root",
        "derived_root",
    }


def test_task_030_doctor_rejects_mismatched_or_hanging_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _roots(tmp_path, monkeypatch)
    mismatch = run_doctor(binary=_binary(tmp_path / "mismatch", "9.9.9"))
    assert next(check for check in mismatch.checks if check.name == "cpp_binary").status == "fail"

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired("itchlab", 5)

    monkeypatch.setattr(doctor_module.subprocess, "run", timeout)
    hanging = run_doctor(binary=_binary(tmp_path / "hanging"))
    binary_check = next(check for check in hanging.checks if check.name == "cpp_binary")
    assert binary_check.status == "fail"
    assert "TimeoutExpired" in binary_check.summary


def test_task_030_doctor_rejects_symlink_and_broad_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_runs = tmp_path / "real-runs"
    real_runs.mkdir()
    linked_runs = tmp_path / "linked-runs"
    linked_runs.symlink_to(real_runs, target_is_directory=True)
    derived = tmp_path / "data" / "derived"
    derived.mkdir(parents=True)
    monkeypatch.setenv("ITCHLAB_RUNS_DIR", str(linked_runs))
    monkeypatch.setenv("ITCHLAB_DATA_DIR", str(derived.parent))

    report = run_doctor(binary=_binary(tmp_path / "itchlab"))
    runs_check = next(check for check in report.checks if check.name == "runs_root")
    assert runs_check.status == "fail"
    assert "symlink" in runs_check.summary


def test_task_030_doctor_cli_help_json_and_failure_exit(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["doctor", "--help"]) == 0
    assert "installed offline runtime" in capsys.readouterr().out

    report = DoctorReport(
        application_version="0.1.0",
        operating_system="TestOS",
        architecture="test-arch",
        checks=(DoctorCheck("cpp_binary", "fail", "Binary missing."),),
    )
    monkeypatch.setattr(doctor_module, "run_doctor", lambda **kwargs: report)
    assert main(["doctor", "--format", "json"]) == 7
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value["command"] == "doctor"
    assert value["status"] == "failed"
    assert value["summary"]["network"] == "not_required_or_tested"
    assert value["summary"]["checks"] == [
        {"name": "cpp_binary", "status": "fail", "summary": "Binary missing."}
    ]
    assert captured.err == ""


def test_task_030_doctor_cli_rejects_duplicate_and_unknown_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["doctor", "--format", "json", "--format", "human"]) == 2
    assert "duplicate option --format" in capsys.readouterr().err

    assert main(["doctor", "--unknown"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err


def test_task_030_doctor_starts_when_runtime_dependencies_are_missing(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    derived = tmp_path / "data" / "derived"
    runs.mkdir()
    derived.mkdir(parents=True)
    binary = _binary(tmp_path / "itchlab")
    code = """
import json

blocked = {"jsonschema", "numpy", "pyarrow", "rfc8785", "sklearn"}
from itchlab_research.cli import main
import itchlab_research.doctor as doctor

original_import = doctor.importlib.import_module
def guarded(name):
    if name in blocked:
        raise ImportError("blocked by TASK-030 test")
    return original_import(name)
doctor.importlib.import_module = guarded

raise SystemExit(main(["doctor", "--binary", BINARY, "--format", "json"]))
""".replace("BINARY", repr(str(binary)))
    environment = os.environ.copy()
    environment["ITCHLAB_RUNS_DIR"] = str(runs)
    environment["ITCHLAB_DATA_DIR"] = str(derived.parent)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 7
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    failures = {check["name"] for check in value["summary"]["checks"] if check["status"] == "fail"}
    expected_dependencies = ("jsonschema", "numpy", "pyarrow", "rfc8785", "scikit-learn")
    assert failures == {f"dependency:{name}" for name in expected_dependencies}
