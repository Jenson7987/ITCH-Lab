#!/usr/bin/env python3
"""Generate the small maintained TASK-028 framing and decoder fuzz corpus."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPOSITORY_ROOT / "tests" / "fuzz" / "corpus"
MIXED_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "synthetic_mixed.itch"
MINIMAL_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "synthetic_minimal.itch"
SUPPORTED_TYPES = b"SRHAFECXDUPQB"
IGNORED_LENGTHS = {
    ord("Y"): 20,
    ord("L"): 26,
    ord("V"): 35,
    ord("W"): 12,
    ord("K"): 28,
    ord("I"): 50,
    ord("N"): 20,
    ord("J"): 35,
    ord("h"): 21,
}


def _frames(content: bytes) -> tuple[bytes, ...]:
    result: list[bytes] = []
    offset = 0
    while offset < len(content):
        if offset + 2 > len(content):
            raise ValueError("fixture ends within a frame prefix")
        length = int.from_bytes(content[offset : offset + 2], "big")
        end = offset + 2 + length
        if length == 0 or end > len(content):
            raise ValueError("fixture contains an invalid frame")
        result.append(content[offset + 2 : end])
        offset = end
    return tuple(result)


def _expected_files() -> dict[Path, bytes]:
    mixed = MIXED_FIXTURE.read_bytes()
    frames = _frames(mixed)
    first_by_type: dict[int, bytes] = {}
    for payload in frames:
        first_by_type.setdefault(payload[0], payload)
    missing = [chr(value) for value in SUPPORTED_TYPES if value not in first_by_type]
    if missing:
        raise ValueError(f"mixed fixture lacks supported types: {', '.join(missing)}")

    expected = {
        CORPUS_ROOT / "framing" / "empty.bin": b"",
        CORPUS_ROOT / "framing" / "one-prefix-byte.bin": b"\x00",
        CORPUS_ROOT / "framing" / "zero-length.bin": b"\x00\x00",
        CORPUS_ROOT / "framing" / "truncated-payload.bin": b"\x00\x0cS\x00\x00",
        CORPUS_ROOT / "framing" / "maximum-frame.bin": b"\x02\x00" + b"Z" * 512,
        CORPUS_ROOT / "framing" / "oversized-frame.bin": b"\x02\x01",
        CORPUS_ROOT / "framing" / "synthetic-minimal.bin": MINIMAL_FIXTURE.read_bytes(),
        CORPUS_ROOT / "framing" / "synthetic-mixed.bin": mixed,
        CORPUS_ROOT / "decoder" / "empty.bin": b"",
        CORPUS_ROOT / "decoder" / "unknown-type.bin": b"Z",
        CORPUS_ROOT / "decoder" / "wrong-known-length.bin": b"A",
    }
    for source_type in SUPPORTED_TYPES:
        expected[CORPUS_ROOT / "decoder" / f"type-{chr(source_type)}.bin"] = (
            first_by_type[source_type]
        )
    for source_type, length in IGNORED_LENGTHS.items():
        payload = bytearray(length)
        payload[0] = source_type
        name = "lower-h" if source_type == ord("h") else chr(source_type)
        expected[CORPUS_ROOT / "decoder" / f"type-{name}.bin"] = bytes(payload)
    invalid_timestamp = bytearray(first_by_type[ord("S")])
    invalid_timestamp[5:11] = b"\xff" * 6
    expected[CORPUS_ROOT / "decoder" / "invalid-timestamp.bin"] = bytes(
        invalid_timestamp
    )
    return expected


def _validate_destination(path: Path) -> None:
    resolved_parent = path.parent.resolve(strict=False)
    corpus = CORPUS_ROOT.resolve(strict=False)
    if corpus != resolved_parent and corpus not in resolved_parent.parents:
        raise ValueError(f"corpus output escapes explicit root: {path}")


def _check(expected: dict[Path, bytes]) -> None:
    actual = {path for path in CORPUS_ROOT.rglob("*") if path.is_file()}
    if actual != set(expected):
        missing = sorted(
            str(path.relative_to(REPOSITORY_ROOT)) for path in set(expected) - actual
        )
        extra = sorted(
            str(path.relative_to(REPOSITORY_ROOT)) for path in actual - set(expected)
        )
        raise SystemExit(
            f"fuzz corpus file set mismatch; missing={missing}; extra={extra}"
        )
    for path, content in expected.items():
        if path.read_bytes() != content:
            raise SystemExit(
                f"fuzz corpus content mismatch: {path.relative_to(REPOSITORY_ROOT)}"
            )


def _write(expected: dict[Path, bytes]) -> None:
    for path, content in expected.items():
        _validate_destination(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        with partial.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    arguments = parser.parse_args()
    expected = _expected_files()
    if arguments.check:
        _check(expected)
    else:
        _write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
