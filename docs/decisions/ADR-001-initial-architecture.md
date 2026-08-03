# ADR-001 — Initial local pipeline architecture

## Status

Accepted for MVP.

## Context

ITCH-Lab must demonstrate low-level market-data engineering, quantitative research and reproducibility without becoming a hosted product. Raw source days are large, ordered and binary. The project is expected to be implementable by one developer on an M2 Pro MacBook and testable in Linux CI using synthetic data.

## Decision

Use an offline, local, file-oriented pipeline:

- C++20 single-process core for streaming input, ITCH decoding, selected-symbol level-3 reconstruction and validated interchange output.
- Python 3.11+ package for conversion, causal datasets, baseline models, execution simulation analysis and reporting.
- Immutable run directories and manifests connect stages.
- No database, server, accounts, cloud infrastructure, live feed or broker connection in the MVP.

## Alternatives considered

- Python-only: simpler initially but gives weaker systems/performance evidence.
- C++ only: possible but makes iterative research/reporting unnecessarily slow.
- Local database: adds ingestion/schema operations without multi-user query need.
- Web service/frontend: adds security/deployment work without improving the core interview signal.
- Live trading stack: materially changes operational, legal and safety scope.

## Consequences

Positive:

- Clear separation of performance and research concerns.
- Each stage can be tested/reproduced independently.
- Runtime has no network attack surface.
- Scope remains feasible for one developer.

Negative:

- Cross-language schemas require explicit maintenance.
- Intermediate files use disk space.
- A local reviewer must obtain authorised source data separately.
- No interactive/live demonstration beyond CLI and reports.

## Conditions that justify revisiting

- A proven reviewer/user need for remote interactive access.
- File interchange becomes the dominant measured bottleneck.
- A live receiver is separately authorised and accompanied by a new threat model.
- Multi-run querying cannot be handled reasonably by Parquet scans.
