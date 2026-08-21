"""TASK-030 CI policy and documentation-check contract tests."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COVERAGE_SCRIPT = REPOSITORY_ROOT / "scripts/ci/check_coverage.py"
DOCS_SCRIPT = REPOSITORY_ROOT / "scripts/ci/check_docs.py"


def _summary(
    *, statements: int, covered: int, branches: int, covered_branches: int
) -> dict[str, int]:
    return {
        "num_statements": statements,
        "covered_lines": covered,
        "num_branches": branches,
        "covered_branches": covered_branches,
    }


def _coverage_document(*, high_covered_branches: int = 8) -> dict[str, Any]:
    return {
        "files": {
            "python/src/itchlab_research/interchange/readers.py": {
                "summary": _summary(statements=10, covered=9, branches=20, covered_branches=17)
            },
            "python/src/itchlab_research/config.py": {
                "summary": _summary(
                    statements=20,
                    covered=17,
                    branches=10,
                    covered_branches=high_covered_branches,
                )
            },
            "python/src/itchlab_research/cli.py": {
                "summary": _summary(statements=10, covered=8, branches=4, covered_branches=0)
            },
        }
    }


def _run_coverage_policy(tmp_path: Path, document: object) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(document), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(COVERAGE_SCRIPT), str(report)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_task_030_coverage_policy_enforces_documented_tiers(tmp_path: Path) -> None:
    passed = _run_coverage_policy(tmp_path, _coverage_document())

    assert passed.returncode == 0
    assert "critical: line 90.00%" in passed.stdout
    assert "high: line 85.00%" in passed.stdout
    assert "standard: line 80.00%" in passed.stdout
    assert passed.stderr == ""

    failed = _run_coverage_policy(tmp_path, _coverage_document(high_covered_branches=7))

    assert failed.returncode == 1
    assert failed.stdout == ""
    assert "high branch coverage 70.00% is below 80%" in failed.stderr


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"files": {}},
        {"files": {"bad": []}},
        {
            "files": {
                "python/src/itchlab_research/config.py": {
                    "summary": _summary(statements=1, covered=2, branches=0, covered_branches=0)
                }
            }
        },
    ],
)
def test_task_030_coverage_policy_rejects_malformed_reports(
    tmp_path: Path, document: object
) -> None:
    completed = _run_coverage_policy(tmp_path, document)

    assert completed.returncode == 1
    assert "coverage policy failed:" in completed.stderr


def test_task_030_documentation_checker_rejects_broken_local_links(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DOCS_SCRIPT), run_name="task030_check_docs")
    check_markdown = cast(Any, namespace["check_markdown"])
    check_markdown.__globals__["REPOSITORY_ROOT"] = tmp_path
    markdown = tmp_path / "guide.md"
    markdown.write_text("[missing](absent.md)\n", encoding="utf-8")

    issues = check_markdown(markdown)

    assert issues == ("guide.md:1: local link does not exist: absent.md",)


def test_task_030_workflow_fixes_supported_platforms_and_offline_release_gate() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ubuntu-24.04" in workflow
    assert "macos-15" in workflow
    assert "expected_architecture: x86_64" in workflow
    assert "expected_architecture: arm64" in workflow
    assert "./scripts/release/task030-release-smoke.sh" in workflow
    assert "python scripts/ci/check_coverage.py" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in workflow
