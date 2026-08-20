"""Generate the untracked deterministic TASK-029 benchmark fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

from tests.fixtures.itch50_builder import (
    deterministic_gzip,
    encode_payload,
    frame_payload,
    message,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_CYCLES: Final = 100_000
PLAIN_PATH: Final = Path("data/fixtures/performance.itch")
GZIP_PATH: Final = Path("data/fixtures/performance.itch.gz")
METADATA_PATH: Final = Path("data/fixtures/performance-metadata.json")


def _messages(cycles: int) -> Iterator[bytes]:
    yield frame_payload(
        encode_payload(
            message(
                "system-start",
                "S",
                stock_locate=0,
                tracking_number=1,
                timestamp_ns=1,
                event_code="O",
            )
        )
    )
    for locate, symbol, tracking in ((1, "AAPL", 2), (2, "MSFT", 3)):
        yield frame_payload(
            encode_payload(
                message(
                    f"directory-{symbol}",
                    "R",
                    stock_locate=locate,
                    tracking_number=tracking,
                    timestamp_ns=tracking,
                    stock=symbol,
                    market_category="Q",
                    financial_status="N",
                    round_lot_size=100,
                    round_lots_only="N",
                    issue_classification="A",
                    issue_sub_type="",
                    authenticity="P",
                    short_sale_threshold_indicator="N",
                    ipo_flag="N",
                    luld_reference_price_tier="1",
                    etp_flag="N",
                    etp_leverage_factor=1,
                    inverse_indicator="N",
                )
            )
        )

    timestamp = 4
    for cycle in range(cycles):
        base = cycle * 10 + 1_000
        definitions = (
            message(
                "selected-buy-add",
                "A",
                stock_locate=1,
                tracking_number=1,
                timestamp_ns=timestamp,
                order_reference=base,
                side="B",
                shares=100,
                stock="AAPL",
                price4=1_000_000 + cycle % 32,
            ),
            message(
                "selected-sell-add",
                "A",
                stock_locate=1,
                tracking_number=2,
                timestamp_ns=timestamp + 1,
                order_reference=base + 1,
                side="S",
                shares=100,
                stock="AAPL",
                price4=1_000_100 + cycle % 32,
            ),
            message(
                "selected-buy-cancel",
                "X",
                stock_locate=1,
                tracking_number=3,
                timestamp_ns=timestamp + 2,
                order_reference=base,
                cancelled_shares=20,
            ),
            message(
                "selected-sell-execute",
                "E",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=timestamp + 3,
                order_reference=base + 1,
                executed_shares=40,
                match_number=base + 5_000_000,
            ),
            message(
                "selected-buy-replace",
                "U",
                stock_locate=1,
                tracking_number=5,
                timestamp_ns=timestamp + 4,
                original_order_reference=base,
                new_order_reference=base + 2,
                shares=80,
                price4=1_000_001 + cycle % 32,
            ),
            message(
                "selected-buy-delete",
                "D",
                stock_locate=1,
                tracking_number=6,
                timestamp_ns=timestamp + 5,
                order_reference=base + 2,
            ),
            message(
                "selected-sell-delete",
                "D",
                stock_locate=1,
                tracking_number=7,
                timestamp_ns=timestamp + 6,
                order_reference=base + 1,
            ),
            message(
                "filtered-add",
                "A",
                stock_locate=2,
                tracking_number=8,
                timestamp_ns=timestamp + 7,
                order_reference=base + 3,
                side="B",
                shares=50,
                stock="MSFT",
                price4=2_000_000 + cycle % 32,
            ),
            message(
                "filtered-cancel",
                "X",
                stock_locate=2,
                tracking_number=9,
                timestamp_ns=timestamp + 8,
                order_reference=base + 3,
                cancelled_shares=10,
            ),
            message(
                "filtered-delete",
                "D",
                stock_locate=2,
                tracking_number=10,
                timestamp_ns=timestamp + 9,
                order_reference=base + 3,
            ),
        )
        for definition in definitions:
            yield frame_payload(encode_payload(definition))
        timestamp += len(definitions)


def render_fixture(
    cycles: int = DEFAULT_CYCLES,
) -> tuple[bytes, bytes, dict[str, object]]:
    """Return deterministic plain/gzip bytes and their public-safe metadata."""
    if cycles < 1 or cycles > 1_000_000:
        raise ValueError("cycles must be between 1 and 1,000,000")
    plain = b"".join(_messages(cycles))
    compressed = deterministic_gzip(plain)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "synthetic": True,
        "cycles": cycles,
        "message_count": 3 + cycles * 10,
        "selected_symbol": "AAPL",
        "filtered_symbol": "MSFT",
        "maximum_live_selected_orders": 2,
        "plain": {
            "path": PLAIN_PATH.as_posix(),
            "sha256": hashlib.sha256(plain).hexdigest(),
            "size_bytes": len(plain),
        },
        "gzip": {
            "path": GZIP_PATH.as_posix(),
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "size_bytes": len(compressed),
        },
    }
    return plain, compressed, metadata


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".partial"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_fixture(
    output_root: Path = REPOSITORY_ROOT, cycles: int = DEFAULT_CYCLES
) -> None:
    plain, compressed, metadata = render_fixture(cycles)
    _atomic_write(output_root / PLAIN_PATH, plain)
    _atomic_write(output_root / GZIP_PATH, compressed)
    _atomic_write(
        output_root / METADATA_PATH,
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def check_fixture(
    output_root: Path = REPOSITORY_ROOT, cycles: int = DEFAULT_CYCLES
) -> bool:
    plain, compressed, metadata = render_fixture(cycles)
    expected = {
        PLAIN_PATH: plain,
        GZIP_PATH: compressed,
        METADATA_PATH: (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    }
    return all(
        (output_root / path).is_file() and (output_root / path).read_bytes() == content
        for path, content in expected.items()
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)
    args = parser.parse_args(argv)
    if args.check:
        if check_fixture(cycles=args.cycles):
            print("TASK-029 performance fixture matches its deterministic recipe.")
            return 0
        print("TASK-029 performance fixture is missing or stale.")
        return 1
    write_fixture(cycles=args.cycles)
    print(f"Generated {3 + args.cycles * 10:,} synthetic benchmark messages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
