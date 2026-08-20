"""TASK-029 deterministic performance-fixture recipe tests."""

from __future__ import annotations

import hashlib

from tests.fixtures.generate_performance import render_fixture


def test_task_029_performance_fixture_is_deterministic_and_self_describing() -> None:
    first_plain, first_gzip, first_metadata = render_fixture(cycles=3)
    second_plain, second_gzip, second_metadata = render_fixture(cycles=3)

    assert first_plain == second_plain
    assert first_gzip == second_gzip
    assert first_metadata == second_metadata
    assert first_metadata["message_count"] == 33
    assert first_metadata["maximum_live_selected_orders"] == 2
    assert first_metadata["plain"] == {
        "path": "data/fixtures/performance.itch",
        "sha256": hashlib.sha256(first_plain).hexdigest(),
        "size_bytes": len(first_plain),
    }
    assert first_metadata["gzip"] == {
        "path": "data/fixtures/performance.itch.gz",
        "sha256": hashlib.sha256(first_gzip).hexdigest(),
        "size_bytes": len(first_gzip),
    }
