"""Safe typed readers for the versioned C++ interchange boundary."""

from itchlab_research.errors import InterchangeReadError
from itchlab_research.interchange.models import (
    EventBatch,
    EventKind,
    EventRecord,
    InterchangeKind,
    InterchangeMetadata,
    SnapshotBatch,
    SnapshotDepthLevel,
    SnapshotRecord,
    SymbolEntry,
    TradingState,
)
from itchlab_research.interchange.readers import read_events, read_snapshots

__all__ = [
    "EventBatch",
    "EventKind",
    "EventRecord",
    "InterchangeKind",
    "InterchangeMetadata",
    "InterchangeReadError",
    "SnapshotBatch",
    "SnapshotDepthLevel",
    "SnapshotRecord",
    "SymbolEntry",
    "TradingState",
    "read_events",
    "read_snapshots",
]
