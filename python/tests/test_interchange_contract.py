"""TASK-015 independent Python contract reads for C++ interchange-v1 goldens."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "interchange"

HEADER = struct.Struct("<8sHHHHIIHHQ32s32s4s")
SYMBOL = struct.Struct("<HH8sI")
EVENT = struct.Struct("<QQQQQIIIHBbcHB4sB7s")
SNAPSHOT = struct.Struct("<QQHBBIQI4sQ")
DEPTH_LEVEL = struct.Struct("<BB2sIQIQ")

PRIMARY = 1 << 0
SECONDARY = 1 << 1
SIDE = 1 << 2
PRICE = 1 << 3
QUANTITY = 1 << 4
REMAINING = 1 << 5
EXECUTION_PRICE = 1 << 6
AUXILIARY = 1 << 7
SUBTYPE = 1 << 8
IN_SESSION = 1 << 9


def test_ct_bin_001_python_reads_every_event_v1_golden_record_exactly() -> None:
    """Decode reviewed fields without importing production constants or readers."""
    content = (GOLDEN_ROOT / "synthetic_events_v1.ilb").read_bytes()
    header = HEADER.unpack_from(content)
    assert header[:10] == (
        b"ITCHLE1\0",
        1,
        104,
        72,
        0,
        10_000,
        20_190_130,
        2,
        1,
        10,
    )
    assert header[10] == bytes(range(1, 33))
    assert header[11] == bytes(range(33, 65))
    assert header[12] == b"\0" * 4
    assert hashlib.sha256(content).hexdigest() == (
        "1d950ca79254b96081add98db62fdbce12167895ba96cbdee886168689e607de"
    )

    assert [SYMBOL.unpack_from(content, 104 + index * SYMBOL.size) for index in range(2)] == [
        (1, 0x1234, b"AAPL    ", 100),
        (2, 0xABCD, b"MSFT.X  ", 200),
    ]

    records_offset = 104 + 2 * SYMBOL.size
    records = [
        EVENT.unpack_from(content, records_offset + index * EVENT.size) for index in range(10)
    ]
    observed = [
        (record[0], record[1], record[8], record[9], record[10], record[11], record[12])
        for record in records
    ]
    assert observed == [
        (5, 1_000_000, 1, 1, 1, b"F", PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | AUXILIARY),
        (
            6,
            1_000_010,
            1,
            2,
            1,
            b"E",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        (
            7,
            1_000_020,
            1,
            3,
            -1,
            b"C",
            PRIMARY
            | SECONDARY
            | SIDE
            | PRICE
            | QUANTITY
            | REMAINING
            | EXECUTION_PRICE
            | IN_SESSION,
        ),
        (
            8,
            1_000_030,
            2,
            4,
            -1,
            b"X",
            PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        (
            9,
            1_000_040,
            2,
            5,
            -1,
            b"D",
            PRIMARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        (
            10,
            1_000_050,
            2,
            6,
            -1,
            b"U",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | REMAINING | IN_SESSION,
        ),
        (
            11,
            1_000_060,
            1,
            7,
            1,
            b"P",
            PRIMARY | SECONDARY | SIDE | PRICE | QUANTITY | IN_SESSION,
        ),
        (
            12,
            1_000_070,
            2,
            8,
            0,
            b"Q",
            SECONDARY | PRICE | QUANTITY | SUBTYPE | IN_SESSION,
        ),
        (13, 1_000_080, 1, 9, 0, b"B", PRIMARY | IN_SESSION),
        (14, 1_000_090, 1, 10, 0, b"H", AUXILIARY | SUBTYPE | IN_SESSION),
    ]
    assert records[0][2:8] == (0x0102030405060708, 0, 300, 1_652_300, 300, 0)
    assert records[0][14:] == (b"MM01", 0, b"\0" * 7)
    assert records[2][7] == 1_652_350
    assert records[9][14:] == (b"NEWS", ord("H"), b"\0" * 7)


def test_ct_bin_001_python_reads_every_snapshot_v1_golden_record_exactly() -> None:
    """Decode prefixes and every depth slot with explicit little-endian structs."""
    content = (GOLDEN_ROOT / "synthetic_snapshots_v1.ilb").read_bytes()
    header = HEADER.unpack_from(content)
    assert header[:10] == (
        b"ITCHLS1\0",
        1,
        104,
        104,
        2,
        10_000,
        20_190_130,
        2,
        1,
        2,
    )
    assert hashlib.sha256(content).hexdigest() == (
        "8367cffd4c6167ee959063418877068422d6a6d2bb8767629a2c0d30037a4349"
    )

    records_offset = 104 + 2 * SYMBOL.size
    first = SNAPSHOT.unpack_from(content, records_offset)
    second = SNAPSHOT.unpack_from(content, records_offset + 104)
    assert first == (
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
    )
    assert second == (14, 1_000_090, 2, 10, 24, 0, 0, 0, b"\0" * 4, 0)

    first_levels = [
        DEPTH_LEVEL.unpack_from(content, records_offset + 48 + index * DEPTH_LEVEL.size)
        for index in range(2)
    ]
    second_levels = [
        DEPTH_LEVEL.unpack_from(content, records_offset + 104 + 48 + index * DEPTH_LEVEL.size)
        for index in range(2)
    ]
    assert first_levels == [
        (1, 1, b"\0\0", 1_652_300, 300, 1_652_400, 200),
        (1, 0, b"\0\0", 1_652_200, 500, 0, 0),
    ]
    assert second_levels == [(0, 0, b"\0\0", 0, 0, 0, 0)] * 2
