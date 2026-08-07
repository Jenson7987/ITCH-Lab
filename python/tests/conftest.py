"""Independent synthetic completed-replay builders for Python integration tests."""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from itchlab_research.canonical_json import config_document, config_hashes
from itchlab_research.config import ReplayConfig, parse_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "interchange"

HEADER = struct.Struct("<8sHHHHIIHHQ32s32s4s")
SYMBOL = struct.Struct("<HH8sI")
EVENT = struct.Struct("<QQQQQIIIHBbcHB4sB7s")
SNAPSHOT = struct.Struct("<QQHBBIQI4sQ")
DEPTH_LEVEL = struct.Struct("<BB2sIQIQ")


def _stage_identity(
    source_sha256: str,
    identity_config_sha256: str,
    executable_sha256: str,
) -> str:
    digest = hashlib.sha256(b"itchlab-replay-v1\0")
    digest.update(bytes.fromhex(source_sha256))
    digest.update(bytes.fromhex(identity_config_sha256))
    digest.update(bytes.fromhex(executable_sha256))
    digest.update(b"\0\1")
    return digest.hexdigest()


def _patch_golden(
    source: Path,
    *,
    config_sha256: str,
    source_sha256: str,
    degraded: bool,
    trading_date: int,
    first_symbol: str,
) -> bytes:
    content = bytearray(source.read_bytes())
    content[20:24] = struct.pack("<I", trading_date)
    content[26:28] = struct.pack("<H", int(degraded))
    content[36:68] = bytes.fromhex(config_sha256)
    content[68:100] = bytes.fromhex(source_sha256)
    content[108:116] = first_symbol.encode("ascii").ljust(8, b" ")
    return bytes(content)


def _large_event_file(
    path: Path,
    *,
    config_sha256: str,
    source_sha256: str,
    trading_date: int,
    record_count: int,
) -> None:
    header = HEADER.pack(
        b"ITCHLE1\0",
        1,
        HEADER.size,
        EVENT.size,
        0,
        10_000,
        trading_date,
        1,
        0,
        record_count,
        bytes.fromhex(config_sha256),
        bytes.fromhex(source_sha256),
        b"\0" * 4,
    )
    with path.open("wb") as stream:
        stream.write(header)
        stream.write(SYMBOL.pack(1, 1, b"AAPL    ", 100))
        for index in range(record_count):
            stream.write(
                EVENT.pack(
                    index,
                    index,
                    index + 1,
                    0,
                    100,
                    1_000_000,
                    100,
                    0,
                    1,
                    1,
                    1,
                    b"A",
                    573,
                    0,
                    b"\0" * 4,
                    0,
                    b"\0" * 7,
                )
            )


def _empty_snapshot_file(
    path: Path,
    *,
    config_sha256: str,
    source_sha256: str,
    trading_date: int,
) -> None:
    depth = 1
    record_size = SNAPSHOT.size + DEPTH_LEVEL.size * depth
    path.write_bytes(
        HEADER.pack(
            b"ITCHLS1\0",
            1,
            HEADER.size,
            record_size,
            depth,
            10_000,
            trading_date,
            1,
            0,
            0,
            bytes.fromhex(config_sha256),
            bytes.fromhex(source_sha256),
            b"\0" * 4,
        )
        + SYMBOL.pack(1, 1, b"AAPL    ", 100)
    )


def _manifest(
    *,
    replay_id: str,
    status: str,
    replay_config: ReplayConfig,
    executable_sha256: str,
    identity_sha256: str,
    event_sha256: str,
    event_size: int,
    event_count: int,
    snapshot_sha256: str,
    snapshot_size: int,
    snapshot_count: int,
    symbols: list[tuple[int, int, str, int]],
) -> dict[str, Any]:
    hashes = config_hashes(replay_config)
    depth = replay_config.output.depth
    return {
        "artefacts": [
            {
                "kind": "events",
                "path": "events.ilb",
                "record_count": event_count,
                "record_size": 72,
                "schema_version": 1,
                "sha256": event_sha256,
                "size_bytes": event_size,
            },
            {
                "depth": depth,
                "kind": "snapshots",
                "path": "snapshots.ilb",
                "record_count": snapshot_count,
                "record_size": 48 + 28 * depth,
                "schema_version": 1,
                "sha256": snapshot_sha256,
                "size_bytes": snapshot_size,
            },
        ],
        "build": {
            "application_version": "0.1.0",
            "build_type": "Debug",
            "compiler": "Synthetic",
            "compiler_version": "1",
            "target": "test",
        },
        "code_revision": "unknown+dirty",
        "completed_at": "2026-08-07T12:00:01.000000000Z",
        "config": config_document(replay_config),
        "config_sha256": hashes.config_sha256,
        "counts": {
            "all_by_type": {"A": event_count},
            "decoded_messages": event_count,
            "directory_messages": len(symbols),
            "errors_observed": 1 if status == "degraded" else 0,
            "filtered_instrument_messages": 0,
            "global_system_messages": 0,
            "messages_processed": event_count,
            "selected_by_type": {"A": event_count},
            "selected_events": event_count,
            "selected_instrument_messages": event_count,
            "skipped_messages": 1 if status == "degraded" else 0,
            "snapshots_written": snapshot_count,
        },
        "error_summary": {"ERR_UNKNOWN_MESSAGE": 1} if status == "degraded" else {},
        "executable_sha256": executable_sha256,
        "global_session_events": [],
        "identity_config_sha256": hashes.identity_config_sha256,
        "identity_sha256": identity_sha256,
        "instruments": [
            {
                "final_book_digest": hashlib.sha256(symbol.encode()).hexdigest(),
                "final_order_count": 0,
                "final_trading_state": "closed",
                "financial_status": "N",
                "market_category": "Q",
                "round_lot_size": round_lot_size,
                "round_lots_only": False,
                "stock_locate": stock_locate,
                "symbol": symbol,
                "symbol_id": symbol_id,
            }
            for symbol_id, stock_locate, symbol, round_lot_size in symbols
        ],
        "publishable": False,
        "replay_id": replay_id,
        "schema_version": 1,
        "source": {
            "canonical_name": "synthetic.itch",
            "compression": "none",
            "exchange_timezone": "America/New_York",
            "framing": "itch-length-v1",
            "sha256": replay_config.input.sha256,
            "size_bytes": 16,
            "trading_date": replay_config.input.trading_date,
        },
        "started_at": "2026-08-07T12:00:00.000000000Z",
        "status": status,
    }


@pytest.fixture
def replay_factory(tmp_path: Path) -> Callable[..., Path]:
    """Create a strict completed replay directory independently from production conversion."""

    counter = 0

    def create(
        *,
        degraded: bool = False,
        trading_date: str = "2019-01-30",
        first_symbol: str = "AAPL",
        large_event_count: int | None = None,
    ) -> Path:
        nonlocal counter
        counter += 1
        source_sha256 = hashlib.sha256(f"source-{counter}".encode()).hexdigest()
        depth = 1 if large_event_count is not None else 2
        symbols = [first_symbol] if large_event_count is None else ["AAPL"]
        if large_event_count is None:
            symbols.append("MSFT.X")
        config_value = {
            "schema_version": 1,
            "input": {
                "path": "synthetic.itch",
                "sha256": source_sha256,
                "trading_date": trading_date,
                "exchange_timezone": "America/New_York",
            },
            "selection": {
                "symbols": symbols,
                "session_start_ns": 0,
                "session_end_ns": 86_400_000_000_000,
                "require_trading_state": False,
            },
            "output": {"depth": depth, "emit_unchanged_trade_snapshots": False},
            "validation": {
                "mode": "permissive" if degraded else "strict",
                "max_skipped_messages": 1 if degraded else 0,
                "invariant_interval": 1,
            },
        }
        replay_config = parse_config(json.dumps(config_value), "replay")
        assert isinstance(replay_config, ReplayConfig)
        hashes = config_hashes(replay_config)
        executable_sha256 = hashlib.sha256(b"synthetic executable").hexdigest()
        identity_sha256 = _stage_identity(
            source_sha256,
            hashes.identity_config_sha256,
            executable_sha256,
        )
        replay_id = f"20260807T1200{counter:02d}.000000000Z-{identity_sha256[:12]}"
        run = tmp_path / "parents" / replay_id
        run.mkdir(parents=True)
        events = run / "events.ilb"
        snapshots = run / "snapshots.ilb"
        encoded_date = int(trading_date.replace("-", ""))

        if large_event_count is None:
            events.write_bytes(
                _patch_golden(
                    GOLDEN_ROOT / "synthetic_events_v1.ilb",
                    config_sha256=hashes.config_sha256,
                    source_sha256=source_sha256,
                    degraded=degraded,
                    trading_date=encoded_date,
                    first_symbol=first_symbol,
                )
            )
            snapshots.write_bytes(
                _patch_golden(
                    GOLDEN_ROOT / "synthetic_snapshots_v1.ilb",
                    config_sha256=hashes.config_sha256,
                    source_sha256=source_sha256,
                    degraded=degraded,
                    trading_date=encoded_date,
                    first_symbol=first_symbol,
                )
            )
            event_count = 10
            snapshot_count = 2
            dictionary = [(1, 0x1234, first_symbol, 100), (2, 0xABCD, "MSFT.X", 200)]
        else:
            _large_event_file(
                events,
                config_sha256=hashes.config_sha256,
                source_sha256=source_sha256,
                trading_date=encoded_date,
                record_count=large_event_count,
            )
            _empty_snapshot_file(
                snapshots,
                config_sha256=hashes.config_sha256,
                source_sha256=source_sha256,
                trading_date=encoded_date,
            )
            event_count = large_event_count
            snapshot_count = 0
            dictionary = [(1, 1, "AAPL", 100)]

        manifest = _manifest(
            replay_id=replay_id,
            status="degraded" if degraded else "completed",
            replay_config=replay_config,
            executable_sha256=executable_sha256,
            identity_sha256=identity_sha256,
            event_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
            event_size=events.stat().st_size,
            event_count=event_count,
            snapshot_sha256=hashlib.sha256(snapshots.read_bytes()).hexdigest(),
            snapshot_size=snapshots.stat().st_size,
            snapshot_count=snapshot_count,
            symbols=dictionary,
        )
        manifest_path = run / "replay-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest_path

    return create
