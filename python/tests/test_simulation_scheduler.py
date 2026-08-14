"""TASK-022 deterministic integer-nanosecond latency scheduler tests."""

from __future__ import annotations

import pytest

from itchlab_research.errors import ErrorCode
from itchlab_research.simulation import (
    MAX_LATENCY_NS,
    MAX_TIMESTAMP_NS,
    LatencyScheduler,
    ScheduledAction,
    ScheduledActionKind,
    SimulationError,
)


def _schedule(
    scheduler: LatencyScheduler,
    kind: ScheduledActionKind,
    *,
    order_id: int,
    requested: int,
    message_index: int,
    latency: int,
) -> ScheduledAction:
    return scheduler.schedule(
        kind,
        simulated_order_id=order_id,
        requested_timestamp_ns=requested,
        request_message_index=message_index,
        latency_ns=latency,
    )


def test_ut_sim_001_equal_timestamp_market_messages_precede_actions() -> None:
    scheduler = LatencyScheduler()
    activation = _schedule(
        scheduler,
        ScheduledActionKind.ACTIVATE,
        order_id=1,
        requested=100,
        message_index=10,
        latency=10,
    )
    cancellation = _schedule(
        scheduler,
        ScheduledActionKind.CANCEL,
        order_id=1,
        requested=105,
        message_index=11,
        latency=5,
    )

    assert scheduler.actions_before_market(110, 12) == ()
    assert scheduler.actions_before_market(110, 13) == ()
    assert scheduler.actions_after_market_timestamp(110) == (activation, cancellation)
    assert scheduler.pending_actions == ()


def test_task_022_actions_between_market_messages_apply_before_the_later_event() -> None:
    scheduler = LatencyScheduler()
    action = _schedule(
        scheduler,
        ScheduledActionKind.ACTIVATE,
        order_id=4,
        requested=100,
        message_index=5,
        latency=15,
    )

    assert scheduler.actions_before_market(100, 5) == ()
    assert scheduler.actions_before_market(120, 6) == (action,)
    assert scheduler.current_market_key == (120, 6)
    assert not scheduler.current_market_timestamp_completed


def test_task_022_equal_effective_actions_retain_request_sequence() -> None:
    scheduler = LatencyScheduler()
    expected = tuple(
        _schedule(
            scheduler,
            ScheduledActionKind.ACTIVATE if index % 2 == 0 else ScheduledActionKind.CANCEL,
            order_id=index,
            requested=100 + index,
            message_index=10 + index,
            latency=10 - index,
        )
        for index in range(6)
    )

    assert scheduler.actions_before_market(110, 30) == ()
    assert scheduler.actions_after_market_timestamp(110) == expected
    assert [action.sequence for action in expected] == list(range(6))


@pytest.mark.parametrize("latency", [-1, True, MAX_LATENCY_NS + 1])
def test_ut_sim_001_invalid_latency_fails_without_scheduling(latency: int) -> None:
    scheduler = LatencyScheduler()

    with pytest.raises(SimulationError) as captured:
        _schedule(
            scheduler,
            ScheduledActionKind.ACTIVATE,
            order_id=1,
            requested=100,
            message_index=1,
            latency=latency,
        )

    assert captured.value.code is ErrorCode.LATENCY
    assert scheduler.pending_actions == ()


def test_task_022_latency_addition_is_checked_at_day_boundary() -> None:
    assert LatencyScheduler.effective_timestamp(MAX_TIMESTAMP_NS, 0) == MAX_TIMESTAMP_NS

    with pytest.raises(SimulationError) as captured:
        LatencyScheduler.effective_timestamp(MAX_TIMESTAMP_NS, 1)

    assert captured.value.code is ErrorCode.LATENCY


@pytest.mark.parametrize(
    ("timestamp", "message_index", "expected_code"),
    [
        (99, 11, ErrorCode.TIMESTAMP),
        (100, 10, ErrorCode.SIMULATION_ANOMALY),
        (100, 9, ErrorCode.SIMULATION_ANOMALY),
    ],
)
def test_task_022_market_keys_must_remain_source_ordered(
    timestamp: int, message_index: int, expected_code: ErrorCode
) -> None:
    scheduler = LatencyScheduler()
    assert scheduler.actions_before_market(100, 10) == ()

    with pytest.raises(SimulationError) as captured:
        scheduler.actions_before_market(timestamp, message_index)

    assert captured.value.code is expected_code


def test_task_022_completed_timestamp_cannot_receive_more_events_or_actions() -> None:
    scheduler = LatencyScheduler()
    assert scheduler.actions_before_market(100, 1) == ()
    assert scheduler.actions_after_market_timestamp(100) == ()

    with pytest.raises(SimulationError) as captured:
        scheduler.actions_before_market(100, 2)
    assert captured.value.code is ErrorCode.TIMESTAMP

    with pytest.raises(SimulationError) as captured:
        _schedule(
            scheduler,
            ScheduledActionKind.ACTIVATE,
            order_id=1,
            requested=100,
            message_index=2,
            latency=0,
        )
    assert captured.value.code is ErrorCode.TIMESTAMP
