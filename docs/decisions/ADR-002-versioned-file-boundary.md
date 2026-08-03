# ADR-002 — Versioned file boundary between C++ and Python

## Status

Accepted for MVP.

## Context

The C++ replay core must hand potentially millions of selected events and snapshots to Python. The boundary must be efficient, deterministic, strongly typed and independently testable. The first implementation should not acquire a large Arrow C++ dependency or invoke Python once per market event.

## Decision

Write two explicit version-1 binary interchange files:

- events.ilb for normalised selected-symbol lifecycle events.
- snapshots.ilb for changed top-N state and configured trade observations.

Both use:

- A fixed documented header and symbol dictionary.
- Explicit little-endian field encoding.
- Fixed-width records with validity flags.
- Schema version, source/config hashes and record count.
- Partial-file plus atomic-publication workflow.

Python validates and reads records in chunks, then converts them to partitioned Parquet. Direct host-struct dumps and executable serialisation are prohibited.

## Alternatives considered

- CSV: large, slow and weakly typed.
- JSON Lines: inspectable but too verbose for the primary boundary.
- Apache Arrow/Parquet directly in C++: good format but heavy initial build/dependency surface.
- pybind11 callbacks: excessive per-event overhead and coupled process ownership.
- Custom socket/network API: unnecessary complexity and failure modes.

## Consequences

Positive:

- Fast sequential writes/reads and deterministic byte contracts.
- Small C++ dependency surface.
- Cross-language golden contract testing.
- Parquet remains the flexible research representation.

Negative:

- The project owns a binary schema and migration burden.
- Diagnostic inspection requires a tool.
- Snapshot depth changes record size and requires header validation.

## Conditions that justify revisiting

- Arrow C++ materially simplifies the implementation after the core is stable.
- Conversion time/disk duplication dominates measured workflow cost.
- A batched zero-copy binding has a concrete non-exploratory consumer.
- Version-maintenance cost exceeds the dependency cost of a standard format.
