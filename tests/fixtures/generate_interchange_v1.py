"""Generate or verify independent synthetic event-v1 and snapshot-v1 goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
EVENT_GOLDEN_PATH: Final = Path("tests/golden/interchange/synthetic_events_v1.ilb")
EVENT_METADATA_PATH: Final = Path("tests/golden/interchange/synthetic_events_v1.json")
SNAPSHOT_GOLDEN_PATH: Final = Path("tests/golden/interchange/synthetic_snapshots_v1.ilb")
SNAPSHOT_METADATA_PATH: Final = Path("tests/golden/interchange/synthetic_snapshots_v1.json")

HEADER = struct.Struct("<8sHHHHIIHHQ32s32s4s")
SYMBOL = struct.Struct("<HH8sI")
EVENT = struct.Struct("<QQQQQIIIHBbcHB4sB7s")
SNAPSHOT = struct.Struct("<QQHBBIQI4sQ")
DEPTH_LEVEL = struct.Struct("<BB2sIQIQ")

PRIMARY: Final = 1 << 0
SECONDARY: Final = 1 << 1
SIDE: Final = 1 << 2
PRICE: Final = 1 << 3
QUANTITY: Final = 1 << 4
REMAINING: Final = 1 << 5
EXECUTION_PRICE: Final = 1 << 6
AUXILIARY: Final = 1 << 7
SUBTYPE: Final = 1 << 8
IN_SESSION: Final = 1 << 9


def _event(
    message_index: int,
    timestamp_ns: int,
    primary: int,
    secondary: int,
    quantity: int,
    price4: int,
    remaining: int,
    execution_price4: int,
    symbol_id: int,
    kind: int,
    side: int,
    source_type: str,
    flags: int,
    auxiliary: str = "",
    subtype: str = "",
) -> bytes:
    return EVENT.pack(
        message_index,
        timestamp_ns,
        primary,
        secondary,
        quantity,
        price4,
        remaining,
        execution_price4,
        symbol_id,
        kind,
        side,
        source_type.encode("ascii"),
        flags,
        0,
        auxiliary.encode("ascii").ljust(4, b" ") if flags & AUXILIARY else b"\0" * 4,
        ord(subtype) if flags & SUBTYPE else 0,
        b"\0" * 7,
    )


def render_event_golden() -> bytes:
    """Render reviewed literal event-v1 values without production-code imports."""
    config_hash = bytes(range(1, 33))
    source_hash = bytes(range(33, 65))
    records = (
        _event(
            5,
            1_000_000,
            0x0102030405060708,
            0,
            300,
            1_652_300,
            300,
            0,
            1,
            1,
            1,
            "F",
            PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | AUXILIARY,
            "MM01",
        ),
        _event(
            6,
            1_000_010,
            0x0102030405060708,
            0x1112131415161718,
            100,
            1_652_300,
            200,
            0,
            1,
            2,
            1,
            "E",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        _event(
            7,
            1_000_020,
            0x2122232425262728,
            0x3132333435363738,
            50,
            1_652_400,
            100,
            1_652_350,
            1,
            3,
            -1,
            "C",
            PRIMARY
            | SECONDARY
            | SIDE
            | PRICE
            | QUANTITY
            | REMAINING
            | EXECUTION_PRICE
            | IN_SESSION,
        ),
        _event(
            8,
            1_000_030,
            0x4142434445464748,
            0,
            50,
            1_652_500,
            250,
            0,
            2,
            4,
            -1,
            "X",
            PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        _event(
            9,
            1_000_040,
            0x4142434445464748,
            0,
            250,
            1_652_500,
            0,
            0,
            2,
            5,
            -1,
            "D",
            PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        _event(
            10,
            1_000_050,
            0x5152535455565758,
            0x6162636465666768,
            400,
            1_652_600,
            400,
            0,
            2,
            6,
            -1,
            "U",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        _event(
            11,
            1_000_060,
            0,
            0x7172737475767778,
            75,
            1_652_700,
            0,
            0,
            1,
            7,
            1,
            "P",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | IN_SESSION,
        ),
        _event(
            12,
            1_000_070,
            0,
            0x0101010102020202,
            1_000,
            1_652_800,
            0,
            0,
            2,
            8,
            0,
            "Q",
            SECONDARY | PRICE | QUANTITY | SUBTYPE | IN_SESSION,
            subtype="O",
        ),
        _event(
            13,
            1_000_080,
            0x7172737475767778,
            0,
            0,
            0,
            0,
            0,
            1,
            9,
            0,
            "B",
            PRIMARY | IN_SESSION,
        ),
        _event(
            14,
            1_000_090,
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            10,
            0,
            "H",
            AUXILIARY | SUBTYPE | IN_SESSION,
            "NEWS",
            "H",
        ),
    )
    header = HEADER.pack(
        b"ITCHLE1\0",
        1,
        104,
        72,
        0,
        10_000,
        20_190_130,
        2,
        1,
        len(records),
        config_hash,
        source_hash,
        b"\0" * 4,
    )
    dictionary = b"".join(
        (
            SYMBOL.pack(1, 0x1234, b"AAPL    ", 100),
            SYMBOL.pack(2, 0xABCD, b"MSFT.X  ", 200),
        )
    )
    rendered = header + dictionary + b"".join(records)
    assert HEADER.size == 104
    assert SYMBOL.size == 16
    assert EVENT.size == 72
    assert len(rendered) == 104 + 2 * 16 + 10 * 72
    return rendered


def _depth_level(
    bid: tuple[int, int] | None, ask: tuple[int, int] | None
) -> bytes:
    return DEPTH_LEVEL.pack(
        int(bid is not None),
        int(ask is not None),
        b"\0" * 2,
        bid[0] if bid else 0,
        bid[1] if bid else 0,
        ask[0] if ask else 0,
        ask[1] if ask else 0,
    )


def render_snapshot_golden() -> bytes:
    """Render reviewed literal snapshot-v1 values without production-code imports."""
    config_hash = bytes(range(1, 33))
    source_hash = bytes(range(33, 65))
    depth = 2
    record_size = 48 + 28 * depth
    first = SNAPSHOT.pack(
        5,
        1_000_000,
        1,
        1,
        87,
        1_652_300,
        300,
        1_652_350,
        b"\0" * 4,
        75,
    ) + b"".join(
        (
            _depth_level((1_652_300, 300), (1_652_400, 200)),
            _depth_level((1_652_200, 500), None),
        )
    )
    second = SNAPSHOT.pack(
        14,
        1_000_090,
        2,
        10,
        24,
        0,
        0,
        0,
        b"\0" * 4,
        0,
    ) + b"".join((_depth_level(None, None), _depth_level(None, None)))
    records = (first, second)
    header = HEADER.pack(
        b"ITCHLS1\0",
        1,
        104,
        record_size,
        depth,
        10_000,
        20_190_130,
        2,
        1,
        len(records),
        config_hash,
        source_hash,
        b"\0" * 4,
    )
    dictionary = b"".join(
        (
            SYMBOL.pack(1, 0x1234, b"AAPL    ", 100),
            SYMBOL.pack(2, 0xABCD, b"MSFT.X  ", 200),
        )
    )
    rendered = header + dictionary + b"".join(records)
    assert SNAPSHOT.size == 48
    assert DEPTH_LEVEL.size == 28
    assert all(len(record) == record_size for record in records)
    assert len(rendered) == 104 + 2 * 16 + 2 * record_size
    return rendered


def render_outputs() -> dict[Path, bytes]:
    event_golden = render_event_golden()
    event_metadata = {
        "record_count": 10,
        "record_size": 72,
        "schema_version": 1,
        "sha256": hashlib.sha256(event_golden).hexdigest(),
        "size_bytes": len(event_golden),
        "symbol_count": 2,
        "synthetic": True,
    }
    snapshot_golden = render_snapshot_golden()
    snapshot_metadata = {
        "depth": 2,
        "record_count": 2,
        "record_size": 104,
        "schema_version": 1,
        "sha256": hashlib.sha256(snapshot_golden).hexdigest(),
        "size_bytes": len(snapshot_golden),
        "symbol_count": 2,
        "synthetic": True,
    }
    return {
        EVENT_GOLDEN_PATH: event_golden,
        EVENT_METADATA_PATH: (
            json.dumps(event_metadata, indent=2, sort_keys=True) + "\n"
        ).encode(),
        SNAPSHOT_GOLDEN_PATH: snapshot_golden,
        SNAPSHOT_METADATA_PATH: (
            json.dumps(snapshot_metadata, indent=2, sort_keys=True) + "\n"
        ).encode(),
    }


def _write_atomic(path: Path, content: bytes) -> None:
    destination = REPOSITORY_ROOT / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed outputs")
    arguments = parser.parse_args()
    outputs = render_outputs()
    if arguments.check:
        mismatches = [
            path.as_posix()
            for path, expected in outputs.items()
            if not (REPOSITORY_ROOT / path).is_file()
            or (REPOSITORY_ROOT / path).read_bytes() != expected
        ]
        if mismatches:
            parser.error("generated outputs differ: " + ", ".join(mismatches))
        return 0
    for path, content in outputs.items():
        _write_atomic(path, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
