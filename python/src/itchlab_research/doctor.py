"""Read-only installed-environment health checks for ITCH-Lab."""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import Final, Literal

from itchlab_research import __version__

DoctorCheckStatus = Literal["pass", "fail"]

_DEPENDENCIES: Final[tuple[tuple[str, str], ...]] = (
    ("jsonschema", "jsonschema"),
    ("numpy", "numpy"),
    ("pyarrow", "pyarrow"),
    ("rfc8785", "rfc8785"),
    ("scikit-learn", "sklearn"),
)
_SCHEMA_DOCUMENTS: Final[tuple[str, ...]] = (
    "conversion-config.schema.json",
    "conversion-manifest.schema.json",
    "dataset-config.schema.json",
    "dataset-manifest.schema.json",
    "experiment-config.schema.json",
    "experiment-manifest.schema.json",
    "replay-config.schema.json",
    "replay-manifest.schema.json",
    "simulation-config.schema.json",
    "simulation-manifest.schema.json",
)


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One bounded health-check result suitable for human or JSON presentation."""

    name: str
    status: DoctorCheckStatus
    summary: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Complete installed-environment health report."""

    application_version: str
    operating_system: str
    architecture: str
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        """Return whether every required check passed."""
        return all(check.status == "pass" for check in self.checks)


def _check(name: str, passed: bool, summary: str) -> DoctorCheck:
    return DoctorCheck(name=name, status="pass" if passed else "fail", summary=summary)


def _display_path(path: Path) -> str:
    try:
        return Path(os.path.relpath(path, Path.cwd())).as_posix()
    except ValueError:
        return path.name


def _python_check() -> DoctorCheck:
    version = platform.python_version()
    passed = sys.version_info >= (3, 11)
    if passed:
        summary = f"Python {version}."
    else:
        summary = f"Python {version}; version 3.11 or later is required."
    return _check("python", passed, summary)


def _dependency_checks() -> tuple[DoctorCheck, ...]:
    checks: list[DoctorCheck] = []
    for distribution, module_name in _DEPENDENCIES:
        try:
            installed_version = metadata.version(distribution)
            importlib.import_module(module_name)
        except (ImportError, metadata.PackageNotFoundError) as error:
            checks.append(
                _check(
                    f"dependency:{distribution}",
                    False,
                    f"{distribution} is unavailable ({type(error).__name__}).",
                )
            )
        except Exception as error:
            checks.append(
                _check(
                    f"dependency:{distribution}",
                    False,
                    f"{distribution} could not be imported ({type(error).__name__}).",
                )
            )
        else:
            checks.append(
                _check(
                    f"dependency:{distribution}",
                    True,
                    f"{distribution} {installed_version} imports successfully.",
                )
            )
    return tuple(checks)


def _schema_check() -> DoctorCheck:
    try:
        schema_root = resources.files("itchlab_research._schemas")
        for name in _SCHEMA_DOCUMENTS:
            document = json.loads(schema_root.joinpath(name).read_text(encoding="utf-8"))
            version = document.get("properties", {}).get("schema_version", {}).get("const")
            if version != 1:
                return _check(
                    "schemas",
                    False,
                    f"Packaged schema {name} does not declare supported version 1.",
                )
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return _check(
            "schemas",
            False,
            f"Packaged schemas could not be validated ({type(error).__name__}).",
        )
    return _check(
        "schemas",
        True,
        "Config and manifest schema version 1 is supported.",
    )


def _binary_check(binary: Path | None) -> DoctorCheck:
    candidate = str(binary) if binary is not None else shutil.which("itchlab")
    if candidate is None:
        return _check(
            "cpp_binary",
            False,
            "itchlab was not found on PATH; use --binary to select the installed executable.",
        )
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return _check(
            "cpp_binary",
            False,
            f"C++ binary {_display_path(path)} is not an executable regular file.",
        )
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _check(
            "cpp_binary",
            False,
            f"C++ binary could not report its version ({type(error).__name__}).",
        )
    expected = f"itchlab {__version__}\n"
    if completed.returncode != 0 or completed.stdout != expected or completed.stderr:
        return _check(
            "cpp_binary",
            False,
            "C++ binary version output is malformed or does not match the Python package.",
        )
    return _check(
        "cpp_binary",
        True,
        f"itchlab {__version__} is executable at {_display_path(path)}.",
    )


def _writable_directory_check(name: str, path: Path) -> DoctorCheck:
    displayed = _display_path(path)
    try:
        if path.is_symlink():
            return _check(name, False, f"{displayed} is a symlink and is not a safe run root.")
        resolved = path.resolve(strict=True)
    except OSError as error:
        return _check(name, False, f"{displayed} is unavailable ({type(error).__name__}).")
    if not path.is_dir():
        return _check(name, False, f"{displayed} is not a directory.")
    if resolved == Path(resolved.anchor):
        return _check(name, False, f"{displayed} is too broad for generated output.")

    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".itchlab-doctor-", dir=path)
        temporary = Path(temporary_name)
        os.write(descriptor, b"itchlab-doctor\n")
        os.fsync(descriptor)
    except OSError as error:
        return _check(name, False, f"{displayed} is not writable ({type(error).__name__}).")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _check(name, True, f"{displayed} accepts and removes a bounded write probe.")


def run_doctor(*, binary: Path | None = None) -> DoctorReport:
    """Inspect the installed environment without performing research or network access."""
    runs_root = Path(os.environ.get("ITCHLAB_RUNS_DIR", "runs"))
    data_root = Path(os.environ.get("ITCHLAB_DATA_DIR", "data"))
    checks = (
        _python_check(),
        *_dependency_checks(),
        _binary_check(binary),
        _schema_check(),
        _writable_directory_check("runs_root", runs_root),
        _writable_directory_check("derived_root", data_root / "derived"),
    )
    return DoctorReport(
        application_version=__version__,
        operating_system=platform.system() or "unknown",
        architecture=platform.machine() or "unknown",
        checks=checks,
    )


__all__ = ["DoctorCheck", "DoctorReport", "run_doctor"]
