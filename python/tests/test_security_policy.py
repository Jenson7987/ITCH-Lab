"""TASK-028 repository-wide security policy regression tests."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SOURCE = REPOSITORY_ROOT / "python" / "src"
CONFIG_SCHEMAS = REPOSITORY_ROOT / "schemas"

_EXECUTABLE_SERIALISATION = {"dill", "joblib", "marshal", "pickle"}
_NETWORK_IMPORTS = {
    "aiohttp",
    "ftplib",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "telnetlib",
    "urllib.request",
}
_CREDENTIAL_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?key|credential|password|private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_BULK_SUFFIXES = {".joblib", ".parquet", ".pickle", ".pkl"}


def _tracked_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(
        REPOSITORY_ROOT / value.decode("utf-8") for value in completed.stdout.split(b"\0") if value
    )


def _imports(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
            result.update(
                f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
            )
    return result


def _property_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = (
            set(value.get("properties", {})) if isinstance(value.get("properties"), dict) else set()
        )
        for child in value.values():
            result.update(_property_names(child))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for child in value:
            result.update(_property_names(child))
        return result
    return set()


def test_task_028_runtime_source_has_no_executable_serialisation_or_code_execution() -> None:
    violations: list[str] = []
    for path in sorted(PYTHON_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_roots = {name.partition(".")[0] for name in _imports(tree)}
        forbidden_imports = sorted(imported_roots & _EXECUTABLE_SERIALISATION)
        if forbidden_imports:
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {forbidden_imports}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec"}:
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno} calls {node.func.id}"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "load"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"np", "numpy"}
            ):
                allow_pickle = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "allow_pickle"),
                    None,
                )
                if not isinstance(allow_pickle, ast.Constant) or allow_pickle.value is not False:
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno} uses unsafe numpy.load"
                    )
    assert violations == []


def test_task_028_runtime_source_declares_no_network_client_surface() -> None:
    violations: list[str] = []
    for path in sorted(PYTHON_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = sorted(_imports(tree) & _NETWORK_IMPORTS)
        if forbidden:
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} imports {forbidden}")

    network_headers = re.compile(
        r"#\s*include\s*[<\"](?:arpa/inet|boost/asio|curl|netinet|sys/socket)",
    )
    for path in sorted((REPOSITORY_ROOT / "cpp").rglob("*")):
        if path.suffix in {".cpp", ".hpp"} and network_headers.search(
            path.read_text(encoding="utf-8")
        ):
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)} includes a network API")
    assert violations == []


def test_task_028_config_schemas_expose_no_credential_fields() -> None:
    names: set[str] = set()
    for path in sorted(CONFIG_SCHEMAS.glob("*-config.schema.json")):
        names.update(_property_names(json.loads(path.read_text(encoding="utf-8"))))
    assert sorted(name for name in names if _CREDENTIAL_KEY.search(name)) == []


def test_task_028_tracked_data_is_synthetic_and_bulk_outputs_are_absent() -> None:
    violations: list[str] = []
    for path in _tracked_files():
        relative = path.relative_to(REPOSITORY_ROOT)
        relative_text = relative.as_posix()
        if relative.parts[:2] in {("data", "raw"), ("data", "derived")} or relative.parts[:1] == (
            "runs",
        ):
            if relative.name != ".gitkeep":
                violations.append(relative_text)
        if path.suffix in _BULK_SUFFIXES:
            violations.append(relative_text)
        if relative_text.endswith((".itch", ".itch.gz")):
            if relative.parts[:2] != ("tests", "fixtures") or not relative.name.startswith(
                ("synthetic_",)
            ):
                violations.append(relative_text)
    assert sorted(set(violations)) == []


def test_task_028_public_examples_contain_no_repository_or_home_path() -> None:
    private_values = {str(REPOSITORY_ROOT), str(Path.home())}
    candidates = [
        *sorted((REPOSITORY_ROOT / "configs").glob("*.json")),
        *sorted((REPOSITORY_ROOT / "tests" / "golden").rglob("*.json")),
    ]
    for path in candidates:
        content = path.read_text(encoding="utf-8")
        assert all(value not in content for value in private_values), path


@pytest.mark.parametrize("document", sorted(CONFIG_SCHEMAS.glob("*-config.schema.json")))
def test_task_028_root_and_packaged_security_scanned_schemas_match(document: Path) -> None:
    packaged = PYTHON_SOURCE / "itchlab_research" / "_schemas" / document.name
    assert packaged.read_bytes() == document.read_bytes()
