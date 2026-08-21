"""TASK-018 causal feature catalogue and hand-calculated feature tests."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from itchlab_research.config import FeatureConfig
from itchlab_research.conversion import event_schema, snapshot_schema
from itchlab_research.datasets import (
    FeatureComputationError,
    FeaturePartitionContext,
    build_feature_batches,
    feature_catalogue,
    feature_catalogue_document,
    feature_schema,
)
from itchlab_research.errors import ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = REPOSITORY_ROOT / "tests" / "golden" / "features"
TRADING_DATE = date(2019, 1, 30)
CONFIG = FeatureConfig(
    depth_levels=(1, 5, 10),
    event_windows=(20, 100, 500),
    clock_windows_ns=(100_000_000, 1_000_000_000),
)
CONTEXT = FeaturePartitionContext(
    trading_date=TRADING_DATE,
    symbol="AAPL",
    symbol_id=1,
    tick_size4=100,
    session_start_ns=0,
    session_end_ns=10_000_000_000,
)


def _event(
    message_index: int,
    timestamp_ns: int,
    *,
    kind: str = "add",
    side: int | None = 1,
    quantity: int | None = 100,
    primary_reference: int | None = None,
    secondary_reference: int | None = None,
) -> dict[str, Any]:
    source_types = {
        "add": "A",
        "execute": "E",
        "execute_price": "C",
        "cancel": "X",
        "delete": "D",
        "replace": "U",
        "trade": "P",
        "cross": "Q",
        "broken_trade": "B",
        "trading_state": "H",
    }
    if primary_reference is None and kind not in {"cross", "trading_state"}:
        primary_reference = message_index + 10_000
    if kind in {"execute", "execute_price"} and secondary_reference is None:
        secondary_reference = message_index + 20_000
    return {
        "trading_date": TRADING_DATE,
        "symbol": "AAPL",
        "message_index": message_index,
        "timestamp_ns": timestamp_ns,
        "symbol_id": 1,
        "event_kind": kind,
        "source_type": source_types[kind],
        "primary_reference": primary_reference,
        "secondary_reference": secondary_reference,
        "side": side,
        "price4": 10_000 if side is not None else None,
        "quantity": quantity,
        "remaining_quantity": quantity,
        "execution_price4": 10_000 if kind == "execute_price" else None,
        "aux_code": None,
        "event_subtype": None,
        "in_session": True,
        "flags": 0,
    }


def _snapshot(
    message_index: int,
    timestamp_ns: int,
    *,
    kind: str = "add",
    bid_price4: int = 10_000,
    ask_price4: int = 10_200,
    bid_quantity: int = 100,
    ask_quantity: int = 100,
    depth: int = 10,
    top_n_changed: bool = True,
    trading_state: str = "trading",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trading_date": TRADING_DATE,
        "symbol": "AAPL",
        "message_index": message_index,
        "timestamp_ns": timestamp_ns,
        "symbol_id": 1,
        "event_kind": kind,
        "event_price4": bid_price4,
        "event_quantity": bid_quantity,
        "last_trade_price4": None,
        "last_trade_quantity": None,
        "top_n_changed": top_n_changed,
        "trading_state": trading_state,
        "flags": 0,
    }
    for level in range(1, depth + 1):
        row[f"bid_price4_{level}"] = max(0, bid_price4 - (level - 1) * 100)
        row[f"bid_quantity_{level}"] = bid_quantity if level == 1 else 0 + 1
        row[f"ask_price4_{level}"] = ask_price4 + (level - 1) * 100
        row[f"ask_quantity_{level}"] = ask_quantity if level == 1 else 0 + 1
    return row


def _batch(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist(rows, schema=schema)


def _features(
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    context: FeaturePartitionContext = CONTEXT,
    depth: int = 10,
) -> pa.Table:
    batches = list(
        build_feature_batches(
            [_batch(events, event_schema())],
            [_batch(snapshots, snapshot_schema(depth))],
            CONFIG,
            context,
        )
    )
    return pa.Table.from_batches(batches, schema=feature_schema(CONFIG))


def test_task_018_feature_catalogue_matches_independent_golden_and_schema() -> None:
    golden = json.loads((GOLDEN_ROOT / "feature-catalogue-v1.json").read_text(encoding="utf-8"))
    catalogue = feature_catalogue(CONFIG)
    schema = feature_schema(CONFIG)

    assert feature_catalogue_document(CONFIG)["schema_version"] == golden["schema_version"]
    assert [item.name for item in catalogue] == golden["feature_names"]
    assert [item.name for item in catalogue if item.dtype == "int8"] == golden["int8_features"]
    assert [item.name for item in catalogue if not item.nullable] == golden["non_nullable_features"]
    assert [field.name for field in schema][7:] == golden["feature_names"]
    assert schema.field("aggressor_sign").type == pa.int8()
    assert all(
        schema.field(item.name).type == (pa.int8() if item.dtype == "int8" else pa.float64())
        and item.formula
        and item.unit
        and item.null_policy
        and item.owner == "itchlab_research.datasets.features"
        for item in catalogue
    )
    assert next(item for item in catalogue if item.name == "ofi_500").lookback_value == 500
    assert next(item for item in catalogue if item.name == "add_bid_rate_1s").lookback_kind == (
        "clock_ns"
    )
    with pytest.raises(FeatureComputationError) as captured:
        feature_catalogue(
            FeatureConfig(
                depth_levels=(True, 5, 10),  # type: ignore[arg-type]
                event_windows=CONFIG.event_windows,
                clock_windows_ns=CONFIG.clock_windows_ns,
            )
        )
    assert captured.value.code is ErrorCode.CONFIG_SCHEMA


def test_task_018_hand_calculated_current_depth_clock_and_warmup_features() -> None:
    event = _event(10, 1_000_000_000)
    snapshot = _snapshot(10, 1_000_000_000, bid_quantity=100, ask_quantity=300)
    for level in range(2, 6):
        snapshot[f"bid_quantity_{level}"] = 50
        snapshot[f"ask_quantity_{level}"] = 25
    for level in range(6, 11):
        snapshot[f"bid_quantity_{level}"] = 20
        snapshot[f"ask_quantity_{level}"] = 40

    context = FeaturePartitionContext(TRADING_DATE, "AAPL", 1, 100, 0, 2_000_000_000)
    actual = _features([event], [snapshot], context=context).to_pylist()[0]
    expected = json.loads((GOLDEN_ROOT / "hand-calculated-v1.json").read_text(encoding="utf-8"))[
        "row"
    ]

    for name, value in expected.items():
        if isinstance(value, float):
            assert actual[name] == pytest.approx(value)
        else:
            assert actual[name] == value
    assert actual["ofi_normalised_20"] is None
    assert actual["ofi_normalised_100"] is None
    assert actual["ofi_normalised_500"] is None


def _constant_price_sequence(count: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for ordinal in range(count):
        message_index = ordinal * 10 + 10
        timestamp_ns = ordinal * 10_000_000
        events.append(_event(message_index, timestamp_ns))
        snapshots.append(
            _snapshot(
                message_index,
                timestamp_ns,
                bid_quantity=100 + ordinal,
                ask_quantity=100,
            )
        )
    return events, snapshots


def test_task_018_required_ofi_windows_rates_and_history_boundary_are_exact() -> None:
    events, snapshots = _constant_price_sequence(501)
    rows = _features(events, snapshots).to_pylist()

    assert rows[19]["ofi_20"] is None
    assert rows[20]["ofi_20"] == 20.0
    assert rows[20]["ofi_normalised_20"] == pytest.approx(20 / 4_210)
    assert rows[99]["ofi_100"] is None
    assert rows[100]["ofi_100"] == 100.0
    assert rows[499]["ofi_500"] is None
    assert rows[499]["history_complete"] is False
    assert rows[500]["ofi_20"] == 20.0
    assert rows[500]["ofi_normalised_20"] == pytest.approx(20 / 13_810)
    assert rows[500]["ofi_100"] == 100.0
    assert rows[500]["ofi_500"] == 500.0
    assert rows[500]["realised_volatility_500"] == 0.0
    assert rows[500]["history_complete"] is True
    assert rows[0]["add_bid_rate_100ms"] is None
    assert rows[10]["add_bid_rate_100ms"] == 100.0
    assert rows[100]["add_bid_rate_1s"] == 100.0
    assert rows[500]["add_bid_rate_100ms"] == 100.0
    assert rows[500]["add_bid_rate_1s"] == 100.0
    assert rows[500]["session_progress"] == 0.5


def test_task_018_realised_volatility_uses_only_trailing_returns() -> None:
    events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for ordinal in range(501):
        message_index = ordinal * 10 + 10
        timestamp_ns = ordinal * 10_000_000
        events.append(_event(message_index, timestamp_ns))
        bid, ask = (9_900, 10_100) if ordinal % 2 == 0 else (10_000, 10_200)
        snapshots.append(_snapshot(message_index, timestamp_ns, bid_price4=bid, ask_price4=ask))

    row = _features(events, snapshots).to_pylist()[500]
    absolute_return = abs(math.log(20_200 / 20_000))

    for window in CONFIG.event_windows:
        assert row[f"realised_volatility_{window}"] == pytest.approx(
            math.sqrt(window) * absolute_return
        )


def test_task_018_observable_flow_and_broken_trade_are_causal() -> None:
    events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for ordinal in range(22):
        message_index = ordinal * 10 + 10
        timestamp_ns = 1_000_000_000 + ordinal * 10_000_000
        if ordinal == 5:
            event = _event(
                message_index,
                timestamp_ns,
                kind="execute",
                side=1,
                quantity=10,
                secondary_reference=501,
            )
        elif ordinal == 6:
            event = _event(
                message_index,
                timestamp_ns,
                kind="execute_price",
                side=-1,
                quantity=30,
                secondary_reference=501,
            )
        else:
            event = _event(message_index, timestamp_ns)
        events.append(event)
        snapshots.append(_snapshot(message_index, timestamp_ns, kind=event["event_kind"]))
    events.append(
        _event(
            215,
            1_205_000_000,
            kind="broken_trade",
            side=None,
            quantity=None,
            primary_reference=501,
        )
    )
    events.append(
        _event(
            216,
            1_206_000_000,
            kind="trade",
            side=1,
            quantity=999,
            secondary_reference=999,
        )
    )
    events.sort(key=lambda row: row["message_index"])

    rows = _features(events, snapshots).to_pylist()

    assert rows[5]["aggressor_sign"] == -1
    assert rows[6]["aggressor_sign"] == 1
    assert rows[7]["aggressor_sign"] is None
    assert rows[20]["execution_imbalance_20"] == pytest.approx(0.5)
    assert rows[21]["execution_imbalance_20"] == pytest.approx(0.0)
    assert rows[20]["execution_bid_rate_1s"] == 1.0
    assert rows[20]["execution_ask_rate_1s"] == 1.0
    assert rows[21]["execution_bid_rate_1s"] == 1.0
    assert rows[21]["execution_ask_rate_1s"] == 1.0


def test_task_018_clock_rates_cover_categories_sides_and_same_timestamp_order() -> None:
    events = [
        _event(10, 950_000_000, kind="cancel", side=1),
        _event(20, 960_000_000, kind="delete", side=-1),
        _event(30, 970_000_000, kind="execute", side=1, secondary_reference=700),
        _event(40, 1_000_000_000),
        _event(50, 1_000_000_000, kind="execute", side=-1, secondary_reference=701),
    ]
    snapshots = [_snapshot(40, 1_000_000_000)]

    row = _features(events, snapshots).to_pylist()[0]

    assert row["add_bid_rate_100ms"] == 10.0
    assert row["cancel_delete_bid_rate_100ms"] == 10.0
    assert row["cancel_delete_ask_rate_100ms"] == 10.0
    assert row["execution_bid_rate_100ms"] == 10.0
    assert row["execution_ask_rate_100ms"] == 0.0
    assert row["execution_bid_rate_1s"] == 1.0
    assert row["execution_ask_rate_1s"] == 0.0


def test_ut_feat_001_future_mutation_cannot_change_previous_feature_rows() -> None:
    events, snapshots = _constant_price_sequence(30)
    baseline = _features(events, snapshots)
    mutated_events = [dict(row) for row in events]
    mutated_snapshots = [dict(row) for row in snapshots]
    mutated_events[25]["side"] = -1
    mutated_snapshots[25]["bid_quantity_1"] = 10_000
    mutated_snapshots[25]["ask_quantity_1"] = 1

    mutated = _features(mutated_events, mutated_snapshots)

    assert baseline.slice(0, 25).equals(mutated.slice(0, 25))
    assert not baseline.slice(25).equals(mutated.slice(25))


def test_task_018_nonqualifying_rows_do_not_advance_feature_windows() -> None:
    events, snapshots = _constant_price_sequence(22)
    snapshots[1]["top_n_changed"] = False
    snapshots[1]["trading_state"] = "halted"

    rows = _features(events, snapshots).to_pylist()

    assert len(rows) == 21
    assert [row["qualifying_ordinal"] for row in rows] == list(range(21))
    assert rows[19]["ofi_20"] is None
    assert rows[20]["ofi_20"] is not None


@pytest.mark.parametrize(
    ("events", "snapshots", "context", "depth", "expected_code"),
    [
        (
            [_event(20, 20), _event(10, 10)],
            [_snapshot(20, 20)],
            CONTEXT,
            10,
            ErrorCode.INVARIANT,
        ),
        (
            [_event(10, 10)],
            [_snapshot(10, 10, depth=5)],
            CONTEXT,
            5,
            ErrorCode.DEPTH,
        ),
        (
            [_event(10, 10)],
            [_snapshot(10, 10)],
            FeaturePartitionContext(TRADING_DATE, "AAPL", 1, 0, 0, 1_000),
            10,
            ErrorCode.PRICE,
        ),
        (
            [_event(10, 10)],
            [_snapshot(10, 10, bid_price4=0, ask_price4=0)],
            CONTEXT,
            10,
            ErrorCode.PRICE,
        ),
    ],
)
def test_task_018_invalid_order_depth_context_and_price_fail_stably(
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    context: FeaturePartitionContext,
    depth: int,
    expected_code: ErrorCode,
) -> None:
    with pytest.raises(FeatureComputationError) as captured:
        _features(events, snapshots, context=context, depth=depth)

    assert captured.value.code is expected_code


def test_task_018_rejects_unexpected_arrow_schema() -> None:
    event = _batch([_event(10, 10)], event_schema())
    malformed = _batch([_snapshot(10, 10)], snapshot_schema(10)).drop_columns(["flags"])

    with pytest.raises(FeatureComputationError) as captured:
        list(build_feature_batches([event], [malformed], CONFIG, CONTEXT))

    assert captured.value.code is ErrorCode.SCHEMA_VERSION

    malformed_depth = _snapshot(10, 10)
    malformed_depth["bid_quantity_5"] = None
    with pytest.raises(FeatureComputationError) as captured:
        _features([_event(10, 10)], [malformed_depth])

    assert captured.value.code is ErrorCode.INVARIANT
