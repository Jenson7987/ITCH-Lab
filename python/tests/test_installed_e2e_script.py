"""TASK-030 installed E2E fixture and command-boundary tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _e2e_module() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts/release/installed_e2e.py"
    specification = importlib.util.spec_from_file_location("task030_installed_e2e", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_task_030_installed_e2e_fixture_is_deterministic_and_substantial() -> None:
    e2e = _e2e_module()
    first = e2e.synthetic_stream()
    second = e2e.synthetic_stream()

    assert first == second
    assert len(first) > 50_000
    assert len(e2e._messages()) > 2_500


def test_task_030_installed_e2e_refuses_nonempty_or_symlink_workspace(tmp_path: Path) -> None:
    e2e = _e2e_module()
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "sentinel").write_bytes(b"preserve")

    with pytest.raises(e2e.InstalledSmokeError, match="must be empty"):
        e2e.run_installed_e2e(
            binary=Path(sys.executable), python=Path(sys.executable), workspace=nonempty
        )
    assert (nonempty / "sentinel").read_bytes() == b"preserve"

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(e2e.InstalledSmokeError, match="non-symlink"):
        e2e.run_installed_e2e(
            binary=Path(sys.executable), python=Path(sys.executable), workspace=linked
        )
