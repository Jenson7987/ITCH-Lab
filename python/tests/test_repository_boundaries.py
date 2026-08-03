"""TASK-001 repository-boundary smoke tests."""

import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _is_ignored(relative_path: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY_ROOT),
            "check-ignore",
            "--quiet",
            "--no-index",
            relative_path,
        ],
        check=False,
    )
    if completed.returncode not in {0, 1}:
        pytest.fail(f"git check-ignore failed for {relative_path!r}")
    return completed.returncode == 0


@pytest.mark.parametrize(
    "relative_path",
    [
        "data/raw/source.itch",
        "data/raw/nested/source.itch.gz",
        "data/derived/events.parquet",
        "data/derived/nested/snapshots.parquet",
        "runs/replay/example/manifest.json",
    ],
)
def test_task_001_bulk_data_and_run_outputs_are_ignored(relative_path: str) -> None:
    assert _is_ignored(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "data/fixtures/synthetic.itch",
        "data/raw/.gitkeep",
        "data/derived/.gitkeep",
        "runs/.gitkeep",
    ],
)
def test_task_001_source_and_sentinels_are_not_ignored(relative_path: str) -> None:
    assert not _is_ignored(relative_path)
