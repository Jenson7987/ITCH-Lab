# ADR-003 — Deterministic single-threaded replay and correctness-first book

## Status

Accepted for MVP.

## Context

ITCH state depends on strict source order. Parallel processing and highly specialised containers can improve throughput but create ordering, ownership and debugging complexity before a correct baseline exists.

## Decision

- Replay one source file on one logical thread in message order.
- Process different trading days as independent processes when parallelism is useful.
- Represent live orders with an unordered reference map.
- Represent bid/ask prices with ordered maps.
- Represent price-time priority with per-level FIFO lists and store stable iterators in order records.
- Maintain aggregated quantity incrementally.
- Provide deterministic state digests and invariant scans.
- Profile before replacing containers or adding internal parallelism.

## Alternatives considered

- Per-symbol worker threads: filtering is easy but ordered output/back-pressure and global state complicate determinism.
- Flat price arrays: fast for bounded known tick ranges but less general and requires careful price-range allocation.
- Vectors per level: compact but arbitrary cancellation/removal becomes linear.
- Intrusive/custom allocators initially: potentially faster but increases correctness and memory-safety risk.
- Reconstruct only level 2: insufficient for queue-aware research and weaker technical scope.

## Consequences

Positive:

- Clear mutation semantics and easier invariant testing.
- Deterministic source/output ordering.
- Correctness baseline for later profiling.
- Exact known visible orders are available for queue modelling.

Negative:

- std::map/list allocation and cache behaviour may limit throughput.
- Single-source replay cannot use all cores directly.
- Per-order iterator ownership requires careful move/erase tests.

## Conditions that justify revisiting

- Release profiling shows book containers, not decompression/I/O, dominate runtime.
- NFR-003 cannot be met after simpler allocation/reservation improvements.
- Alternative representation passes identical golden/property tests and state digests.
- An ADR records benchmark methodology, memory trade-offs and migration.
