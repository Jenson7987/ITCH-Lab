"""Generate or verify the committed independent synthetic ITCH 5.0 corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from tests.fixtures.itch50_builder import (
    BuiltMessage,
    BuiltStream,
    build_stream,
    corrupt_gzip_checksum,
    deterministic_gzip,
    frame_payload,
    frame_with_observed_payload_length,
    oversized_frame_prefix,
    replace_message_type,
    truncated_gzip,
    truncated_length_prefix,
    truncated_payload_frame,
    zero_length_frame,
)
from tests.fixtures.itch50_definitions import INVALID_LIFECYCLES, VALID_STREAMS

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
SPECIFICATION_URL: Final = (
    "https://www.nasdaqtrader.com/content/technicalsupport/specifications/"
    "dataproducts/NQTVITCHspecification.pdf"
)
EXPECTED_PATH: Final = Path("tests/golden/itch50/synthetic_expected.json")
HASH_PATH: Final = Path("tests/golden/itch50/fixture_sha256.json")


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _message_document(message: BuiltMessage) -> dict[str, Any]:
    return {
        "fields": message.definition.field_map(),
        "frame_offset": message.frame_offset,
        "message_index": message.message_index,
        "name": message.definition.name,
        "payload_hex": message.payload.hex(),
        "payload_length": len(message.payload),
        "payload_offset": message.payload_offset,
        "type": message.definition.message_type,
    }


def _stream_document(
    name: str, purpose: str, built: BuiltStream, plain_path: Path, gzip_path: Path
) -> dict[str, Any]:
    return {
        "counts_by_type": built.counts_by_type,
        "fixture": plain_path.as_posix(),
        "gzip_fixture": gzip_path.as_posix(),
        "message_count": len(built.messages),
        "messages": [_message_document(item) for item in built.messages],
        "name": name,
        "purpose": purpose,
    }


def _corruptions(
    minimal: BuiltStream, minimal_gzip: bytes
) -> tuple[dict[Path, bytes], list[dict[str, Any]]]:
    first_payload = minimal.messages[0].payload
    wrong_known_length = len(first_payload) - 1
    cases: tuple[tuple[str, bytes, str, str], ...] = (
        (
            "synthetic_corrupt_truncated_length_prefix.itch",
            truncated_length_prefix(minimal.framed_bytes),
            "ERR_TRUNCATED_MESSAGE",
            "One byte of the required two-byte outer length prefix.",
        ),
        (
            "synthetic_corrupt_zero_length_frame.itch",
            zero_length_frame(),
            "ERR_FRAMING",
            "Outer frame declares a forbidden zero-length payload.",
        ),
        (
            "synthetic_corrupt_oversized_frame.itch",
            oversized_frame_prefix(),
            "ERR_FRAMING",
            "Outer frame declares 513 bytes, one above the project cap.",
        ),
        (
            "synthetic_corrupt_truncated_payload.itch",
            truncated_payload_frame(first_payload),
            "ERR_TRUNCATED_MESSAGE",
            "Frame declares a complete S payload but its final byte is absent.",
        ),
        (
            "synthetic_corrupt_wrong_known_length.itch",
            frame_with_observed_payload_length(first_payload, wrong_known_length),
            "ERR_MESSAGE_LENGTH",
            "Safely framed S payload is one byte shorter than its exact message length.",
        ),
        (
            "synthetic_corrupt_unknown_type.itch",
            frame_payload(replace_message_type(first_payload, "Z")),
            "ERR_UNKNOWN_MESSAGE",
            "Safely framed payload has an unsupported type byte.",
        ),
        (
            "synthetic_corrupt_truncated_gzip.itch.gz",
            truncated_gzip(minimal_gzip),
            "ERR_FRAMING",
            "Gzip member is missing its complete trailer.",
        ),
        (
            "synthetic_corrupt_gzip_checksum.itch.gz",
            corrupt_gzip_checksum(minimal_gzip),
            "ERR_FRAMING",
            "Gzip member has a deliberately incorrect CRC-32 value.",
        ),
    )

    outputs: dict[Path, bytes] = {}
    documents: list[dict[str, Any]] = []
    for filename, data, expected_error_code, purpose in cases:
        path = Path("tests/fixtures/corrupt") / filename
        outputs[path] = data
        documents.append(
            {
                "expected_error_code": expected_error_code,
                "fixture": path.as_posix(),
                "purpose": purpose,
                "synthetic": True,
            }
        )
    return outputs, documents


def render_outputs() -> dict[Path, bytes]:
    """Render every committed fixture and golden file entirely in memory."""
    outputs: dict[Path, bytes] = {}
    stream_documents: list[dict[str, Any]] = []
    built_valid: dict[str, BuiltStream] = {}

    for stream in VALID_STREAMS:
        built = build_stream(stream.messages)
        built_valid[stream.name] = built
        plain_path = Path("tests/fixtures") / f"{stream.name}.itch"
        gzip_path = Path("tests/fixtures") / f"{stream.name}.itch.gz"
        compressed = deterministic_gzip(built.framed_bytes)
        outputs[plain_path] = built.framed_bytes
        outputs[gzip_path] = compressed
        stream_documents.append(
            _stream_document(stream.name, stream.purpose, built, plain_path, gzip_path)
        )

    invalid_documents: list[dict[str, Any]] = []
    for lifecycle in INVALID_LIFECYCLES:
        built = build_stream(lifecycle.messages)
        path = Path("tests/fixtures/invalid_lifecycle") / f"{lifecycle.name}.itch"
        outputs[path] = built.framed_bytes
        offending_index = next(
            message.message_index
            for message in built.messages
            if message.definition.name == lifecycle.offending_message_name
        )
        invalid_documents.append(
            {
                "expected_error_code": lifecycle.expected_error_code,
                "fixture": path.as_posix(),
                "message_count": len(built.messages),
                "name": lifecycle.name,
                "offending_message_index": offending_index,
                "offending_message_name": lifecycle.offending_message_name,
                "synthetic": True,
            }
        )

    minimal = built_valid["synthetic_minimal"]
    minimal_gzip = outputs[Path("tests/fixtures/synthetic_minimal.itch.gz")]
    corrupt_outputs, corruption_documents = _corruptions(minimal, minimal_gzip)
    outputs.update(corrupt_outputs)

    expected_document = {
        "corruptions": corruption_documents,
        "framing": {
            "length_bytes": 2,
            "length_byte_order": "big",
            "status": (
                "Verified against the public Nasdaq 2019-12-30 TotalView-ITCH 5.0 sample; "
                "complete-frame EOF and zero-length rejection are fixed by ADR-005."
            ),
        },
        "invalid_lifecycles": invalid_documents,
        "schema_version": 1,
        "specification": {
            "name": "Nasdaq TotalView-ITCH",
            "url": SPECIFICATION_URL,
            "version": "5.0",
        },
        "streams": stream_documents,
        "synthetic": True,
    }
    outputs[EXPECTED_PATH] = _json_bytes(expected_document)

    fixture_entries: dict[str, dict[str, int | str]] = {}
    for path, content in sorted(outputs.items(), key=lambda item: item[0].as_posix()):
        if path.suffix not in {".itch", ".gz"}:
            continue
        fixture_entries[path.as_posix()] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    outputs[HASH_PATH] = _json_bytes(
        {"files": fixture_entries, "schema_version": 1, "synthetic": True}
    )
    return outputs


def _safe_destination(output_root: Path, relative_path: Path) -> Path:
    root = output_root.resolve()
    destination = (root / relative_path).resolve(strict=False)
    if not destination.is_relative_to(root):
        raise ValueError(f"fixture output escapes explicit root: {relative_path}")
    return destination


def write_outputs(output_root: Path = REPOSITORY_ROOT) -> tuple[Path, ...]:
    """Atomically write the fixed generated-output set beneath an explicit root."""
    written: list[Path] = []
    for relative_path, content in render_outputs().items():
        destination = _safe_destination(output_root, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".partial",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        written.append(relative_path)
    return tuple(written)


def compare_outputs(output_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    """Return bounded diagnostics for missing or stale committed generated files."""
    mismatches: list[str] = []
    for relative_path, expected in render_outputs().items():
        destination = _safe_destination(output_root, relative_path)
        try:
            actual = destination.read_bytes()
        except FileNotFoundError:
            mismatches.append(f"missing: {relative_path.as_posix()}")
            continue
        if actual != expected:
            mismatches.append(f"stale: {relative_path.as_posix()}")
    return tuple(mismatches)


def main(argv: Sequence[str] | None = None) -> int:
    """Generate fixtures or check that committed outputs are current."""
    parser = argparse.ArgumentParser(
        description="Generate independent, explicitly synthetic ITCH 5.0 fixtures."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare committed files without writing",
    )
    arguments = parser.parse_args(argv)

    if arguments.check:
        mismatches = compare_outputs()
        if mismatches:
            for mismatch in mismatches:
                print(mismatch)
            print("Synthetic ITCH fixture check failed; regenerate the corpus.")
            return 1
        print(f"Synthetic ITCH fixture check passed ({len(render_outputs())} files).")
        return 0

    written = write_outputs()
    print(f"Generated {len(written)} independent synthetic ITCH fixture files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPECTED_PATH",
    "HASH_PATH",
    "REPOSITORY_ROOT",
    "compare_outputs",
    "main",
    "render_outputs",
    "write_outputs",
]
