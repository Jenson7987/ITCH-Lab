#!/usr/bin/env python3
"""Check local Markdown links, whitespace, fences and Mermaid diagram declarations."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
MERMAID_STARTS = (
    "architecture",
    "block",
    "classDiagram",
    "erDiagram",
    "flowchart",
    "gantt",
    "gitGraph",
    "graph",
    "journey",
    "kanban",
    "mindmap",
    "packet",
    "pie",
    "quadrantChart",
    "requirementDiagram",
    "sankey-beta",
    "sequenceDiagram",
    "stateDiagram",
    "timeline",
    "xychart-beta",
)


def markdown_paths() -> tuple[Path, ...]:
    """Return repository Markdown candidates without scanning ignored artefacts."""
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "*.md",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        REPOSITORY_ROOT / name
        for name in completed.stdout.split("\0")
        if name and (REPOSITORY_ROOT / name).is_file()
    )


def _link_issue(markdown: Path, target: str) -> str | None:
    target = target.strip("<>")
    if target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    local = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not local:
        return None
    destination = (markdown.parent / local).resolve(strict=False)
    try:
        destination.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return f"local link escapes the repository: {target}"
    if not destination.exists():
        return f"local link does not exist: {target}"
    return None


def check_markdown(markdown: Path) -> tuple[str, ...]:
    """Return deterministic lint diagnostics for one Markdown file."""
    relative = markdown.relative_to(REPOSITORY_ROOT).as_posix()
    try:
        text = markdown.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return (f"{relative}: cannot read UTF-8 Markdown ({type(error).__name__})",)
    issues: list[str] = []
    in_fence = False
    mermaid_start: int | None = None
    mermaid_content: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip(" \t") != line:
            issues.append(f"{relative}:{number}: trailing whitespace")
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                if stripped == "```mermaid":
                    mermaid_start = number
                    mermaid_content = []
            else:
                if mermaid_start is not None:
                    first = next(
                        (item.strip() for item in mermaid_content if item.strip()), ""
                    )
                    if not first.startswith(MERMAID_STARTS):
                        issues.append(
                            f"{relative}:{mermaid_start}: Mermaid block lacks a recognised declaration"
                        )
                    mermaid_start = None
                in_fence = False
            continue
        if mermaid_start is not None:
            mermaid_content.append(line)
    if in_fence:
        issues.append(f"{relative}: unclosed fenced code block")
    for match in LINK_PATTERN.finditer(text):
        issue = _link_issue(markdown, match.group(1))
        if issue is not None:
            line = text.count("\n", 0, match.start()) + 1
            issues.append(f"{relative}:{line}: {issue}")
    return tuple(issues)


def main() -> int:
    """Lint repository Markdown and emit all failures together."""
    issues = [issue for path in markdown_paths() for issue in check_markdown(path)]
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print(f"Documentation checks passed for {len(markdown_paths())} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
