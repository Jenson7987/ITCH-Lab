"""TASK-030 release packaging policy tests."""

from __future__ import annotations

import importlib.util
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _release_module() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts/release/build_release.py"
    specification = importlib.util.spec_from_file_location("task030_build_release", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_task_030_public_versions_agree() -> None:
    release = _release_module()
    assert release.repository_versions() == {
        "cmake": "0.1.3",
        "pyproject": "0.1.3",
        "python_package": "0.1.3",
    }
    assert release.checked_version() == "0.1.3"


@pytest.mark.parametrize(
    ("description", "architecture", "expected"),
    [
        ("Mach-O 64-bit executable arm64", "arm64", True),
        ("ELF 64-bit LSB pie executable, x86-64", "x86_64", True),
        ("ELF 64-bit LSB pie executable, ARM aarch64", "arm64", True),
        ("Mach-O 64-bit executable x86_64", "arm64", False),
    ],
)
def test_task_030_native_file_descriptions_match_release_architecture(
    description: str,
    architecture: str,
    expected: bool,
) -> None:
    release = _release_module()
    assert release._binary_matches_architecture(description, architecture) is expected


def test_task_030_source_inventory_excludes_raw_bulk_and_operating_system_files() -> None:
    release = _release_module()
    files = release.source_files()

    assert PurePosixPath("LICENSE") in files
    assert PurePosixPath("README.md") in files
    assert PurePosixPath("THIRD_PARTY_NOTICES.md") in files
    assert all(".DS_Store" not in path.parts for path in files)
    assert all(not release._forbidden_member(path) for path in files)


def test_task_030_release_output_root_must_be_new_narrow_and_non_data(
    tmp_path: Path,
) -> None:
    release = _release_module()
    safe = release.validate_output_root(tmp_path / "release-candidate")
    assert safe == (tmp_path / "release-candidate").resolve()

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(release.ReleaseError, match="must not already exist"):
        release.validate_output_root(existing)
    with pytest.raises(release.ReleaseError, match="too broad"):
        release.validate_output_root(REPOSITORY_ROOT)
    with pytest.raises(release.ReleaseError, match="data/raw"):
        release.validate_output_root(REPOSITORY_ROOT / "data/raw/release")


def test_task_030_archive_is_deterministic_and_rejects_unsafe_members(tmp_path: Path) -> None:
    release = _release_module()
    payload = tmp_path / "payload.txt"
    payload.write_text("synthetic\n", encoding="utf-8")
    members = [(PurePosixPath("payload.txt"), payload)]
    first = release._tar_bytes(
        root=tmp_path,
        members=members,
        prefix=PurePosixPath("itchlab-0.1.3"),
        epoch=1_700_000_000,
    )
    second = release._tar_bytes(
        root=tmp_path,
        members=members,
        prefix=PurePosixPath("itchlab-0.1.3"),
        epoch=1_700_000_000,
    )
    assert first == second

    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        archive.add(payload, arcname="itchlab-0.1.3/data/raw/payload.itch")
    with pytest.raises(release.ReleaseError, match="unsafe release archive member"):
        release.validate_archive(archive_path)

    with pytest.raises(release.ReleaseError, match="unsafe archive member"):
        release._tar_bytes(
            root=tmp_path,
            members=[(PurePosixPath("data/derived/payload.parquet"), payload)],
            prefix=PurePosixPath("itchlab-0.1.3"),
            epoch=1_700_000_000,
        )


def test_task_030_wheel_validation_rejects_links_and_raw_members(tmp_path: Path) -> None:
    release = _release_module()
    linked_wheel = tmp_path / "linked.whl"
    with zipfile.ZipFile(linked_wheel, mode="w") as archive:
        member = zipfile.ZipInfo("itchlab_research/payload")
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "target")
    with pytest.raises(release.ReleaseError, match="unsafe Python wheel member"):
        release._validate_wheel(linked_wheel)

    raw_wheel = tmp_path / "raw.whl"
    with zipfile.ZipFile(raw_wheel, mode="w") as archive:
        archive.writestr("data/raw/source.itch", b"synthetic")
    with pytest.raises(release.ReleaseError, match="unsafe Python wheel member"):
        release._validate_wheel(raw_wheel)
