"""TASK-003 independent synthetic ITCH fixture builder and corpus tests."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from tests.fixtures.generate_itch50 import (
    EXPECTED_PATH,
    HASH_PATH,
    REPOSITORY_ROOT,
    compare_outputs,
    main,
    render_outputs,
    write_outputs,
)
from tests.fixtures.itch50_builder import (
    MESSAGE_LENGTHS,
    MESSAGE_TYPES,
    FixtureBuildError,
    as_message_type,
    build_stream,
    deterministic_gzip,
    encode_payload,
    frame_payload,
    frame_with_observed_payload_length,
    message,
)
from tests.fixtures.itch50_definitions import (
    INVALID_LIFECYCLES,
    MINIMAL_STREAM,
    MIXED_STREAM,
    VALID_STREAMS,
)

EXPECTED_LENGTHS = {
    "S": 12,
    "R": 39,
    "H": 25,
    "A": 36,
    "F": 40,
    "E": 31,
    "C": 36,
    "X": 23,
    "D": 19,
    "U": 35,
    "P": 44,
    "Q": 40,
    "B": 19,
}

# Reviewed literal vectors are deliberately separate from the builder and its generated JSON.
FIRST_MIXED_PAYLOAD_HEX = {
    "S": "53000000010000000003e84f",
    "R": "52000100020000000007d04141504c20202020514e000000644e432020504e4e314e000000014e",
    "H": "48000100061f1aced9e4484141504c20202020542020202020",
    "A": "410001000a1f1aced9f3e800000000000003e942000000644141504c20202020000f4240",
    "F": ("460001000b1f1aced9f7d000000000000003ea53000000c84141504c20202020000f462854455354"),
    "E": "450001000e1f1aceda038800000000000003e9000000280000000000001389",
    "C": "43000100111f1aceda0f4000000000000003ea00000032000000000000138a59000f45c4",
    "X": "580001000f1f1aceda077000000000000003e90000000a",
    "D": "44000100101f1aceda0b5800000000000003e9",
    "U": "55000100121f1aceda132800000000000003ea00000000000003eb0000007d000f468c",
    "P": (
        "50000100181f1aceda2a980000000000000000420000004b4141504c20202020000f44340000000000001771"
    ),
    "Q": "510002001a1f1aceda326800000000000003e84d53465420202020001e84800000000000001b594f",
    "B": "42000100191f1aceda2e800000000000001771",
}


def _load_json(relative_path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")),
    )


def _parse_frames_independently(data: bytes) -> list[tuple[int, int, bytes]]:
    frames: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= 2
        payload_length = int.from_bytes(data[offset : offset + 2], byteorder="big")
        payload_offset = offset + 2
        payload_end = payload_offset + payload_length
        assert 1 <= payload_length <= 512
        assert payload_end <= len(data)
        frames.append((offset, payload_offset, data[payload_offset:payload_end]))
        offset = payload_end
    assert offset == len(data)
    return frames


def test_task_003_supported_types_and_lengths_match_the_independent_spec_table() -> None:
    assert set(MESSAGE_TYPES) == set(EXPECTED_LENGTHS)
    assert MESSAGE_LENGTHS == EXPECTED_LENGTHS


def test_task_003_all_message_layouts_match_reviewed_literal_vectors() -> None:
    first_by_type: dict[str, bytes] = {}
    for built in build_stream(MIXED_STREAM.messages).messages:
        first_by_type.setdefault(built.definition.message_type, built.payload)

    assert set(first_by_type) == set(EXPECTED_LENGTHS)
    assert {key: value.hex() for key, value in first_by_type.items()} == FIRST_MIXED_PAYLOAD_HEX


def test_task_003_six_byte_timestamp_and_big_endian_integer_vector() -> None:
    definition = message(
        "endian_vector",
        "S",
        stock_locate=0,
        tracking_number=0x1234,
        timestamp_ns=0x010203040506,
        event_code="Q",
    )
    assert encode_payload(definition).hex() == "530000123401020304050651"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("stock_locate", True),
        ("stock_locate", -1),
        ("stock_locate", 65_536),
        ("tracking_number", "1"),
        ("timestamp_ns", -1),
        ("timestamp_ns", 86_400_000_000_000),
        ("event_code", "QQ"),
        ("event_code", "£"),
    ],
)
def test_task_003_builder_rejects_invalid_boundaries(
    field_name: str, invalid_value: int | str | bool
) -> None:
    fields: dict[str, int | str | bool] = {
        "stock_locate": 0,
        "tracking_number": 1,
        "timestamp_ns": 1_000,
        "event_code": "O",
    }
    fields[field_name] = invalid_value
    with pytest.raises(FixtureBuildError):
        encode_payload(message("invalid_boundary", "S", **fields))


def test_task_003_builder_rejects_missing_unknown_and_unsupported_fields() -> None:
    with pytest.raises(FixtureBuildError, match="missing event_code"):
        encode_payload(
            message(
                "missing_field",
                "S",
                stock_locate=0,
                tracking_number=1,
                timestamp_ns=1_000,
            )
        )
    with pytest.raises(FixtureBuildError, match="unknown extra"):
        encode_payload(
            message(
                "unknown_field",
                "S",
                stock_locate=0,
                tracking_number=1,
                timestamp_ns=1_000,
                event_code="O",
                extra=1,
            )
        )
    with pytest.raises(FixtureBuildError, match="unsupported message type"):
        as_message_type("Z")


def test_task_003_framed_stream_offsets_and_expected_json_are_exact() -> None:
    expected = _load_json(EXPECTED_PATH)
    expected_streams = cast(list[dict[str, Any]], expected["streams"])

    for definition, stream_document in zip(VALID_STREAMS, expected_streams, strict=True):
        plain_path = REPOSITORY_ROOT / cast(str, stream_document["fixture"])
        frames = _parse_frames_independently(plain_path.read_bytes())
        expected_messages = cast(list[dict[str, Any]], stream_document["messages"])

        assert stream_document["name"] == definition.name
        assert len(frames) == stream_document["message_count"] == len(expected_messages)
        for message_index, ((frame_offset, payload_offset, payload), expected_message) in enumerate(
            zip(frames, expected_messages, strict=True)
        ):
            assert expected_message["message_index"] == message_index
            assert expected_message["frame_offset"] == frame_offset
            assert expected_message["payload_offset"] == payload_offset
            assert expected_message["payload_length"] == len(payload)
            assert expected_message["payload_hex"] == payload.hex()
            assert payload[:1].decode("ascii") == expected_message["type"]


@pytest.mark.parametrize("stream", VALID_STREAMS, ids=lambda stream: stream.name)
def test_task_003_gzip_and_uncompressed_fixtures_are_byte_equivalent(stream: Any) -> None:
    plain = (REPOSITORY_ROOT / "tests" / "fixtures" / f"{stream.name}.itch").read_bytes()
    compressed = (REPOSITORY_ROOT / "tests" / "fixtures" / f"{stream.name}.itch.gz").read_bytes()

    assert compressed[:4] == b"\x1f\x8b\x08\x00"
    assert compressed[4:8] == b"\x00\x00\x00\x00"
    assert compressed[9] == 255
    assert gzip.decompress(compressed) == plain
    assert deterministic_gzip(plain) == compressed


def test_task_003_mixed_stream_covers_every_required_type_and_complete_lifecycles() -> None:
    mixed = build_stream(MIXED_STREAM.messages)
    names = {item.definition.name for item in mixed.messages}

    assert set(mixed.counts_by_type) == set(EXPECTED_LENGTHS)
    assert {
        "partially_execute_aapl_bid",
        "partially_cancel_aapl_bid",
        "delete_aapl_bid_remainder",
        "execute_aapl_ask_with_price",
        "replace_aapl_ask",
        "fully_execute_replaced_aapl_ask",
        "non_cross_trade_aapl",
        "break_non_cross_trade",
        "opening_cross_msft",
        "halt_aapl",
        "resume_aapl",
        "end_messages",
    } <= names


def test_task_003_minimal_stream_is_limited_to_the_first_vertical_slice() -> None:
    minimal = build_stream(MINIMAL_STREAM.messages)
    assert set(minimal.counts_by_type) == {"S", "R", "A", "D"}


def test_task_003_wrong_length_mutator_covers_every_short_length_and_one_long_length() -> None:
    first_by_type: dict[str, bytes] = {}
    for built in build_stream(MIXED_STREAM.messages).messages:
        first_by_type.setdefault(built.definition.message_type, built.payload)

    for message_type, expected_length in EXPECTED_LENGTHS.items():
        payload = first_by_type[message_type]
        for observed_length in (*range(expected_length), expected_length + 1):
            frame = frame_with_observed_payload_length(payload, observed_length)
            assert int.from_bytes(frame[:2], byteorder="big") == observed_length
            assert len(frame) == observed_length + 2
            if observed_length:
                assert frame[2] == ord(message_type)


def test_task_003_corruption_fixtures_have_the_declared_bounded_shapes() -> None:
    root = REPOSITORY_ROOT / "tests" / "fixtures" / "corrupt"
    assert (root / "synthetic_corrupt_truncated_length_prefix.itch").stat().st_size == 1
    assert (root / "synthetic_corrupt_zero_length_frame.itch").read_bytes() == b"\x00\x00"
    assert (
        int.from_bytes(
            (root / "synthetic_corrupt_oversized_frame.itch").read_bytes(), byteorder="big"
        )
        == 513
    )

    truncated_payload = (root / "synthetic_corrupt_truncated_payload.itch").read_bytes()
    assert int.from_bytes(truncated_payload[:2], byteorder="big") == 12
    assert len(truncated_payload[2:]) == 11

    wrong_length = (root / "synthetic_corrupt_wrong_known_length.itch").read_bytes()
    assert int.from_bytes(wrong_length[:2], byteorder="big") == 11
    assert len(wrong_length[2:]) == 11

    unknown = (root / "synthetic_corrupt_unknown_type.itch").read_bytes()
    assert int.from_bytes(unknown[:2], byteorder="big") == 12
    assert unknown[2:3] == b"Z"

    for filename in (
        "synthetic_corrupt_truncated_gzip.itch.gz",
        "synthetic_corrupt_gzip_checksum.itch.gz",
    ):
        with pytest.raises((gzip.BadGzipFile, EOFError)):
            gzip.decompress((root / filename).read_bytes())


def test_task_003_invalid_lifecycles_are_framed_and_end_at_the_declared_failure() -> None:
    expected = _load_json(EXPECTED_PATH)
    expected_cases = {
        item["name"]: item for item in cast(list[dict[str, Any]], expected["invalid_lifecycles"])
    }

    assert set(expected_cases) == {case.name for case in INVALID_LIFECYCLES}
    for case in INVALID_LIFECYCLES:
        built = build_stream(case.messages)
        expected_case = expected_cases[case.name]
        offending = built.messages[cast(int, expected_case["offending_message_index"])]
        assert offending.definition.name == case.offending_message_name
        assert expected_case["expected_error_code"] == case.expected_error_code
        assert _parse_frames_independently(built.framed_bytes)[-1][2] == offending.payload


def test_task_003_fixture_hash_snapshots_match_exact_committed_bytes() -> None:
    manifest = _load_json(HASH_PATH)
    entries = cast(dict[str, dict[str, Any]], manifest["files"])

    assert entries
    for relative_path, expected in entries.items():
        content = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert len(content) == expected["size_bytes"]
        assert hashlib.sha256(content).hexdigest() == expected["sha256"]


def test_task_003_generation_is_deterministic_and_committed_outputs_are_current() -> None:
    first = render_outputs()
    second = render_outputs()
    assert first == second
    assert compare_outputs() == ()


def test_task_003_generator_writes_only_fixed_paths_beneath_explicit_root(
    tmp_path: Path,
) -> None:
    written = write_outputs(tmp_path)
    assert set(written) == set(render_outputs())
    assert compare_outputs(tmp_path) == ()
    assert all((tmp_path / path).resolve().is_relative_to(tmp_path.resolve()) for path in written)


def test_task_003_every_binary_fixture_is_small_and_explicitly_synthetic() -> None:
    fixture_outputs = {
        path: content
        for path, content in render_outputs().items()
        if path.suffix in {".itch", ".gz"}
    }
    assert fixture_outputs
    assert all(path.name.startswith("synthetic_") for path in fixture_outputs)
    assert max(map(len, fixture_outputs.values())) < 2_048
    assert sum(map(len, fixture_outputs.values())) < 10_000


def test_task_003_tooling_does_not_import_production_modules() -> None:
    tooling = (
        REPOSITORY_ROOT / "tests" / "fixtures" / "itch50_builder.py",
        REPOSITORY_ROOT / "tests" / "fixtures" / "itch50_definitions.py",
        REPOSITORY_ROOT / "tests" / "fixtures" / "generate_itch50.py",
    )
    assert all("itchlab_research" not in path.read_text(encoding="utf-8") for path in tooling)


def test_task_003_generator_check_command_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    captured = capsys.readouterr()
    assert "fixture check passed" in captured.out
    assert captured.err == ""


def test_task_003_frame_payload_rejects_empty_and_oversized_inputs() -> None:
    with pytest.raises(FixtureBuildError):
        frame_payload(b"")
    with pytest.raises(FixtureBuildError):
        frame_payload(bytes(513))
