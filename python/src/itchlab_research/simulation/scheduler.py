"""Deterministic integer-nanosecond scheduling around ordered market messages."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import StrEnum

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.order import (
    MAX_LATENCY_NS,
    MAX_TIMESTAMP_NS,
    MAX_UINT64,
)


class ScheduledActionKind(StrEnum):
    """Action types whose effects are delayed by the execution configuration."""

    ACTIVATE = "activate"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ScheduledAction:
    """One deterministic latency-delayed order action."""

    effective_timestamp_ns: int
    sequence: int
    simulated_order_id: int
    kind: ScheduledActionKind
    requested_timestamp_ns: int
    request_message_index: int


def _fail(
    code: ErrorCode,
    message: str,
    *,
    simulated_order_id: int | None = None,
    message_index: int | None = None,
) -> SimulationError:
    return SimulationError(
        code,
        message,
        simulated_order_id=simulated_order_id,
        message_index=message_index,
    )


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


class LatencyScheduler:
    """Schedule actions after latency while preserving the market-first timestamp tie-break."""

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, ScheduledAction]] = []
        self._next_sequence = 0
        self._last_market_timestamp_ns: int | None = None
        self._last_market_message_index: int | None = None
        self._completed_market_timestamp_ns: int | None = None

    @staticmethod
    def effective_timestamp(requested_timestamp_ns: int, latency_ns: int) -> int:
        """Return the checked action timestamp under the version-1 latency bounds."""
        if not _valid_int(requested_timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(
                ErrorCode.TIMESTAMP, "Action request timestamp is outside the exchange day."
            )
        if not _valid_int(latency_ns, minimum=0, maximum=MAX_LATENCY_NS):
            raise _fail(ErrorCode.LATENCY, "Action latency is outside the version-1 bounds.")
        effective = requested_timestamp_ns + latency_ns
        if effective > MAX_TIMESTAMP_NS:
            raise _fail(ErrorCode.LATENCY, "Action latency extends beyond the exchange day.")
        return effective

    def schedule(
        self,
        kind: ScheduledActionKind,
        *,
        simulated_order_id: int,
        requested_timestamp_ns: int,
        request_message_index: int,
        latency_ns: int,
    ) -> ScheduledAction:
        """Schedule one validated action and return its immutable identity."""
        if not isinstance(kind, ScheduledActionKind):
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Scheduled action kind is invalid.",
                simulated_order_id=simulated_order_id,
            )
        if not _valid_int(simulated_order_id, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Scheduled order ID is invalid.")
        if not _valid_int(request_message_index, minimum=0, maximum=MAX_UINT64):
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Scheduled request message index is invalid.",
                simulated_order_id=simulated_order_id,
            )
        effective = self.effective_timestamp(requested_timestamp_ns, latency_ns)
        if (
            self._completed_market_timestamp_ns is not None
            and requested_timestamp_ns <= self._completed_market_timestamp_ns
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Action was requested after its market timestamp was completed.",
                simulated_order_id=simulated_order_id,
                message_index=request_message_index,
            )
        if self._next_sequence > MAX_UINT64:
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Scheduled action sequence overflowed.")
        action = ScheduledAction(
            effective_timestamp_ns=effective,
            sequence=self._next_sequence,
            simulated_order_id=simulated_order_id,
            kind=kind,
            requested_timestamp_ns=requested_timestamp_ns,
            request_message_index=request_message_index,
        )
        heapq.heappush(self._heap, (effective, self._next_sequence, action))
        self._next_sequence += 1
        return action

    def actions_before_market(
        self, timestamp_ns: int, message_index: int
    ) -> tuple[ScheduledAction, ...]:
        """Return actions strictly earlier than the next ordered market message."""
        self._validate_market_key(timestamp_ns, message_index)
        if (
            self._completed_market_timestamp_ns is not None
            and timestamp_ns <= self._completed_market_timestamp_ns
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Market message belongs to an already completed timestamp.",
                message_index=message_index,
            )
        if (
            self._last_market_timestamp_ns is not None
            and timestamp_ns > self._last_market_timestamp_ns
        ):
            self._completed_market_timestamp_ns = self._last_market_timestamp_ns
        actions = self._pop_due(timestamp_ns, inclusive=False)
        self._last_market_timestamp_ns = timestamp_ns
        self._last_market_message_index = message_index
        return actions

    def actions_after_market_timestamp(self, timestamp_ns: int) -> tuple[ScheduledAction, ...]:
        """Return equal-time actions after the caller completes that entire market-time group."""
        if not _valid_int(timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(ErrorCode.TIMESTAMP, "Completed market timestamp is invalid.")
        if self._last_market_timestamp_ns != timestamp_ns:
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Only the current market timestamp may be completed.",
            )
        if (
            self._completed_market_timestamp_ns is not None
            and timestamp_ns <= self._completed_market_timestamp_ns
        ):
            raise _fail(ErrorCode.TIMESTAMP, "Market timestamp was already completed.")
        actions = self._pop_due(timestamp_ns, inclusive=True)
        self._completed_market_timestamp_ns = timestamp_ns
        return actions

    @property
    def pending_actions(self) -> tuple[ScheduledAction, ...]:
        """Return a deterministic snapshot of actions that have not become effective."""
        return tuple(item[2] for item in sorted(self._heap))

    @property
    def current_market_key(self) -> tuple[int, int] | None:
        """Return the most recently opened market-message key, if any."""
        if self._last_market_timestamp_ns is None or self._last_market_message_index is None:
            return None
        return self._last_market_timestamp_ns, self._last_market_message_index

    @property
    def current_market_timestamp_completed(self) -> bool:
        """Return whether the current equal-timestamp market group has been closed."""
        return (
            self._last_market_timestamp_ns is not None
            and self._completed_market_timestamp_ns == self._last_market_timestamp_ns
        )

    def _validate_market_key(self, timestamp_ns: int, message_index: int) -> None:
        if not _valid_int(timestamp_ns, minimum=0, maximum=MAX_TIMESTAMP_NS):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Market timestamp is outside the exchange day.",
                message_index=message_index if isinstance(message_index, int) else None,
            )
        if not _valid_int(message_index, minimum=0, maximum=MAX_UINT64):
            raise _fail(ErrorCode.SIMULATION_ANOMALY, "Market message index is invalid.")
        if (
            self._last_market_timestamp_ns is not None
            and timestamp_ns < self._last_market_timestamp_ns
        ):
            raise _fail(
                ErrorCode.TIMESTAMP,
                "Market timestamps are decreasing.",
                message_index=message_index,
            )
        if (
            self._last_market_message_index is not None
            and message_index <= self._last_market_message_index
        ):
            raise _fail(
                ErrorCode.SIMULATION_ANOMALY,
                "Market message indices are not strictly increasing.",
                message_index=message_index,
            )

    def _pop_due(self, timestamp_ns: int, *, inclusive: bool) -> tuple[ScheduledAction, ...]:
        actions: list[ScheduledAction] = []
        while self._heap:
            effective = self._heap[0][0]
            if effective > timestamp_ns or (effective == timestamp_ns and not inclusive):
                break
            actions.append(heapq.heappop(self._heap)[2])
        return tuple(actions)


__all__ = ["LatencyScheduler", "ScheduledAction", "ScheduledActionKind"]
