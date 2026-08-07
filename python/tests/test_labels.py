"""TASK-019 bounded three-class future-label tests."""

from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa
import pytest

from itchlab_research.config import LabelConfig
from itchlab_research.conversion import snapshot_schema
from itchlab_research.datasets import (
    FeaturePartitionContext,
    LabelComputationError,
    build_label_batches,
    label_schema,
)
from itchlab_research.errors import ErrorCode

TRADING_DATE = date(2019, 1, 30)
CONFIG = LabelConfig(
    primary_event_horizon=2,
    secondary_event_horizons=(1, 3),
    flat_threshold_ticks=1,
)
CONTEXT = FeaturePartitionContext(
    trading_date=TRADING_DATE,
    symbol="AAPL",
    symbol_id=1,
    tick_size4=100,
    session_start_ns=0,
    session_end_ns=10_000,
)


def _snapshot(message_index: int, ordinal: int, mid2: int) -> dict[str, Any]:
    bid = mid2 // 2 - 100
    ask = mid2 - bid
    row: dict[str, Any] = {
        "trading_date": TRADING_DATE,
        "symbol": "AAPL",
        "message_index": message_index,
        "timestamp_ns": ordinal + 1,
        "symbol_id": 1,
        "event_kind": "add",
        "event_price4": bid,
        "event_quantity": 100,
        "last_trade_price4": None,
        "last_trade_quantity": None,
        "top_n_changed": True,
        "trading_state": "trading",
        "flags": 0,
    }
    for level in range(1, 11):
        row[f"bid_price4_{level}"] = bid - (level - 1) * 100
        row[f"bid_quantity_{level}"] = 100
        row[f"ask_price4_{level}"] = ask + (level - 1) * 100
        row[f"ask_quantity_{level}"] = 100
    return row


def _labels(rows: list[dict[str, Any]], *, batch_at: int | None = None) -> pa.Table:
    schema = snapshot_schema(10)
    if batch_at is None:
        batches = [pa.RecordBatch.from_pylist(rows, schema=schema)]
    else:
        batches = [
            pa.RecordBatch.from_pylist(rows[:batch_at], schema=schema),
            pa.RecordBatch.from_pylist(rows[batch_at:], schema=schema),
        ]
    return pa.Table.from_batches(
        list(build_label_batches(batches, CONFIG, CONTEXT)),
        schema=label_schema(CONFIG),
    )


def test_ut_label_001_hand_sequence_yields_down_flat_up_and_null_tails() -> None:
    rows = [
        _snapshot(10, 0, 20_000),
        _snapshot(20, 1, 20_200),
        _snapshot(30, 2, 20_000),
        _snapshot(40, 3, 20_401),
        _snapshot(50, 4, 19_800),
        _snapshot(60, 5, 20_000),
    ]

    actual = _labels(rows, batch_at=3).to_pylist()

    assert [row["qualifying_ordinal"] for row in actual] == list(range(6))
    assert [row["label_horizon_2"] for row in actual] == [0, 1, 0, -1, None, None]
    assert actual[0]["label_horizon_1"] == 0  # Exact +200 threshold is flat.
    assert actual[1]["label_horizon_3"] == -1
    assert [row["message_index"] for row in actual] == [10, 20, 30, 40, 50, 60]
    assert label_schema(CONFIG).field("label_horizon_2").type == pa.int8()


def test_task_019_nonqualifying_rows_do_not_advance_label_horizons() -> None:
    rows = [_snapshot(index * 10, index, 20_000 + index * 100) for index in range(6)]
    rows[1]["top_n_changed"] = False
    rows[1]["trading_state"] = "halted"

    actual = _labels(rows).to_pylist()

    assert len(actual) == 5
    assert [row["qualifying_ordinal"] for row in actual] == list(range(5))
    assert actual[-2]["label_horizon_2"] is None
    assert actual[-1]["label_horizon_1"] is None


def test_task_019_future_mutation_changes_only_labels_whose_target_reaches_it() -> None:
    rows = [_snapshot(index * 10, index, 20_000) for index in range(7)]
    baseline = _labels(rows)
    mutated = [dict(row) for row in rows]
    mutated[4] = _snapshot(40, 4, 21_000)
    changed = _labels(mutated)

    assert baseline.slice(0, 1).equals(changed.slice(0, 1))
    assert not baseline.slice(1, 4).equals(changed.slice(1, 4))
    assert baseline.slice(5).equals(changed.slice(5))


def test_task_019_label_input_order_and_schema_fail_stably() -> None:
    rows = [_snapshot(20, 0, 20_000), _snapshot(10, 1, 20_200)]
    with pytest.raises(LabelComputationError) as captured:
        _labels(rows)
    assert captured.value.code is ErrorCode.INVARIANT

    malformed = pa.RecordBatch.from_pylist(
        [_snapshot(10, 0, 20_000)], schema=snapshot_schema(10)
    ).drop_columns(["flags"])
    with pytest.raises(LabelComputationError) as captured:
        list(build_label_batches([malformed], CONFIG, CONTEXT))
    assert captured.value.code is ErrorCode.SCHEMA_VERSION


@pytest.mark.parametrize(
    "config",
    [
        LabelConfig(0, (1, 3), 0),
        LabelConfig(2, (3, 1), 0),
        LabelConfig(2, (2, 3), 0),
        LabelConfig(2, (1, 3), -1),
    ],
)
def test_task_019_invalid_label_configs_fail_with_horizon_code(config: LabelConfig) -> None:
    with pytest.raises(LabelComputationError) as captured:
        label_schema(config)
    assert captured.value.code is ErrorCode.HORIZON
