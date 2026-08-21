#!/usr/bin/env python3
"""Enforce the TASK-030 Python coverage tiers from the testing strategy."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

CRITICAL_PATHS: Final = (
    "interchange/readers.py",
    "simulation/accounting.py",
    "simulation/state_machine.py",
)
HIGH_PATHS: Final = (
    "canonical_json.py",
    "config.py",
    "datasets/features.py",
    "datasets/labels.py",
    "datasets/splits.py",
    "strategies/",
)


class CoveragePolicyError(RuntimeError):
    """Coverage input is malformed or a required tier is below its threshold."""


@dataclass(frozen=True, slots=True)
class Totals:
    """Aggregated executable line and branch totals."""

    lines: int = 0
    covered_lines: int = 0
    branches: int = 0
    covered_branches: int = 0

    @property
    def line_percent(self) -> float:
        return 100.0 if self.lines == 0 else self.covered_lines * 100.0 / self.lines

    @property
    def branch_percent(self) -> float:
        return (
            100.0
            if self.branches == 0
            else self.covered_branches * 100.0 / self.branches
        )

    def plus(self, summary: dict[str, Any]) -> Totals:
        required = (
            "num_statements",
            "covered_lines",
            "num_branches",
            "covered_branches",
        )
        values = tuple(summary.get(name) for name in required)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise CoveragePolicyError("coverage summary contains an invalid total")
        if (
            summary["covered_lines"] > summary["num_statements"]
            or summary["covered_branches"] > summary["num_branches"]
        ):
            raise CoveragePolicyError("coverage summary contains impossible totals")
        return Totals(
            lines=self.lines + summary["num_statements"],
            covered_lines=self.covered_lines + summary["covered_lines"],
            branches=self.branches + summary["num_branches"],
            covered_branches=self.covered_branches + summary["covered_branches"],
        )


def _tier(path: str) -> str:
    if any(suffix in path for suffix in CRITICAL_PATHS):
        return "critical"
    if any(suffix in path for suffix in HIGH_PATHS):
        return "high"
    return "standard"


def coverage_totals(document: object) -> dict[str, Totals]:
    """Aggregate coverage.py JSON by the documented project criticality tiers."""
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise CoveragePolicyError("coverage document must contain a files object")
    totals = {name: Totals() for name in ("critical", "high", "standard")}
    production_files = 0
    for path, value in document["files"].items():
        if not isinstance(path, str) or not isinstance(value, dict):
            raise CoveragePolicyError("coverage files must map paths to objects")
        if "/itchlab_research/" not in path.replace("\\", "/"):
            continue
        summary = value.get("summary")
        if not isinstance(summary, dict):
            raise CoveragePolicyError(f"coverage entry lacks a summary: {path}")
        tier = _tier(path.replace("\\", "/"))
        totals[tier] = totals[tier].plus(summary)
        production_files += 1
    if production_files == 0 or any(total.lines == 0 for total in totals.values()):
        raise CoveragePolicyError(
            "coverage document does not contain every required tier"
        )
    return totals


def enforce(totals: dict[str, Totals]) -> tuple[str, ...]:
    """Return printable evidence or raise when a documented minimum is missed."""
    thresholds = {
        "critical": (90.0, 85.0),
        "high": (85.0, 80.0),
        "standard": (80.0, None),
    }
    failures: list[str] = []
    evidence: list[str] = []
    for tier in ("critical", "high", "standard"):
        total = totals[tier]
        line_minimum, branch_minimum = thresholds[tier]
        fields = [
            f"line {total.line_percent:.2f}% ({total.covered_lines}/{total.lines})"
        ]
        if branch_minimum is not None:
            fields.append(
                f"branch {total.branch_percent:.2f}% "
                f"({total.covered_branches}/{total.branches})"
            )
        evidence.append(f"{tier}: {', '.join(fields)}")
        if total.line_percent < line_minimum:
            failures.append(
                f"{tier} line coverage {total.line_percent:.2f}% is below {line_minimum:.0f}%"
            )
        if branch_minimum is not None and total.branch_percent < branch_minimum:
            failures.append(
                f"{tier} branch coverage {total.branch_percent:.2f}% is below "
                f"{branch_minimum:.0f}%"
            )
    if failures:
        raise CoveragePolicyError("; ".join(failures))
    return tuple(evidence)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path, help="coverage.py JSON report")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate one coverage.py report and print its tier aggregates."""
    options = _parser().parse_args(arguments)
    try:
        document = json.loads(options.coverage_json.read_text(encoding="utf-8"))
        evidence = enforce(coverage_totals(document))
    except (OSError, json.JSONDecodeError, CoveragePolicyError) as error:
        print(f"coverage policy failed: {error}", file=sys.stderr)
        return 1
    print("\n".join(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
