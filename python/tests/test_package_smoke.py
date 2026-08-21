"""TASK-001 import and command-line smoke tests."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import itchlab_research
from itchlab_research.cli import main

PYTHON_ROOT = Path(__file__).resolve().parents[1]


def test_task_001_package_version_matches_project_metadata() -> None:
    metadata = tomllib.loads((PYTHON_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert itchlab_research.__version__ == metadata["project"]["version"]


def test_task_001_python_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0

    captured = capsys.readouterr()
    assert "Offline research package for ITCH-Lab" in captured.out
    assert "doctor" in captured.out
    assert captured.err == ""


def test_task_001_module_cli_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "itchlab_research", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "itchlab-research 0.1.0\n"
    assert completed.stderr == ""


def test_task_001_console_cli_version() -> None:
    executable = Path(sys.executable).with_name("itchlab-research")
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "itchlab-research 0.1.0\n"
    assert completed.stderr == ""


def test_task_001_python_cli_rejects_unsupported_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["convert"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--config is required" in captured.err
