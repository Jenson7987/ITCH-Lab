#!/usr/bin/env python3
"""Build checked, deterministic TASK-030 release artefacts without publishing them."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Final

import tomllib

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
_VERSION_PATTERN: Final = re.compile(
    r'^__version__:\s*Final\s*=\s*"([^"]+)"$', re.MULTILINE
)
_FORBIDDEN_PREFIXES: Final = (
    PurePosixPath("data/raw"),
    PurePosixPath("data/derived"),
    PurePosixPath("runs"),
)
_REQUIRED_SOURCE_FILES: Final = (
    PurePosixPath("LICENSE"),
    PurePosixPath("README.md"),
    PurePosixPath("THIRD_PARTY_NOTICES.md"),
    PurePosixPath("docs/09-deployment.md"),
)


class ReleaseError(RuntimeError):
    """Expected release-validation or build failure."""


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path = REPOSITORY_ROOT,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise ReleaseError(f"command failed ({arguments[0]}): {detail}")
    return completed


def repository_versions(root: Path = REPOSITORY_ROOT) -> dict[str, str]:
    """Return independently declared public versions."""
    cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    cmake_match = re.search(r"project\(\s*itchlab\s+VERSION\s+([^\s)]+)", cmake)
    if cmake_match is None:
        raise ReleaseError("CMake project version is missing")
    pyproject = tomllib.loads(
        (root / "python" / "pyproject.toml").read_text(encoding="utf-8")
    )
    python_version = pyproject.get("project", {}).get("version")
    package = (root / "python/src/itchlab_research/__init__.py").read_text(
        encoding="utf-8"
    )
    package_match = _VERSION_PATTERN.search(package)
    if not isinstance(python_version, str) or package_match is None:
        raise ReleaseError("Python package version is missing")
    return {
        "cmake": cmake_match.group(1),
        "pyproject": python_version,
        "python_package": package_match.group(1),
    }


def checked_version(root: Path = REPOSITORY_ROOT) -> str:
    """Require all public version declarations to agree."""
    versions = repository_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        rendered = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ReleaseError(f"public versions do not agree: {rendered}")
    return unique.pop()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_output_root(path: Path, root: Path = REPOSITORY_ROOT) -> Path:
    """Resolve a new, narrow output root and reject aliases or unsafe locations."""
    lexical = path.absolute()
    repository = root.resolve(strict=True)
    if lexical == Path(lexical.anchor) or lexical == repository:
        raise ReleaseError("release output root is too broad")
    if path.exists() or path.is_symlink():
        raise ReleaseError("release output root must not already exist")
    current = path
    while not current.exists():
        if current.is_symlink():
            raise ReleaseError("release output root must not traverse a symlink")
        if current.parent == current:
            break
        current = current.parent
    if current.is_symlink():
        raise ReleaseError("release output root must not traverse a symlink")
    resolved = path.resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved == repository:
        raise ReleaseError("release output root is too broad")
    for relative in _FORBIDDEN_PREFIXES:
        if _is_relative_to(resolved, repository / relative):
            raise ReleaseError(
                f"release output root may not be beneath {relative.as_posix()}"
            )
    return resolved


def _forbidden_member(path: PurePosixPath) -> bool:
    if path.is_absolute() or ".." in path.parts or ".DS_Store" in path.parts:
        return True
    for prefix in _FORBIDDEN_PREFIXES:
        width = len(prefix.parts)
        if any(
            path.parts[index : index + width] == prefix.parts
            for index in range(len(path.parts))
        ):
            return True
    return False


def source_files(root: Path = REPOSITORY_ROOT) -> tuple[PurePosixPath, ...]:
    """List version-controlled and candidate files allowed in a source release."""
    completed = _run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    paths: list[PurePosixPath] = []
    for value in completed.stdout.split("\0"):
        if not value:
            continue
        relative = PurePosixPath(value)
        candidate = root / relative
        if _forbidden_member(relative):
            continue
        if candidate.is_symlink():
            raise ReleaseError(f"source release refuses symlink {relative.as_posix()}")
        if candidate.is_file():
            paths.append(relative)
    missing = [path.as_posix() for path in _REQUIRED_SOURCE_FILES if path not in paths]
    if missing:
        raise ReleaseError(
            f"source release is missing required files: {', '.join(missing)}"
        )
    return tuple(sorted(paths, key=PurePosixPath.as_posix))


def _normalised_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644


def _tar_bytes(
    *,
    root: Path,
    members: Iterable[tuple[PurePosixPath, Path | bytes]],
    prefix: PurePosixPath,
    epoch: int,
) -> bytes:
    archive_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=archive_buffer, mode="w", format=tarfile.PAX_FORMAT
    ) as archive:
        for relative, source in sorted(members, key=lambda item: item[0].as_posix()):
            archive_name = prefix / relative
            if _forbidden_member(relative) or _forbidden_member(archive_name):
                raise ReleaseError(
                    f"refusing unsafe archive member {archive_name.as_posix()}"
                )
            if isinstance(source, bytes):
                payload = source
                mode = 0o644
            else:
                resolved_source = source.resolve(strict=True)
                if not resolved_source.is_file() or source.is_symlink():
                    raise ReleaseError(
                        f"archive source is not a regular file: {relative.as_posix()}"
                    )
                payload = resolved_source.read_bytes()
                mode = _normalised_mode(resolved_source)
            info = tarfile.TarInfo(archive_name.as_posix())
            info.size = len(payload)
            info.mode = mode
            info.mtime = epoch
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            archive.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, mtime=epoch
    ) as output:
        output.write(archive_buffer.getvalue())
    return compressed.getvalue()


def _write_archive(
    path: Path,
    *,
    root: Path,
    members: Iterable[tuple[PurePosixPath, Path | bytes]],
    prefix: PurePosixPath,
    epoch: int,
) -> None:
    path.write_bytes(_tar_bytes(root=root, members=members, prefix=prefix, epoch=epoch))
    validate_archive(path)


def validate_archive(path: Path) -> None:
    """Fail closed on unsafe, linked, raw or bulk archive members."""
    with tarfile.open(path, mode="r:gz") as archive:
        names: set[str] = set()
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if (
                _forbidden_member(relative)
                or not (member.isfile() or member.isdir())
                or member.issym()
                or member.islnk()
            ):
                raise ReleaseError(f"unsafe release archive member: {member.name}")
            if member.name in names:
                raise ReleaseError(f"duplicate release archive member: {member.name}")
            names.add(member.name)


def _validate_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names: set[str] = set()
        for member in archive.infolist():
            name = member.filename
            mode = member.external_attr >> 16
            if (
                _forbidden_member(PurePosixPath(name))
                or stat.S_IFMT(mode) == stat.S_IFLNK
                or name in names
            ):
                raise ReleaseError(f"unsafe Python wheel member: {name}")
            names.add(name)


def _platform_tag() -> tuple[str, str]:
    operating_system = platform.system()
    architecture = platform.machine().lower()
    if operating_system == "Darwin" and architecture in {"arm64", "aarch64"}:
        return "macos", "arm64"
    if operating_system == "Linux" and architecture in {"x86_64", "amd64"}:
        return "linux", "x86_64"
    raise ReleaseError(
        f"unsupported release platform: {operating_system or 'unknown'} {architecture or 'unknown'}"
    )


def _binary_matches_architecture(description: str, architecture: str) -> bool:
    normalised = description.lower()
    aliases = {
        "arm64": ("arm64", "aarch64"),
        "x86_64": ("x86-64", "x86_64", "amd64"),
    }
    return any(alias in normalised for alias in aliases[architecture])


def _build_native(build_root: Path, install_root: Path, *, epoch: int) -> Path:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    configure = [
        "cmake",
        "-S",
        str(REPOSITORY_ROOT),
        "-B",
        str(build_root),
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=OFF",
        "-DITCHLAB_BUILD_BENCHMARKS=OFF",
        f"-DCMAKE_INSTALL_PREFIX={install_root}",
    ]
    if platform.system() == "Darwin":
        configure.extend(
            ["-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0"]
        )
    _run(configure, environment=environment)
    _run(
        ["cmake", "--build", str(build_root), "--target", "itchlab"],
        environment=environment,
    )
    _run(
        ["cmake", "--install", str(build_root), "--component", "runtime"],
        environment=environment,
    )
    binary = install_root / "bin" / "itchlab"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ReleaseError("CMake install did not publish an executable itchlab binary")
    return binary


def _build_python(
    build_root: Path, source_paths: tuple[PurePosixPath, ...]
) -> tuple[Path, Path]:
    staged_source = build_root / "source"
    for relative in source_paths:
        source = REPOSITORY_ROOT / relative
        target = staged_source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    output = build_root / "python-dist"
    output.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(output),
            str(staged_source / "python"),
        ]
    )
    wheels = tuple(output.glob("*.whl"))
    source_distributions = tuple(output.glob("*.tar.gz"))
    if len(wheels) != 1 or len(source_distributions) != 1:
        raise ReleaseError(
            "Python build did not produce exactly one wheel and source distribution"
        )
    _validate_wheel(wheels[0])
    validate_archive(source_distributions[0])
    return wheels[0], source_distributions[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release(output_root: Path, *, allow_dirty_candidate: bool) -> Path:
    """Build all release artefacts into a newly atomically published directory."""
    output = validate_output_root(output_root)
    version = checked_version()
    source_paths = source_files()
    revision = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    dirty = bool(_run(["git", "status", "--porcelain", "--untracked-files=all"]).stdout)
    if dirty and not allow_dirty_candidate:
        raise ReleaseError("release candidates require a clean working tree")
    epoch_text = _run(["git", "show", "-s", "--format=%ct", "HEAD"]).stdout.strip()
    if not epoch_text.isdigit():
        raise ReleaseError("release source epoch is unavailable")
    epoch = int(epoch_text)
    system_tag, architecture_tag = _platform_tag()
    partial = output.with_name(f"{output.name}.partial")
    if partial.exists() or partial.is_symlink():
        raise ReleaseError("release staging root already exists")
    partial.mkdir(parents=True)

    try:
        with tempfile.TemporaryDirectory(
            prefix="itchlab-release-build-"
        ) as temporary_name:
            temporary = Path(temporary_name)
            binary = _build_native(
                temporary / "cmake", temporary / "install", epoch=epoch
            )
            binary_version = _run([str(binary), "--version"]).stdout
            if binary_version != f"itchlab {version}\n":
                raise ReleaseError(
                    "installed native binary version does not match package version"
                )
            file_description = _run(["file", "-b", str(binary)]).stdout.strip()
            if not _binary_matches_architecture(file_description, architecture_tag):
                raise ReleaseError(
                    f"native binary architecture does not match {architecture_tag}: {file_description}"
                )
            wheel, python_sdist = _build_python(temporary, source_paths)

            metadata = {
                "schema_version": 1,
                "application_version": version,
                "git_revision": revision,
                "git_dirty": dirty,
                "publishable": not dirty,
                "platform": system_tag,
                "architecture": architecture_tag,
                "source_date_epoch": epoch,
                "binary_description": file_description,
                "python": platform.python_version(),
            }
            metadata_bytes = (
                json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
            ).encode("utf-8")
            prefix = PurePosixPath(f"itchlab-{version}")
            source_archive = partial / f"itchlab-{version}-source.tar.gz"
            source_members: list[tuple[PurePosixPath, Path | bytes]] = [
                (relative, REPOSITORY_ROOT / relative) for relative in source_paths
            ]
            source_members.append(
                (PurePosixPath("RELEASE-METADATA.json"), metadata_bytes)
            )
            _write_archive(
                source_archive,
                root=REPOSITORY_ROOT,
                members=source_members,
                prefix=prefix,
                epoch=epoch,
            )

            native_archive = (
                partial / f"itchlab-{version}-{system_tag}-{architecture_tag}.tar.gz"
            )
            native_members: list[tuple[PurePosixPath, Path | bytes]] = [
                (PurePosixPath("bin/itchlab"), binary),
                (PurePosixPath("README.md"), REPOSITORY_ROOT / "README.md"),
                (
                    PurePosixPath("THIRD_PARTY_NOTICES.md"),
                    REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md",
                ),
                (
                    PurePosixPath("docs/09-deployment.md"),
                    REPOSITORY_ROOT / "docs/09-deployment.md",
                ),
                (PurePosixPath("RELEASE-METADATA.json"), metadata_bytes),
            ]
            _write_archive(
                native_archive,
                root=REPOSITORY_ROOT,
                members=native_members,
                prefix=prefix,
                epoch=epoch,
            )
            shutil.copy2(wheel, partial / wheel.name)
            shutil.copy2(python_sdist, partial / python_sdist.name)
            (partial / "RELEASE-METADATA.json").write_bytes(metadata_bytes)

        artefacts = sorted(
            path for path in partial.iterdir() if path.name not in {"SHA256SUMS"}
        )
        checksums = "".join(f"{_sha256(path)}  {path.name}\n" for path in artefacts)
        (partial / "SHA256SUMS").write_text(checksums, encoding="ascii")
        os.replace(partial, output)
    except OSError as error:
        raise ReleaseError(
            f"release staging failed ({type(error).__name__})"
        ) from error
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--allow-dirty-candidate",
        action="store_true",
        help="build an explicitly non-publishable local candidate from a dirty worktree",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        output = build_release(
            options.output_root,
            allow_dirty_candidate=options.allow_dirty_candidate,
        )
    except ReleaseError as error:
        print(f"release build failed: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
