#!/usr/bin/env python3
"""Validate the v0.1.0 requirement and task traceability contracts."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

REQUIREMENT_SOURCES = (
    (Path("docs/01-product-requirements.md"), frozenset({"FR", "NFR"})),
    (Path("docs/07-security-and-privacy.md"), frozenset({"SEC"})),
)
TRACEABILITY_PATH = Path("docs/11-traceability.md")
TESTING_PATH = Path("docs/08-testing-strategy.md")
IMPLEMENTATION_PLAN_PATH = Path("docs/10-implementation-plan.md")
TASKS_PATH = Path("TASKS.md")

EXPECTED_REQUIREMENTS = frozenset(
    {
        *(f"FR-{number:03d}" for number in range(1, 23)),
        *(f"NFR-{number:03d}" for number in range(1, 13)),
        *(f"SEC-{number:03d}" for number in range(1, 13)),
    }
)
EXPECTED_TASKS = frozenset(f"TASK-{number:03d}" for number in range(1, 33))

REQUIREMENT_ROW = re.compile(r"^\|\s*((?:FR|NFR|SEC)-\d{3})\s*\|")
TASK_HEADING = re.compile(r"^####\s+(TASK-\d{3})\b", re.MULTILINE)
TASK_CHECKBOX = re.compile(r"^- \[(?P<state>[ x])\] (?P<id>TASK-\d{3}):", re.MULTILINE)
TASK_REFERENCE = re.compile(r"TASK-(\d{3})(?:[\N{EN DASH}-](?:TASK-)?(\d{3}))?")
TEST_ID = re.compile(r"\b(?:UT|IT|CT|E2E|PERF|SEC)(?:-[A-Z]+)*-\d{3}\b")
EVIDENCE_TERM = re.compile(
    r"\b(?:assertions?|audits?|benchmarks?|checks?|contracts?|coverage|fuzz|gates?|"
    r"fixtures?|goldens?|lint|matri(?:x|ces)|polic(?:y|ies)|properties|reviews?|saniti[sz]ers?|"
    r"smokes?|tests?|validation)\b",
    re.IGNORECASE,
)
DESIGN_REFERENCE = re.compile(r"(?:\b(?:0[1-9]|1[0-2])\b|ADR-\d{3}|AGENTS\.md)")


@dataclass(frozen=True)
class TraceRow:
    """One requirement mapping from the traceability matrix."""

    requirement_id: str
    design: str
    tasks: str
    verification: str
    line: int


def _read(repository_root: Path, relative: Path, issues: list[str]) -> str:
    path = repository_root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        issues.append(
            f"{relative.as_posix()}: cannot read UTF-8 text ({type(error).__name__})"
        )
        return ""


def _duplicates(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def _format_ids(values: set[str] | frozenset[str]) -> str:
    return ", ".join(sorted(values))


def _requirement_ids(text: str, prefixes: frozenset[str]) -> list[str]:
    identifiers: list[str] = []
    for line in text.splitlines():
        match = REQUIREMENT_ROW.match(line)
        if match is not None and match.group(1).split("-", 1)[0] in prefixes:
            identifiers.append(match.group(1))
    return identifiers


def _trace_rows(text: str, issues: list[str]) -> list[TraceRow]:
    rows: list[TraceRow] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = REQUIREMENT_ROW.match(line)
        if match is None:
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) != 4:
            issues.append(
                f"{TRACEABILITY_PATH.as_posix()}:{number}: requirement row must have four columns"
            )
            continue
        rows.append(TraceRow(cells[0], cells[1], cells[2], cells[3], number))
    return rows


def _task_references(value: str, location: str, issues: list[str]) -> set[str]:
    references: set[str] = set()
    for match in TASK_REFERENCE.finditer(value):
        start = int(match.group(1))
        end_text = match.group(2)
        end = int(end_text) if end_text is not None else start
        if end < start:
            issues.append(f"{location}: descending task range {match.group(0)}")
            continue
        references.update(f"TASK-{number:03d}" for number in range(start, end + 1))
    return references


def _check_exact_set(
    *,
    actual: set[str],
    expected: frozenset[str],
    label: str,
    issues: list[str],
) -> None:
    missing = expected - actual
    unexpected = actual - expected
    if missing:
        issues.append(f"{label}: missing identifiers: {_format_ids(missing)}")
    if unexpected:
        issues.append(f"{label}: unexpected identifiers: {_format_ids(unexpected)}")


def _check_completed_task_evidence(tasks_text: str, issues: list[str]) -> set[str]:
    matches = tuple(TASK_CHECKBOX.finditer(tasks_text))
    identifiers = [match.group("id") for match in matches]
    for duplicate in _duplicates(identifiers):
        issues.append(f"{TASKS_PATH.as_posix()}: duplicate checklist item {duplicate}")
    for index, match in enumerate(matches):
        if match.group("state") != "x":
            continue
        end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(tasks_text)
        )
        block = tasks_text[match.end() : end]
        if re.search(r"^\s+- Evidence:\s+\S", block, re.MULTILINE) is None:
            line = tasks_text.count("\n", 0, match.start()) + 1
            issues.append(
                f"{TASKS_PATH.as_posix()}:{line}: completed {match.group('id')} lacks an Evidence entry"
            )
    return set(identifiers)


def check_repository(repository_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    """Return deterministic traceability diagnostics for ``repository_root``."""
    issues: list[str] = []

    source_identifiers: list[str] = []
    for relative, prefixes in REQUIREMENT_SOURCES:
        text = _read(repository_root, relative, issues)
        identifiers = _requirement_ids(text, prefixes)
        for duplicate in _duplicates(identifiers):
            issues.append(f"{relative.as_posix()}: duplicate requirement {duplicate}")
        source_identifiers.extend(identifiers)
    for duplicate in _duplicates(source_identifiers):
        issues.append(f"requirement sources: duplicate requirement {duplicate}")
    source_set = set(source_identifiers)
    _check_exact_set(
        actual=source_set,
        expected=EXPECTED_REQUIREMENTS,
        label="requirement sources",
        issues=issues,
    )

    trace_text = _read(repository_root, TRACEABILITY_PATH, issues)
    rows = _trace_rows(trace_text, issues)
    trace_identifiers = [row.requirement_id for row in rows]
    for duplicate in _duplicates(trace_identifiers):
        issues.append(
            f"{TRACEABILITY_PATH.as_posix()}: duplicate mapping for {duplicate}"
        )
    trace_set = set(trace_identifiers)
    missing_mappings = source_set - trace_set
    extra_mappings = trace_set - source_set
    if missing_mappings:
        issues.append(
            f"traceability matrix: missing mappings: {_format_ids(missing_mappings)}"
        )
    if extra_mappings:
        issues.append(
            f"traceability matrix: undefined mappings: {_format_ids(extra_mappings)}"
        )

    plan_text = _read(repository_root, IMPLEMENTATION_PLAN_PATH, issues)
    plan_identifiers = TASK_HEADING.findall(plan_text)
    for duplicate in _duplicates(plan_identifiers):
        issues.append(
            f"{IMPLEMENTATION_PLAN_PATH.as_posix()}: duplicate task {duplicate}"
        )
    plan_set = set(plan_identifiers)
    _check_exact_set(
        actual=plan_set,
        expected=EXPECTED_TASKS,
        label="implementation plan",
        issues=issues,
    )

    tasks_text = _read(repository_root, TASKS_PATH, issues)
    checklist_set = _check_completed_task_evidence(tasks_text, issues)
    _check_exact_set(
        actual=checklist_set,
        expected=EXPECTED_TASKS,
        label="task checklist",
        issues=issues,
    )
    if plan_set != checklist_set:
        missing_tasks = plan_set - checklist_set
        extra_tasks = checklist_set - plan_set
        if missing_tasks:
            issues.append(
                f"task checklist: missing planned tasks: {_format_ids(missing_tasks)}"
            )
        if extra_tasks:
            issues.append(
                f"task checklist: unplanned tasks: {_format_ids(extra_tasks)}"
            )

    testing_text = _read(repository_root, TESTING_PATH, issues)
    referenced_tasks: set[str] = set()
    for row in rows:
        location = f"{TRACEABILITY_PATH.as_posix()}:{row.line} ({row.requirement_id})"
        if not row.design or DESIGN_REFERENCE.search(row.design) is None:
            issues.append(f"{location}: lacks an accepted design reference")
        task_references = _task_references(row.tasks, location, issues)
        if not task_references:
            issues.append(f"{location}: lacks an implementation task")
        undefined_tasks = task_references - plan_set
        if undefined_tasks:
            issues.append(
                f"{location}: undefined tasks: {_format_ids(undefined_tasks)}"
            )
        referenced_tasks.update(task_references)
        test_ids = set(TEST_ID.findall(row.verification))
        if not row.verification or (
            not test_ids and EVIDENCE_TERM.search(row.verification) is None
        ):
            issues.append(f"{location}: lacks executable test or review evidence")
        for test_id in sorted(test_ids):
            if test_id not in testing_text:
                issues.append(
                    f"{location}: test ID is not defined by testing strategy: {test_id}"
                )
        if (
            "official" in row.verification.casefold()
            and not test_ids
            and "review" not in row.verification.casefold()
        ):
            issues.append(
                f"{location}: verification depends only on inaccessible official data"
            )

    undefined_referenced_tasks = referenced_tasks - checklist_set
    if undefined_referenced_tasks:
        issues.append(
            "traceability matrix: tasks absent from checklist: "
            f"{_format_ids(undefined_referenced_tasks)}"
        )

    return tuple(issues)


def main() -> int:
    """Check repository traceability and emit all failures together."""
    issues = check_repository()
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print(
        "Traceability checks passed for "
        f"{len(EXPECTED_REQUIREMENTS)} requirements and {len(EXPECTED_TASKS)} tasks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
